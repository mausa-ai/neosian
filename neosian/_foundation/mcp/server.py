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
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from neosian._foundation.conversation.recall import create_recall_any_tool
from neosian._foundation.mcp.sdk import Sdk, load_sdk
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.index import memory_system_section
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.skills import (
    create_skill_tools,
    list_skills,
    load_skill,
)
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.constants import ErrorMessages
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


def _read_only(sdk: Sdk, tool: ToolFunction) -> tuple[McpTool, Handler]:
    """A read-only tool served verbatim, its handler validating then
    calling it by keyword."""
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
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
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
) -> Server[None]:
    """Build an MCP server serving `config`'s mounts over the `memory`
    tool, its skills over `list_skills`/`load_skill` and as prompts —
    and, given `conversations`, `recall_turn` over its turns.

    Async because the server's `instructions` are
    `memory_system_section(config)` — the prompt pack plus the live index,
    read from the store. They are rendered here and again per connection
    in the SDK lifespan: the per-session analogue of the
    frozen-index-per-conversation rule (ledger #51).
    """
    sdk = load_sdk()
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
    readers = list(create_skill_tools((), config))
    if conversations is not None:
        readers.append(create_recall_any_tool(conversations))
    for reader in readers:
        served, call = _read_only(sdk, reader)
        tools.append(served)
        handlers[served.name] = call

    async def on_list_tools(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: PaginatedRequestParams | None,  # noqa: ARG001 - no paging
    ) -> ListToolsResult:
        return sdk.list_tools_result(tools=tools)

    # Skills as prompts, listed live: one written this session is a
    # command in the next request (§24.3).
    async def on_list_prompts(
        ctx: ServerRequestContext[None, Any],  # noqa: ARG001 - SDK handler shape
        params: PaginatedRequestParams | None,  # noqa: ARG001 - no paging
    ) -> ListPromptsResult:
        entries = await list_skills(config, ())
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
        entry = await load_skill(config, (), params.name)
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
        server.instructions = await memory_system_section(config)
        yield None

    return sdk.server_class(
        name,
        instructions=await memory_system_section(config),
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
