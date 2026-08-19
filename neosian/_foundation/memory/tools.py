"""The `memory` tool — one function tool, Anthropic's command vocabulary.

One tool with a `command` enum rather than six tools (ledger #19): the
name and shape frontier models are post-trained on, one definition per
request, and the N4 native `memory_20250818` flag becomes a pure
transport swap. The flat schema cannot vary required parameters per
command, so per-command checks happen here with corrective failures.

This module deliberately has no `from __future__ import annotations`:
the @Tool decorator resolves the signature's hints at decoration time.
"""

from typing import Final, Literal

from neosian._foundation.memory import commands
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.shared.exceptions import MemoryStoreError
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

_TOOL_NAME: Final = "memory"
_COMMANDS: Final = ("view", "create", "str_replace", "insert", "delete", "rename")

# Per-code guidance appended to store-error failures (system_reminder).
_HINTS: Final[dict[str, str]] = {
    "memory_document_not_found": (
        "Run the memory tool with command 'view' and path '/' to see what exists."
    ),
    "memory_path_invalid": (
        "Paths look like /mount/topic-name; check the index with view '/'."
    ),
    "memory_read_only_mount": (
        "This mount is reference-only; write to a writable mount instead."
    ),
    "memory_conflict": (
        "The document changed underneath you — view it again before editing."
    ),
    "memory_format_unsupported": (
        "This document was written by a newer neosian; leave it untouched."
    ),
}


class _ArgumentError(Exception):
    """A required parameter for a command was missing (never escapes)."""

    def __init__(self, command: str, name: str) -> None:
        super().__init__(f"The {command!r} command requires the {name!r} parameter")


def _require[T](value: T | None, command: str, name: str) -> T:
    if value is None:
        raise _ArgumentError(command, name)
    return value


def create_memory_tool(
    config: MemoryConfig, *, actor: str | None = None
) -> ToolFunction:
    """Create the `memory` tool bound to a store and mounts.

    `actor` is recorded on every mutation's version row; N2's
    Conversation passes its conversation_id, the bare agent path passes
    None.
    """

    @Tool(name=_TOOL_NAME, description=get_prompt("memory.tool"))
    async def memory(
        command: Literal["view", "create", "str_replace", "insert", "delete", "rename"],
        path: str | None = None,
        content: str | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
        insert_text: str | None = None,
        old_path: str | None = None,
        new_path: str | None = None,
    ) -> ToolResult[str]:
        # Literal constrains the schema, never the runtime: a model that
        # ignores the enum must get a corrective failure, not fall into
        # the last dispatch branch's misleading parameter error. The str
        # widening keeps mypy (warn_unreachable) from proving the guard
        # impossible.
        received: str = command
        if received not in _COMMANDS:
            return ToolResult.fail(
                f"Unknown command {received!r}",
                system_reminder=f"Valid commands: {', '.join(_COMMANDS)}.",
            )
        try:
            if command == "view":
                return await commands.view(config, path if path is not None else "/")
            if command == "create":
                return await commands.create(
                    config,
                    actor,
                    _require(path, command, "path"),
                    _require(content, command, "content"),
                )
            if command == "str_replace":
                return await commands.str_replace(
                    config,
                    actor,
                    _require(path, command, "path"),
                    _require(old_str, command, "old_str"),
                    _require(new_str, command, "new_str"),
                )
            if command == "insert":
                return await commands.insert(
                    config,
                    actor,
                    _require(path, command, "path"),
                    _require(insert_line, command, "insert_line"),
                    _require(insert_text, command, "insert_text"),
                )
            if command == "delete":
                return await commands.delete(
                    config, actor, _require(path, command, "path")
                )
            return await commands.rename(
                config,
                actor,
                _require(old_path, command, "old_path"),
                _require(new_path, command, "new_path"),
            )
        except _ArgumentError as exc:
            return ToolResult.fail(str(exc))
        except MemoryStoreError as exc:
            return ToolResult.fail(
                f"[{exc.code}] {exc.message}",
                system_reminder=_HINTS.get(exc.code),
            )

    return memory
