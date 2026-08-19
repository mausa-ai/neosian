"""The six memory command bodies (DESIGN §8 tool layer).

`str_replace` and `insert` are compositions of read + write — the store
stays at seven methods (C7). Bodies return `ToolResult`; store errors
escape to the dispatcher in `tools.py`, which maps them to informative
failures the model can act on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.memory.index import generate_memory_index
from neosian._foundation.memory.mounts import resolve, writable
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryPathInvalidError,
)
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.memory.mounts import MemoryConfig, Mount
    from neosian._foundation.memory.types import MemoryDocument

_REDACTED_NOTICE = "(redacted — content was cleared; history is preserved)"
_INDEX_HINT = "Run the memory tool with command 'view' and path '/' first."


def _virtual(mount: Mount, doc_path: str) -> str:
    return f"/{mount.mount_path}/{doc_path}"


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


def _numbered(document: MemoryDocument, virtual: str) -> str:
    lines = document.content.split("\n")
    width = len(str(len(lines)))
    body = "\n".join(
        f"{number:>{width}}: {line}" for number, line in enumerate(lines, 1)
    )
    return f"{virtual} (v{document.version}):\n{body}"


def _match_lines(content: str, needle: str) -> list[int]:
    """1-based line numbers where each occurrence of `needle` starts."""
    numbers: list[int] = []
    start = 0
    while (found := content.find(needle, start)) != -1:
        numbers.append(content.count("\n", 0, found) + 1)
        start = found + 1
    return numbers


async def _render_listing(
    config: MemoryConfig, mount: Mount, *, prefix: str
) -> ToolResult[str]:
    entries = await config.store.list_documents(mount.scope, prefix=prefix)
    shown = [
        f"- {_virtual(mount, entry.path)}{' (redacted)' if entry.redacted else ''}"
        for entry in entries
    ]
    if not shown:
        return ToolResult.ok(f"/{mount.mount_path} is empty")
    return ToolResult.ok("\n".join(shown))


async def view(config: MemoryConfig, path: str) -> ToolResult[str]:
    if not path.strip("/"):
        return ToolResult.ok(await generate_memory_index(config.store, config.mounts))
    mount, doc_path = resolve(config, path)
    if not doc_path:
        return await _render_listing(config, mount, prefix="")
    document = await config.store.read(mount.scope, doc_path)
    if document is not None:
        if document.redacted:
            return ToolResult.ok(f"{_virtual(mount, doc_path)}: {_REDACTED_NOTICE}")
        return ToolResult.ok(_numbered(document, _virtual(mount, doc_path)))
    # `prefix` is a plain string match (§8) — the trailing slash makes it
    # a directory boundary rather than a name prefix.
    entries = await config.store.list_documents(mount.scope, prefix=doc_path + "/")
    if entries:
        return await _render_listing(config, mount, prefix=doc_path + "/")
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
    document = await config.store.write(mount.scope, doc_path, content, actor=actor)
    if existing is not None:
        return ToolResult.ok(
            f"Created {_virtual(mount, doc_path)} (v{document.version})",
            system_reminder=(
                f"Overwrote an existing document (was v{existing.version}); "
                "prefer str_replace for edits."
            ),
        )
    return ToolResult.ok(f"Created {_virtual(mount, doc_path)} (v{document.version})")


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
    return ToolResult.ok(f"Edited {_virtual(mount, doc_path)} (v{updated.version})")


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
    return ToolResult.ok(f"Edited {_virtual(mount, doc_path)} (v{updated.version})")


async def delete(config: MemoryConfig, actor: str | None, path: str) -> ToolResult[str]:
    mount, doc_path = _resolve_document(config, path)
    writable(mount)
    removed = await config.store.delete(mount.scope, doc_path, actor=actor)
    if not removed:
        return ToolResult.fail(
            f"No document at {_virtual(mount, doc_path)}",
            system_reminder=_INDEX_HINT,
        )
    return ToolResult.ok(f"Deleted {_virtual(mount, doc_path)}")


async def rename(
    config: MemoryConfig, actor: str | None, old_path: str, new_path: str
) -> ToolResult[str]:
    src_mount, src = _resolve_document(config, old_path)
    dst_mount, dst = _resolve_document(config, new_path)
    writable(src_mount)
    writable(dst_mount)
    if src_mount.mount_path == dst_mount.mount_path:
        document = await config.store.rename(src_mount.scope, src, dst, actor=actor)
        return ToolResult.ok(
            f"Renamed {_virtual(src_mount, src)} to "
            f"{_virtual(dst_mount, dst)} (v{document.version})"
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
        f"{_virtual(dst_mount, dst)} (v{moved.version})"
    )
