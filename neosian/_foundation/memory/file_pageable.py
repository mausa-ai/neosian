"""FileStore's side of paged listings (NQ2 slice C, §8; mixin over the
host's scope check and readers, the `FilePortableStore` shape).

Each page reads what the ABC method reads and keeps the rows after the
cursor, so the files are still scanned whole: what a page bounds is the
answer, not the work — the low-concurrency appliance §18 already names.
The keysets are the listings' own orders: the path, the version, the
ledger's `(created_at, path, version)`, and a redaction's line in the
append-only trail.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from neosian._foundation.memory import file_layout as layout, journal
from neosian._foundation.memory.pageable import (
    Page,
    decode_cursor,
    encode_cursor,
    history_cursor,
    history_key,
    page_limit,
    paged,
)
from neosian._foundation.memory.paths import validate_document_path

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from neosian._foundation.memory.scope import Scope
    from neosian._foundation.memory.types import (
        MemoryEntry,
        MemoryRedaction,
        MemoryVersion,
    )


class FilePageableStore:
    """The `Pageable` protocol over a plain directory (mixin; the host
    class owns the members below)."""

    if TYPE_CHECKING:
        _root: Path

        def _scope(self, scope: str) -> Scope: ...

        def _rows(self, scope: str, path: str) -> tuple[MemoryVersion, ...]: ...

        def _list_documents(
            self, scope: Scope, prefix: str
        ) -> tuple[MemoryEntry, ...]: ...

        def _history(
            self, scope: Scope, since: datetime | None, limit: int | None
        ) -> tuple[MemoryVersion, ...]: ...

    async def list_documents_page(
        self, scope: str, *, prefix: str = "", cursor: str | None = None, limit: int
    ) -> Page[MemoryEntry]:
        scope = self._scope(scope)
        page_limit(limit)
        after = None if cursor is None else decode_cursor("list", cursor, str)[0]
        entries = await asyncio.to_thread(self._list_documents, scope, prefix)
        rows = [entry for entry in entries if after is None or entry.path > after]
        return paged(rows[: limit + 1], limit, lambda e: encode_cursor("list", e.path))

    async def versions_page(
        self, scope: str, path: str, *, cursor: str | None = None, limit: int
    ) -> Page[MemoryVersion]:
        scope = self._scope(scope)
        validate_document_path(path)
        page_limit(limit)
        before = None if cursor is None else decode_cursor("versions", cursor, int)[0]
        rows = [
            row
            for row in reversed(self._rows(scope, path))
            if before is None or row.version < before
        ]
        return paged(
            rows[: limit + 1], limit, lambda r: encode_cursor("versions", r.version)
        )

    async def history_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryVersion]:
        scope = self._scope(scope)
        journal.since_window(since, None)
        page_limit(limit)
        key = None if cursor is None else history_key(cursor)
        rows = await asyncio.to_thread(self._history, scope, since, None)
        after = [row for row in rows if key is None or _follows(row, key)]
        return paged(after[: limit + 1], limit, history_cursor)

    async def redactions_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryRedaction]:
        scope = self._scope(scope)
        journal.since_window(since, None)
        page_limit(limit)
        before = None if cursor is None else decode_cursor("trail", cursor, int)[0]
        trail = layout.scope_dir(self._root, scope) / layout.REDACTIONS
        acts = journal.read_redactions(trail, scope=scope)
        # Newest first is the trail read backwards; a line never moves.
        lines = [
            line
            for line in reversed(range(len(acts)))
            if (before is None or line < before)
            and (since is None or acts[line].created_at >= since)
        ][: limit + 1]
        page = paged(lines, limit, lambda line: encode_cursor("trail", line))
        return Page(tuple(acts[line] for line in page.items), page.next_cursor)


def _follows(row: MemoryVersion, key: tuple[datetime, str, int]) -> bool:
    """Whether `row` comes after the keyset in `created_at` desc, path,
    version desc."""
    created_at, path, version = key
    if row.created_at != created_at:
        return row.created_at < created_at
    if row.path != path:
        return row.path > path
    return row.version < version
