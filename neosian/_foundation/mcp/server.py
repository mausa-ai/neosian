"""The MCP memory server factory: one tool, served verbatim.

`create_memory_server` builds the SDK's low-level `Server` around the
function tool's own `ToolDefinition` — name, description and JSON schema
are the bytes `create_memory_tool` authors, never re-derived from type
hints — and executes every call through `memory/dispatch.py`, so the
function tool, the native declaration and this server cannot drift
(ledger #50). Corrective failures ride MCP's in-band `is_error` — that
is `ToolResult`, expressed in MCP's vocabulary (ledger #52).

The caller owns the store's lifetime (the ledger #33 rule): the factory
and the SDK's per-connection lifespan never close it — the entry point
in `neosian/mcp/serve.py` does.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.mcp.sdk import Sdk, load_sdk
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.index import memory_system_section
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.tools.base import ToolResult, get_tool_definition

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp.server import Server, ServerRequestContext
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
    )

logger = logging.getLogger(__name__)

# Recorded on every version row an MCP client writes (DESIGN §20 grammar);
# `mcp install` renders the client's name (`mcp:claude-code`) via --actor.
DEFAULT_ACTOR: Final = "mcp:stdio"
_SERVER_NAME: Final = "neosian-memory"


def _to_call_tool_result(sdk: Sdk, result: ToolResult[Any]) -> CallToolResult:
    """`ToolResult` in MCP's vocabulary: text content, in-band `is_error`,
    the `system_reminder` as a second content block (ledger #52)."""
    text = result.data if result.success else result.error
    # list[Any]: CallToolResult.content is a list of the SDK's content-block
    # union, and a list[TextContent] is invariant-incompatible with it.
    content: list[Any] = [sdk.text(type="text", text="" if text is None else str(text))]
    if result.system_reminder is not None:
        content.append(sdk.text(type="text", text=result.system_reminder))
    return sdk.call_tool_result(content=content, is_error=not result.success)


async def create_memory_server(
    config: MemoryConfig,
    *,
    actor: str | None = DEFAULT_ACTOR,
    name: str = _SERVER_NAME,
) -> Server[None]:
    """Build an MCP server serving `config`'s mounts over one `memory` tool.

    Async because the server's `instructions` are
    `memory_system_section(config)` — the prompt pack plus the live index,
    read from the store. They are rendered here and again per connection
    in the SDK lifespan: the per-session analogue of the
    frozen-index-per-conversation rule (ledger #51).
    """
    sdk = load_sdk()
    definition = get_tool_definition(create_memory_tool(config, actor=actor))
    if definition is None:  # pragma: no cover - @Tool always attaches one
        raise RuntimeError("create_memory_tool returned an undecorated function")
    memory_tool = sdk.tool(
        name=definition.name,
        description=definition.description,
        input_schema=definition.parameters,
        annotations=sdk.tool_annotations(
            read_only_hint=False,
            destructive_hint=True,  # delete/rename/overwriting create exist
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )

    async def on_list_tools(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: PaginatedRequestParams | None,  # noqa: ARG001 - one tool, no paging
    ) -> ListToolsResult:
        return sdk.list_tools_result(tools=[memory_tool])

    async def on_call_tool(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: CallToolRequestParams,
    ) -> CallToolResult:
        if params.name != definition.name:
            return _to_call_tool_result(
                sdk,
                ToolResult.fail(
                    ErrorMessages.TOOL_NOT_FOUND.format(tool_name=params.name)
                ),
            )
        arguments = params.arguments or {}
        try:
            result = await dispatch(
                config, arguments.get("command"), arguments, actor=actor
            )
        # Parity with agent/tool_exec.py: a mistyped value must come back
        # as the same corrective failure text on every transport, never a
        # JSON-RPC internal error on this one.
        except TypeError as exc:
            result = ToolResult.fail(
                ErrorMessages.TOOL_INVALID_ARGUMENTS.format(
                    tool_name=definition.name, error=exc
                )
            )
        except Exception as exc:
            logger.exception("memory command failed")
            result = ToolResult.fail(
                ErrorMessages.TOOL_EXECUTION_FAILED.format(
                    tool_name=definition.name, error=exc
                )
            )
        return _to_call_tool_result(sdk, result)

    @asynccontextmanager
    async def lifespan(server: Server[None]) -> AsyncIterator[None]:
        # One session per stdio process; `run()` enters this before any
        # request, so `server/discover` reports this session's index.
        server.instructions = await memory_system_section(config)
        yield None

    return sdk.server_class(
        name,
        instructions=await memory_system_section(config),
        lifespan=lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve_stdio(server: Server[None]) -> None:
    """Serve one MCP session on stdio (claims fd 0/1 for the duration)."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )
