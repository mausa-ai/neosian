"""The MCP server factory: the state set, served verbatim.

`create_memory_server` builds the SDK's low-level `Server` around the
function tools' own `ToolDefinition`s — name, description and JSON
schema are the bytes `create_memory_tool` (and, given a conversation
store, `create_recall_any_tool`) author, never re-derived from type
hints — and executes every memory call through `memory/dispatch.py`, so
the function tool, the native declaration and this server cannot drift
(ledger #50). The memory server becomes the state server one tool at a
time (§21.7): `recall_turn` with `conversation` required, so any agent
re-reads a recorded turn of any other; `list_skills`/`load_skill` over
the mounts' `skills/` documents, each of which is also an MCP prompt —
a slash command in the clients that render prompts (§24). Corrective
failures ride MCP's in-band `is_error` — that is `ToolResult`, expressed
in MCP's vocabulary (ledger #52).

The caller owns the store's lifetime (the ledger #33 rule): the factory
and the SDK's per-connection lifespan never close it — the entry point
in `neosian/mcp/serve.py` does.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from neosian._foundation.conversation.handoff import handoff_tools
from neosian._foundation.conversation.search_history import history_any_tools
from neosian._foundation.mcp.sdk import Sdk, load_sdk
from neosian._foundation.mcp.targets import SessionStartDoor
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.sessions import sessions_mount
from neosian._foundation.memory.skills import (
    create_skill_tools,
    list_skills,
    load_skill,
    shipped_skills,
)
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.record.context import render_instructions
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.tools.base import ToolMetadata, ToolResult, get_tool_metadata
from neosian._foundation.tools.schema import rejection, validate_arguments

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from mcp.server import Server, ServerRequestContext
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        GetPromptRequestParams,
        GetPromptResult,
        ListPromptsResult,
        ListToolsResult,
        PaginatedRequestParams,
        Tool as McpTool,
    )

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.shared.types import ToolFunction

    Handler = Callable[[Mapping[str, Any]], Awaitable[ToolResult[Any]]]

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


def _metadata(tool: ToolFunction) -> ToolMetadata:
    metadata = get_tool_metadata(tool)
    if metadata is None or metadata.arguments is None:  # pragma: no cover
        raise RuntimeError("a tool factory returned an undecorated function")
    return metadata


def _validated(
    metadata: ToolMetadata, arguments: Mapping[str, Any]
) -> dict[str, Any] | ToolResult[Any]:
    """The arguments as the schema promises them, or the same corrective
    failure `agent/tool_exec.py` returns — parity across transports."""
    assert metadata.arguments is not None  # _metadata guarantees it
    try:
        return validate_arguments(metadata.arguments, arguments)
    except ValidationError as exc:
        return rejection(metadata.name, exc)


def _served(
    sdk: Sdk, tool: ToolFunction, *, read_only: bool = True, destructive: bool = False
) -> tuple[McpTool, Handler]:
    """A function tool served verbatim, its handler validating then
    calling it by keyword; read-only unless said otherwise."""
    metadata = _metadata(tool)
    definition = metadata.definition

    async def call(arguments: Mapping[str, Any]) -> ToolResult[Any]:
        validated = _validated(metadata, arguments)
        if isinstance(validated, ToolResult):
            return validated
        return await tool(**validated)

    served = sdk.tool(
        name=definition.name,
        description=definition.description,
        input_schema=definition.parameters,
        annotations=sdk.tool_annotations(
            read_only_hint=read_only,
            destructive_hint=destructive,
            idempotent_hint=read_only,
            open_world_hint=False,
        ),
    )
    return served, call


async def create_memory_server(
    config: MemoryConfig,
    *,
    actor: str | None = DEFAULT_ACTOR,
    name: str = _SERVER_NAME,
    conversations: ConversationStore | None = None,
    session_start: SessionStartDoor = "instructions",
) -> Server[None]:
    """Build an MCP server serving `config`'s mounts over the `memory`
    tool, its skills over `list_skills`/`load_skill` and as prompts —
    and, given `conversations`, the state set over its turns.

    Async because the server's `instructions` are read from the store:
    the prompt pack plus the live index, the pending handoff note and
    "where we left off" for a client whose start door they are (§33), or
    the write discipline alone when the client's SessionStart hook prints
    the rest (`session_start="hook"`: one window, one copy). They are
    rendered here and again per connection in the SDK lifespan: the
    per-session analogue of the frozen-index-per-conversation rule
    (ledger #51).
    """
    sdk = load_sdk()

    async def instructions() -> str:
        if session_start == "hook":
            return get_prompt("context.start_instructions")
        return await render_instructions(config, conversations, now=datetime.now(UTC))

    memory_metadata = _metadata(create_memory_tool(config, actor=actor))
    memory = memory_metadata.definition

    async def call_memory(arguments: Mapping[str, Any]) -> ToolResult[Any]:
        validated = _validated(memory_metadata, arguments)
        if isinstance(validated, ToolResult):
            return validated
        return await dispatch(config, validated["command"], validated, actor=actor)

    tools = [
        sdk.tool(
            name=memory.name,
            description=memory.description,
            input_schema=memory.parameters,
            annotations=sdk.tool_annotations(
                read_only_hint=False,
                destructive_hint=True,  # delete/rename/overwriting create exist
                idempotent_hint=False,
                open_world_hint=False,
            ),
        )
    ]
    handlers: dict[str, Handler] = {memory.name: call_memory}
    shipped = shipped_skills()
    served_tools = [
        (reader, True, False) for reader in create_skill_tools(shipped, config)
    ]
    mount = sessions_mount(config.mounts)
    if conversations is not None:
        served_tools.extend((t, True, False) for t in history_any_tools(conversations))
        if mount is not None:
            # The handoff (§33): continue_session marks the baton picked
            # up, handoff replaces the standing note — both write.
            continuing, departing = handoff_tools(
                conversations, config.store, mount.scope, actor=actor
            )
            served_tools.extend([(continuing, False, False), (departing, False, True)])
    for tool, read_only, destructive in served_tools:
        served, call = _served(sdk, tool, read_only=read_only, destructive=destructive)
        tools.append(served)
        handlers[served.name] = call

    async def on_list_tools(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: PaginatedRequestParams | None,  # noqa: ARG001 - no paging
    ) -> ListToolsResult:
        return sdk.list_tools_result(tools=tools)

    # Skills as prompts, listed live: one written this session is a
    # command in the next request (§24.3); the wheel's own skills (the
    # `handoff` prompt, §33) are listed after the mounts' as a directory.
    async def on_list_prompts(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: PaginatedRequestParams | None,  # noqa: ARG001 - no paging
    ) -> ListPromptsResult:
        entries = await list_skills(config, shipped)
        return sdk.list_prompts_result(
            prompts=[
                sdk.prompt(name=entry.name, description=entry.skill.description)
                for entry in entries
                if entry.skill is not None
            ]
        )

    async def on_get_prompt(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: GetPromptRequestParams,
    ) -> GetPromptResult:
        entry = await load_skill(config, shipped, params.name)
        if entry is None or entry.skill is None:
            raise sdk.error(
                code=sdk.invalid_params,
                message=ErrorMessages.SKILL_NOT_FOUND.format(name=params.name),
            )
        return sdk.get_prompt_result(
            description=entry.skill.description,
            messages=[
                sdk.prompt_message(
                    role="user", content=sdk.text(type="text", text=entry.skill.content)
                )
            ],
        )

    async def on_call_tool(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: CallToolRequestParams,
    ) -> CallToolResult:
        handler = handlers.get(params.name)
        if handler is None:
            return _to_call_tool_result(
                sdk,
                ToolResult.fail(
                    ErrorMessages.TOOL_NOT_FOUND.format(tool_name=params.name)
                ),
            )
        try:
            result = await handler(params.arguments or {})
        # Parity with agent/tool_exec.py: a mistyped or missing value must
        # come back as the same corrective failure text on every
        # transport, never a JSON-RPC internal error on this one.
        except TypeError as exc:
            result = ToolResult.fail(
                ErrorMessages.TOOL_INVALID_ARGUMENTS.format(
                    tool_name=params.name, error=exc
                )
            )
        except Exception as exc:
            logger.exception("%s call failed", params.name)
            result = ToolResult.fail(
                ErrorMessages.TOOL_EXECUTION_FAILED.format(
                    tool_name=params.name, error=exc
                )
            )
        return _to_call_tool_result(sdk, result)

    @asynccontextmanager
    async def lifespan(server: Server[None]) -> AsyncIterator[None]:
        # One session per stdio process; `run()` enters this before any
        # request, so `server/discover` reports this session's index.
        server.instructions = await instructions()
        yield None

    return sdk.server_class(
        name,
        instructions=await instructions(),
        lifespan=lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_prompts=on_list_prompts,
        on_get_prompt=on_get_prompt,
    )


async def serve_stdio(server: Server[None]) -> None:
    """Serve one MCP session on stdio (claims fd 0/1 for the duration)."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )
