"""The six memory command bodies (DESIGN §8 tool layer).

`str_replace` and `insert` are compositions of read + write — the store
stays at seven methods (C7). Bodies return `ToolResult`; store errors
escape to the dispatcher in `tools.py`, which maps them to informative
failures the model can act on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.memory.index import generate_memory_index
from neosian._foundation.memory.mounts import resolve, structural, writable
from neosian._foundation.memory.receipt import MemoryWriteReceipt
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryPathInvalidError,
)
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.memory.mounts import MemoryConfig, Mount
    from neosian._foundation.memory.types import MemoryDocument, MemoryEntry

_REDACTED_NOTICE = "(redacted — content was cleared; history is preserved)"
_INDEX_HINT = "Run the memory tool with command 'view' and path '/' first."


def _virtual(mount: Mount, doc_path: str) -> str:
    return f"/{mount.mount_path}/{doc_path}"


def _receipt(
    command: str,
    mount: Mount,
    doc_path: str,
    version: int,
    previous: str | None = None,
) -> MemoryWriteReceipt:
    return MemoryWriteReceipt(
        command=command,
        mount_path=mount.mount_path,
        path=_virtual(mount, doc_path),
        version=version,
        previous_path=previous,
    )


def _resolve_document(config: MemoryConfig, path: str) -> tuple[Mount, str]:
    """Resolve a path that must name a document, not a mount root."""
    mount, doc_path = resolve(config, path)
    if not doc_path:
        raise MemoryPathInvalidError(path, "a mount root is not a document")
    return mount, doc_path


async def _read_required(
    config: MemoryConfig, mount: Mount, doc_path: str
) -> MemoryDocument:
    document = await config.store.read(mount.scope, doc_path)
    if document is None:
        raise MemoryDocumentNotFoundError(mount.scope, doc_path)
    return document


def _numbered(
    document: MemoryDocument, virtual: str, span: tuple[int, int] | None = None
) -> str:
    lines = document.content.split("\n")
    width = len(str(len(lines)))
    start, end = span if span is not None else (1, len(lines))
    body = "\n".join(
        f"{number:>{width}}: {line}"
        for number, line in enumerate(lines, 1)
        if start <= number <= end
    )
    return f"{virtual} (v{document.version}):\n{body}"


def _resolve_view_range(
    view_range: list[int], line_count: int
) -> tuple[int, int] | str:
    """Validated 1-indexed inclusive (start, end) from the reference
    [start_line, end_line] form (end -1 = end of file), or an error string."""
    if len(view_range) != 2:
        return f"view_range must be [start_line, end_line], got {view_range}"
    start, end = view_range
    if start < 1 or start > line_count:
        return f"view_range start {start} is outside [1, {line_count}]"
    if end != -1 and end < start:
        return f"view_range end {end} must be -1 or >= start {start}"
    return start, (line_count if end == -1 else min(end, line_count))


def _match_lines(content: str, needle: str) -> list[int]:
    """1-based line numbers where each occurrence of `needle` starts."""
    numbers: list[int] = []
    start = 0
    while (found := content.find(needle, start)) != -1:
        numbers.append(content.count("\n", 0, found) + 1)
        start = found + 1
    return numbers


def _render_listing(mount: Mount, entries: Sequence[MemoryEntry]) -> ToolResult[str]:
    shown = [
        f"- {_virtual(mount, entry.path)}{' (redacted)' if entry.redacted else ''}"
        for entry in entries
    ]
    if not shown:
        return ToolResult.ok(f"/{mount.mount_path} is empty")
    return ToolResult.ok("\n".join(shown))


async def view(
    config: MemoryConfig, path: str, view_range: list[int] | None = None
) -> ToolResult[str]:
    # view_range applies to document views only; index and directory
    # listings ignore it (the reference tool's semantics).
    if not path.strip("/"):
        return ToolResult.ok(await generate_memory_index(config.store, config.mounts))
    mount, doc_path = resolve(config, path)
    if not doc_path:
        return _render_listing(mount, await config.store.list_documents(mount.scope))
    document = await config.store.read(mount.scope, doc_path)
    if document is not None:
        if document.redacted:
            return ToolResult.ok(f"{_virtual(mount, doc_path)}: {_REDACTED_NOTICE}")
        span: tuple[int, int] | None = None
        if view_range is not None:
            resolved = _resolve_view_range(view_range, document.content.count("\n") + 1)
            if isinstance(resolved, str):
                return ToolResult.fail(resolved)
            span = resolved
        return ToolResult.ok(_numbered(document, _virtual(mount, doc_path), span))
    # `prefix` is a plain string match (§8) — the trailing slash makes it
    # a directory boundary rather than a name prefix.
    entries = await config.store.list_documents(mount.scope, prefix=doc_path + "/")
    if entries:
        return _render_listing(mount, entries)
    return ToolResult.fail(
        f"No document or directory at {_virtual(mount, doc_path)}",
        system_reminder=_INDEX_HINT,
    )


async def create(
    config: MemoryConfig, actor: str | None, path: str, content: str
) -> ToolResult[str]:
    mount, doc_path = _resolve_document(config, path)
    writable(mount)
    existing = await config.store.read(mount.scope, doc_path)
    if existing is None:
        # Overwriting is an edit; only a brand-new path grows the set.
        structural(mount)
    # An overwrite carries the version it read, like every other
    # content-destroying command: a document that moved underneath fails
    # correctively instead of being silently replaced.
    document = await config.store.write(
        mount.scope,
        doc_path,
        content,
        actor=actor,
        expected_version=None if existing is None else existing.version,
    )
    receipt = _receipt("create", mount, doc_path, document.version)
    if existing is not None:
        return ToolResult.ok(
            f"Created {_virtual(mount, doc_path)} (v{document.version})",
            system_reminder=(
                f"Overwrote an existing document (was v{existing.version}); "
                "prefer str_replace for edits."
            ),
            receipt=receipt,
        )
    return ToolResult.ok(
        f"Created {_virtual(mount, doc_path)} (v{document.version})", receipt=receipt
    )


async def str_replace(
    config: MemoryConfig,
    actor: str | None,
    path: str,
    old_str: str,
    new_str: str,
) -> ToolResult[str]:
    mount, doc_path = _resolve_document(config, path)
    writable(mount)
    if not old_str:
        return ToolResult.fail("old_str must be non-empty")
    document = await _read_required(config, mount, doc_path)
    if document.redacted:
        return ToolResult.fail(
            f"{_virtual(mount, doc_path)} is redacted; its content was cleared",
            system_reminder="Create the document again if it is still needed.",
        )
    matches = _match_lines(document.content, old_str)
    if not matches:
        return ToolResult.fail(
            f"old_str did not appear verbatim in {_virtual(mount, doc_path)}",
            system_reminder="View the document and copy the exact text to replace.",
        )
    if len(matches) > 1:
        lines = ", ".join(str(number) for number in matches)
        return ToolResult.fail(
            f"old_str appears {len(matches)} times in "
            f"{_virtual(mount, doc_path)} (lines {lines})",
            system_reminder="Include more surrounding text to make it unique.",
        )
    updated = await config.store.write(
        mount.scope,
        doc_path,
        document.content.replace(old_str, new_str, 1),
        actor=actor,
        expected_version=document.version,
    )
    return ToolResult.ok(
        f"Edited {_virtual(mount, doc_path)} (v{updated.version})",
        receipt=_receipt("str_replace", mount, doc_path, updated.version),
    )


async def insert(
    config: MemoryConfig,
    actor: str | None,
    path: str,
    insert_line: int,
    insert_text: str,
) -> ToolResult[str]:
    mount, doc_path = _resolve_document(config, path)
    writable(mount)
    document = await _read_required(config, mount, doc_path)
    if document.redacted:
        return ToolResult.fail(
            f"{_virtual(mount, doc_path)} is redacted; its content was cleared",
            system_reminder="Create the document again if it is still needed.",
        )
    lines = document.content.split("\n")
    if not 0 <= insert_line <= len(lines):
        return ToolResult.fail(
            f"insert_line {insert_line} is out of range for "
            f"{_virtual(mount, doc_path)} (valid: 0 to {len(lines)})"
        )
    lines.insert(insert_line, insert_text)
    updated = await config.store.write(
        mount.scope,
        doc_path,
        "\n".join(lines),
        actor=actor,
        expected_version=document.version,
    )
    return ToolResult.ok(
        f"Edited {_virtual(mount, doc_path)} (v{updated.version})",
        receipt=_receipt("insert", mount, doc_path, updated.version),
    )


async def delete(config: MemoryConfig, actor: str | None, path: str) -> ToolResult[str]:
    mount, doc_path = _resolve_document(config, path)
    writable(mount)
    structural(mount)
    removed = await config.store.delete(mount.scope, doc_path, actor=actor)
    if not removed:
        return ToolResult.fail(
            f"No document at {_virtual(mount, doc_path)}",
            system_reminder=_INDEX_HINT,
        )
    # The delete's consumed version number is not in the ABC's return
    # (`bool`), and `read` is None after a delete — the newest version row
    # is the one place that knows it. One row read (full content, C5) is
    # the price of a receipt an undo can trust.
    rows = await config.store.versions(mount.scope, doc_path, limit=1)
    return ToolResult.ok(
        f"Deleted {_virtual(mount, doc_path)}",
        receipt=_receipt("delete", mount, doc_path, rows[0].version) if rows else None,
    )


async def rename(
    config: MemoryConfig, actor: str | None, old_path: str, new_path: str
) -> ToolResult[str]:
    src_mount, src = _resolve_document(config, old_path)
    dst_mount, dst = _resolve_document(config, new_path)
    writable(src_mount)
    writable(dst_mount)
    structural(src_mount)
    structural(dst_mount)
    if src_mount.mount_path == dst_mount.mount_path:
        document = await config.store.rename(src_mount.scope, src, dst, actor=actor)
        return ToolResult.ok(
            f"Renamed {_virtual(src_mount, src)} to "
            f"{_virtual(dst_mount, dst)} (v{document.version})",
            receipt=_receipt(
                "rename", dst_mount, dst, document.version, _virtual(src_mount, src)
            ),
        )
    # Cross-mount: composed as read + write + delete. Not atomic — no
    # cross-scope transaction exists (C1); a crash between the write and
    # the delete leaves the document in both places.
    source = await _read_required(config, src_mount, src)
    if await config.store.read(dst_mount.scope, dst) is not None:
        raise MemoryConflictError(dst_mount.scope, dst, "destination_exists")
    moved = await config.store.write(dst_mount.scope, dst, source.content, actor=actor)
    await config.store.delete(src_mount.scope, src, actor=actor)
    return ToolResult.ok(
        f"Moved {_virtual(src_mount, src)} to "
        f"{_virtual(dst_mount, dst)} (v{moved.version})",
        receipt=_receipt(
            "rename", dst_mount, dst, moved.version, _virtual(src_mount, src)
        ),
    )
