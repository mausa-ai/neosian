"""`[[chat.mcp]]`: the MCP servers `neosian chat` opens (NC8, DESIGN
§30.2): one table per server in `config.toml`, mirroring `McpServer`
(§25): `name`, then `command`/`args`/`env` or `url`/`headers`, an
optional `prefix`; literal values, the file being 0600. `chat_servers`
is the grammar tier (nothing spawned); `serving` opens every server for
a session's lifetime and adds its tools to the agent, refusing a name
chat already has unless the table sets `prefix`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from typing import Any, Final

from neosian._foundation.mcp.client import McpServer
from neosian._foundation.shared.constants import BuiltinTools
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.types import AgentConfig, ToolFunction
from neosian._foundation.tools.base import get_tool_metadata

# What the Conversation registers over `config.tools` (§9.6, §24):
# checked up front because `recall_turn` registers lazily, at the first
# compaction, where a collision would surface mid-session.
RESIDENT_TOOLS: Final = ("memory", "list_skills", "load_skill", "recall_turn")

_STDIO_KEYS: Final = frozenset({"command", "args", "env"})
_HTTP_KEYS: Final = frozenset({"url", "headers"})
_COLLISION: Final = (
    "MCP server '{server}' serves '{tool}', which chat already has: "
    "set prefix on its [[chat.mcp]] table"
)


def chat_servers(section: Mapping[str, Any]) -> tuple[McpServer, ...]:
    """The `[chat]` section's `mcp` tables as unconnected servers, the
    grammar tier: a malformed table is a `ValueError` naming it, and
    nothing is spawned."""
    tables = section.get("mcp", [])
    if not isinstance(tables, list):
        raise ValueError("[[chat.mcp]] must be a list of tables")
    servers: list[McpServer] = []
    seen: set[str] = set()
    for index, table in enumerate(tables, start=1):
        where = f"[[chat.mcp]] #{index}"
        if not isinstance(table, dict):
            raise ValueError(f"{where}: not a table")
        name = table.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{where}: name must be a non-empty string")
        where = f"[[chat.mcp]] {name}"
        if name in seen:
            raise ValueError(f"{where}: the name is taken by an earlier table")
        seen.add(name)
        servers.append(_server(table, name, where))
    return tuple(servers)


def _server(table: Mapping[str, Any], name: str, where: str) -> McpServer:
    command = _string(table, "command", where)
    url = _string(table, "url", where)
    prefix = _string(table, "prefix", where)
    if command is not None and url is not None:
        raise ValueError(f"{where}: command and url are two servers, not one")
    if command is not None:
        _only(table, _STDIO_KEYS, "command", where)
        args = table.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"{where}: args must be a list of strings")
        env = _strings(table, "env", where)
        return McpServer.stdio(command, args, env=env, name=name, prefix=prefix)
    if url is not None:
        _only(table, _HTTP_KEYS, "url", where)
        headers = _strings(table, "headers", where)
        return McpServer.http(url, headers=headers, name=name, prefix=prefix)
    raise ValueError(f"{where}: a command to spawn or a url to reach")


def _only(table: Mapping[str, Any], own: frozenset[str], kind: str, where: str) -> None:
    stray = sorted(set(table) - own - {"name", "prefix"})
    if stray:
        raise ValueError(f"{where}: {stray[0]!r} is not a key of a {kind} table")


def _string(table: Mapping[str, Any], key: str, where: str) -> str | None:
    value = table.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{where}: {key} must be a string")
    return value


def _strings(table: Mapping[str, Any], key: str, where: str) -> dict[str, str] | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(v, str) for v in value.values()
    ):
        raise ValueError(f"{where}: {key} must be a table of strings")
    return dict(value)


def _name(tool: ToolFunction) -> str | None:
    metadata = get_tool_metadata(tool)
    return None if metadata is None else str(metadata.name)


@asynccontextmanager
async def serving(
    servers: Sequence[McpServer], config: AgentConfig
) -> AsyncIterator[AgentConfig]:
    """Every server open for the body's lifetime, its tools added to
    `config`, the agent chat runs. Connecting raises
    `McpConnectionError`; a name chat already has (its own tools, the
    Conversation's, `update_todo` when on) is refused here, before the
    Agent's check would name the server with the wrong hint (a bridged
    tool registers first) or fire mid-session (`recall_turn`)."""
    if not servers:
        yield config
        return
    async with AsyncExitStack() as stack:
        for server in servers:
            await stack.enter_async_context(server)
        taken = {name for name in map(_name, config.tools) if name is not None}
        taken.update(RESIDENT_TOOLS)
        if config.enable_todo:
            taken.add(BuiltinTools.Todo.NAME)
        bridged: list[ToolFunction] = []
        for server in servers:
            for tool in server.tools:
                name = _name(tool)
                if name in taken:
                    raise ConfigurationError(
                        _COLLISION.format(server=server.name, tool=name)
                    )
                if name is not None:
                    taken.add(name)
                bridged.append(tool)
        yield replace(config, tools=[*config.tools, *bridged])
