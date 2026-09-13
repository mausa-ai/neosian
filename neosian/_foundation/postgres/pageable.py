"""PostgresStore's side of paged listings (NQ2 slice C, §8; mixin — the
host owns the pool and the statements).

One keyset statement per page (`statements_pageable.py`), fetching
`limit + 1` rows: the extra row only says that something follows. A
redaction's cursor carries the trail row's `id`, the tiebreak the ABC
read already orders by and the value type does not expose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.memory.journal import since_window
from neosian._foundation.memory.pageable import (
    Page,
    decode_cursor,
    encode_cursor,
    history_cursor,
    history_key,
    instant,
    instant_part,
    page_limit,
    paged,
)
from neosian._foundation.memory.paths import validate_document_path
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.postgres.memory_store import like_prefix
from neosian._foundation.postgres.rows import (
    memory_entry,
    memory_redaction,
    memory_version,
)

if TYPE_CHECKING:
    from datetime import datetime

    from neosian._foundation.memory.types import (
        MemoryEntry,
        MemoryRedaction,
        MemoryVersion,
    )
    from neosian._foundation.postgres.pool import PostgresPool
    from neosian._foundation.postgres.statements_pageable import PageableStatements


class PostgresPageableStore:
    _pool: PostgresPool
    _pageable_sql: PageableStatements

    async def list_documents_page(
        self, scope: str, *, prefix: str = "", cursor: str | None = None, limit: int
    ) -> Page[MemoryEntry]:
        scope = parse_scope(scope)
        page_limit(limit)
        after = None if cursor is None else decode_cursor("list", cursor, str)[0]
        rows = await self._pool.fetch(
            self._pageable_sql.list_documents,
            {
                "scope": scope,
                "pattern": like_prefix(prefix),
                "path": after,
                "limit": limit + 1,
            },
        )
        entries = [memory_entry(scope, row) for row in rows]
        return paged(entries, limit, lambda e: encode_cursor("list", e.path))

    async def versions_page(
        self, scope: str, path: str, *, cursor: str | None = None, limit: int
    ) -> Page[MemoryVersion]:
        scope = parse_scope(scope)
        validate_document_path(path)
        page_limit(limit)
        before = None if cursor is None else decode_cursor("versions", cursor, int)[0]
        rows = await self._pool.fetch(
            self._pageable_sql.read_versions,
            {"scope": scope, "path": path, "version": before, "limit": limit + 1},
        )
        versions = [memory_version(scope, row) for row in rows]
        return paged(versions, limit, lambda r: encode_cursor("versions", r.version))

    async def history_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryVersion]:
        scope = parse_scope(scope)
        since_window(since, None)
        page_limit(limit)
        at, path, version = (
            (None, None, None) if cursor is None else history_key(cursor)
        )
        rows = await self._pool.fetch(
            self._pageable_sql.read_history,
            {
                "scope": scope,
                "since": since,
                "at": at,
                "path": path,
                "version": version,
                "limit": limit + 1,
            },
        )
        return paged(
            [memory_version(scope, row) for row in rows], limit, history_cursor
        )

    async def redactions_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryRedaction]:
        scope = parse_scope(scope)
        since_window(since, None)
        page_limit(limit)
        at, trail_id = (None, None) if cursor is None else _trail_key(cursor)
        rows = await self._pool.fetch(
            self._pageable_sql.read_redactions,
            {
                "scope": scope,
                "since": since,
                "at": at,
                "id": trail_id,
                "limit": limit + 1,
            },
        )
        page = paged(
            list(rows),
            limit,
            lambda row: encode_cursor("trail", instant(row[2]), int(row[4])),
        )
        return Page(
            tuple(memory_redaction(row[:4]) for row in page.items), page.next_cursor
        )


def _trail_key(cursor: str) -> tuple[datetime, int]:
    stamp, trail_id = decode_cursor("trail", cursor, str, int)
    return instant_part("trail", cursor, stamp), trail_id
