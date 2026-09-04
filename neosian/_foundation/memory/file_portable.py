"""FileStore's side of store mobility (DESIGN §26; mixin over the root
and the lock, the `FileTurnStore` shape).

`scopes()` walks the root descending only into directories whose name
carries a `%3A` — every scope component does, `conversations/`, the
spool and the layout names never do — and lists a directory as a scope
when it holds a document, a sidecar or a trail; a foreign directory
fails the grammar and is skipped, as listings skip foreign files.
`restore_scope` writes sidecars first (atomic, fsync'd — a number can
never be reused even if the process dies before the documents land),
then the trail, then the documents; everything private (ledger #126).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.conversation.file_turns import (
    CONVERSATIONS,
    PROJECTIONS,
    TURNS,
    render_projection,
    render_turn,
)
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.memory import file_layout as layout, journal
from neosian._foundation.memory.envelope import Envelope, render
from neosian._foundation.memory.paths import validate_document_path
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.shared.exceptions import (
    ConversationConflictError,
    ConversationIdInvalidError,
    MemoryConflictError,
    MemoryScopeInvalidError,
)
from neosian._foundation.shared.fileio import atomic_write, private_mkdir

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path

    from neosian._foundation.memory.portable import ConversationArchive, ScopeArchive
    from neosian._foundation.memory.types import MemoryVersion

_ENCODED_COLON = "%3A"


class FilePortableStore:
    """The `Portable` protocol over a plain directory (mixin; the host
    class owns the two attributes below)."""

    _root: Path
    _lock: asyncio.Lock

    async def scopes(self) -> tuple[str, ...]:
        found: list[str] = []
        self._walk(self._root, (), found)
        return tuple(sorted(found))

    async def conversations(self) -> tuple[str, ...]:
        parent = self._root / CONVERSATIONS
        if not parent.is_dir():
            return ()
        found = []
        for child in parent.iterdir():
            try:
                conversation_id = parse_conversation_id(child.name)
            except ConversationIdInvalidError:
                continue  # a foreign name is not ours
            if _conversation_occupied(child):
                found.append(conversation_id)
        return tuple(sorted(found))

    async def restore_scope(self, archive: ScopeArchive) -> None:
        scope = parse_scope(archive.scope)
        for path in {doc.path for doc in archive.documents} | {
            row.path for row in archive.versions
        }:
            validate_document_path(path)
        async with self._lock:
            scope_dir = layout.scope_dir(self._root, scope)
            if _scope_occupied(scope_dir):
                raise MemoryConflictError(scope, None, "target_occupied")
            by_path: dict[str, list[MemoryVersion]] = {}
            for row in archive.versions:
                by_path.setdefault(row.path, []).append(row)
            for path, rows in by_path.items():
                rows.sort(key=lambda row: row.version)
                journal.rewrite_rows(layout.journal_file(self._root, scope, path), rows)
            if archive.redactions:
                journal.write_redactions(
                    scope_dir / layout.REDACTIONS, archive.redactions
                )
            for document in archive.documents:
                envelope = Envelope(
                    version=document.version,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                    actor=document.actor,
                    redacted=document.redacted,
                    extra=document.extra,
                )
                journal.atomic_write(
                    layout.doc_file(self._root, scope, document.path),
                    render(envelope, document.content),
                )

    async def restore_conversation(self, archive: ConversationArchive) -> None:
        conversation_id = parse_conversation_id(archive.conversation_id)
        async with self._lock:
            directory = self._root / CONVERSATIONS / conversation_id
            if _conversation_occupied(directory):
                raise ConversationConflictError(conversation_id, "target_occupied")
            if archive.turns or archive.projections:
                private_mkdir(directory)
            if archive.turns:
                text = "".join(render_turn(turn) for turn in archive.turns)
                atomic_write(directory / TURNS, text, fsync=True)
            if archive.projections:
                text = "".join(
                    render_projection(entry) for entry in archive.projections
                )
                atomic_write(directory / PROJECTIONS, text, fsync=True)

    def _walk(self, directory: Path, parts: tuple[str, ...], found: list[str]) -> None:
        for child in directory.iterdir():
            if not child.is_dir() or _ENCODED_COLON not in child.name:
                continue
            below = (*parts, child.name)
            try:
                scope = layout.scope_from_directory(below)
            except MemoryScopeInvalidError:
                continue
            if _scope_occupied(child):
                found.append(scope)
            self._walk(child, below, found)


def _scope_occupied(scope_dir: Path) -> bool:
    return (
        any((scope_dir / layout.DOCUMENTS).rglob("*.md"))
        or any((scope_dir / layout.VERSIONS).rglob("*.jsonl"))
        or (scope_dir / layout.REDACTIONS).is_file()
    )


def _conversation_occupied(directory: Path) -> bool:
    return (directory / TURNS).is_file() or (directory / PROJECTIONS).is_file()
