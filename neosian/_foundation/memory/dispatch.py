"""The memory command ladder, shared by every transport.

One dispatcher serves the function tool (`tools.py`), the native
`memory_20250818` declaration (same closure, Anthropic wire), and the
MCP server (`_foundation/mcp/`): the unknown-command guard, per-command
argument checks, the `file_text` alias and the store-error → corrective
failure mapping run once, so the three transports cannot drift.

`command` arrives as `object`: nothing upstream validates it — the
function tool's `Literal` is schema steering, and MCP hands us raw JSON.
Argument values pass through untyped for the same reason the enum is
guarded (`additionalProperties: false` is steering, not a guarantee):
a mistyped value raises inside the command body and every transport's
outer catch turns that into the same execution-failure text. Unknown
argument keys are ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from neosian._foundation.memory import commands
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.shared.exceptions import MemoryStoreError
from neosian._foundation.tools.base import ToolResult

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


async def dispatch(
    config: MemoryConfig,
    command: object,
    arguments: Mapping[str, Any],
    *,
    actor: str | None = None,
) -> ToolResult[str]:
    """Execute one memory command against the mounts.

    A model that ignores the schema must get a corrective failure, not a
    misleading parameter error from a fallthrough branch — so the command
    is guarded before anything else, and every `MemoryStoreError` returns
    as `[code] message` plus a per-code hint.
    """
    if not isinstance(command, str) or command not in _COMMANDS:
        return ToolResult.fail(
            f"Unknown command {command!r}",
            system_reminder=f"Valid commands: {', '.join(_COMMANDS)}.",
        )
    path = arguments.get("path")
    try:
        if command == "view":
            return await commands.view(
                config,
                path if path is not None else "/",
                arguments.get("view_range"),
            )
        if command == "create":
            # `file_text` is the reference `memory_20250818` name for the
            # document text; `content` (our schema name) wins when both arrive.
            content = arguments.get("content")
            text = content if content is not None else arguments.get("file_text")
            return await commands.create(
                config,
                actor,
                _require(path, command, "path"),
                _require(text, command, "content"),
            )
        if command == "str_replace":
            return await commands.str_replace(
                config,
                actor,
                _require(path, command, "path"),
                _require(arguments.get("old_str"), command, "old_str"),
                _require(arguments.get("new_str"), command, "new_str"),
            )
        if command == "insert":
            return await commands.insert(
                config,
                actor,
                _require(path, command, "path"),
                _require(arguments.get("insert_line"), command, "insert_line"),
                _require(arguments.get("insert_text"), command, "insert_text"),
            )
        if command == "delete":
            return await commands.delete(config, actor, _require(path, command, "path"))
        return await commands.rename(
            config,
            actor,
            _require(arguments.get("old_path"), command, "old_path"),
            _require(arguments.get("new_path"), command, "new_path"),
        )
    except _ArgumentError as exc:
        return ToolResult.fail(str(exc))
    except MemoryStoreError as exc:
        return ToolResult.fail(
            f"[{exc.code}] {exc.message}",
            system_reminder=_HINTS.get(exc.code),
        )
