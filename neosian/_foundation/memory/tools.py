"""The `memory` tool — one function tool, Anthropic's command vocabulary.

One tool with a `command` enum rather than six tools (ledger #19): the
name and shape frontier models are post-trained on, one definition per
request, and the N4 native `memory_20250818` flag becomes a pure
transport swap. The flat schema cannot vary required parameters per
command, so per-command checks happen in `dispatch.py` — the ladder
shared with the MCP transport — with corrective failures.

This module deliberately has no `from __future__ import annotations`:
the @Tool decorator resolves the signature's hints at decoration time.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final, Literal

from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult, set_native_type

logger = logging.getLogger(__name__)

_TOOL_NAME: Final = "memory"
NATIVE_MEMORY_TOOL_TYPE: Final = "memory_20250818"
# The mount path Anthropic's trained memory behavior roots at (§9.5.13).
_NATIVE_ROOT: Final = "memories"

# A transport: takes (command, arguments) and returns the dispatch result.
MemoryExecute = Callable[[object, dict[str, Any]], Awaitable[ToolResult[str]]]


def build_memory_tool(execute: MemoryExecute) -> ToolFunction:
    """The one `memory` wire definition; `execute` is the transport.

    Every transport — the dispatch closure below, the eval harness's
    in-process CLI leg — serves this exact name and signature, so the
    schema the model sees cannot fork per transport (the hazard #19/#50
    exist to prevent).
    """

    # `file_text` and `view_range` are the reference `memory_20250818`
    # argument names — accepted first-class so the native transport's
    # trained emissions never hit an unexpected-keyword failure.
    @Tool(name=_TOOL_NAME, description=get_prompt("memory.tool"))
    async def memory(
        command: Literal["view", "create", "str_replace", "insert", "delete", "rename"],
        path: str | None = None,
        view_range: list[int] | None = None,
        content: str | None = None,
        file_text: str | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
        insert_text: str | None = None,
        old_path: str | None = None,
        new_path: str | None = None,
    ) -> ToolResult[str]:
        return await execute(
            command,
            {
                "path": path,
                "view_range": view_range,
                "content": content,
                "file_text": file_text,
                "old_str": old_str,
                "new_str": new_str,
                "insert_line": insert_line,
                "insert_text": insert_text,
                "old_path": old_path,
                "new_path": new_path,
            },
        )

    return memory


def create_memory_tool(
    config: MemoryConfig,
    *,
    actor: str | None | Callable[[], str] = None,
    native: bool = False,
) -> ToolFunction:
    """Create the `memory` tool bound to a store and mounts.

    `actor` is recorded on every mutation's version row; N2's
    Conversation passes its conversation_id, the bare agent path passes
    None. A callable is resolved per command (NP): Conversation binds a
    closure yielding `<conversation_id>#<turn>`, so version rows carry a
    turn-ref without rebuilding the agent per send — the store still
    receives a plain opaque string. `native` marks the definition with
    Anthropic's `memory_20250818` type: the Anthropic client then sends
    the schema-less native declaration (the trained behavior replaces the
    wire description); every other provider — and the local execution
    path here — is byte-identical either way (ledger #41–#44).
    """

    async def execute(command: object, arguments: dict[str, Any]) -> ToolResult[str]:
        resolved = actor() if callable(actor) else actor
        return await dispatch(config, command, arguments, actor=resolved)

    memory = build_memory_tool(execute)

    if native:
        set_native_type(memory, NATIVE_MEMORY_TOOL_TYPE)
        if not any(m.mount_path == _NATIVE_ROOT for m in config.mounts):
            logger.warning(
                "native memory is on but no mount is named %r — the model's "
                "trained paths root at /%s, so expect one corrective "
                "round-trip while it discovers your mounts",
                _NATIVE_ROOT,
                _NATIVE_ROOT,
            )
    return memory
