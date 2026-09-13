"""The MemoryStore seam over Postgres (mixin; PostgresStore owns the
pool, statements and clock).

`supports_optimistic_concurrency = True` — the reason this substrate
exists: the `expected_version` gate is race-safe because a losing
writer's version-row insert hits the primary key, aborts the whole
statement, and the retry re-reads a fresh snapshot where the expectation
fails. Scope is an opaque column value; the store never decomposes it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.journal import since_window
from neosian._foundation.memory.paths import validate_document_path
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.memory.types import MEMORY_FORMAT_VERSION
from neosian._foundation.postgres.rows import (
    aware_now,
    memory_document,
    memory_entry,
    memory_redaction,
    memory_version,
)
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryFormatUnsupportedError,
)

if TYPE_CHECKING:
    from datetime import datetime

    from neosian._foundation.memory.types import (
        MemoryDocument,
        MemoryEntry,
        MemoryRedaction,
        MemoryVersion,
    )
    from neosian._foundation.postgres.pool import PostgresPool
    from neosian._foundation.postgres.statements import Statements
    from neosian._foundation.shared.clock import Clock


def like_prefix(prefix: str) -> str:
    """Escape a plain string prefix into a LIKE pattern (never validated)."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


class PostgresMemoryStore(MemoryStore):
    """Memory documents over the memories/memory_versions tables."""

    supports_optimistic_concurrency: ClassVar[bool] = True

    _pool: PostgresPool
    _sql: Statements
    _clock: Clock

    async def read(self, scope: str, path: str) -> MemoryDocument | None:
        scope = parse_scope(scope)
        validate_document_path(path)
        rows = await self._pool.fetch(
            self._sql.read_document, {"scope": scope, "path": path}
        )
        if not rows:
            return None
        return memory_document(scope, path, rows[0])

    async def write(
        self,
        scope: str,
        path: str,
        content: str,
        *,
        actor: str | None = None,
        expected_version: int | None = None,
    ) -> MemoryDocument:
        scope = parse_scope(scope)
        validate_document_path(path)
        row = await self._pool.fetch_one_retry(
            self._sql.write_document,
            {
                "scope": scope,
                "path": path,
                "content": content,
                "actor": actor,
                "expected": expected_version,
                "now": aware_now(self._clock),
                "format": MEMORY_FORMAT_VERSION,
            },
        )
        existed, format_ok, _, current_version, current_format = row[:5]
        version, created_at, updated_at, extra = row[5:]
        if not format_ok:
            raise MemoryFormatUnsupportedError(
                scope, path, f"neosian_format {current_format} is not supported"
            )
        if version is None:
            if not existed:
                raise MemoryConflictError(
                    scope,
                    path,
                    "document_absent",
                    expected_version=expected_version,
                )
            raise MemoryConflictError(
                scope,
                path,
                "version_mismatch",
                expected_version=expected_version,
                actual_version=current_version,
            )
        return memory_document(
            scope,
            path,
            (
                content,
                version,
                created_at,
                updated_at,
                actor,
                False,
                MEMORY_FORMAT_VERSION,
                extra,
            ),
        )

    async def delete(self, scope: str, path: str, *, actor: str | None = None) -> bool:
        scope = parse_scope(scope)
        validate_document_path(path)
        row = await self._pool.fetch_one_retry(
            self._sql.delete_document,
            {
                "scope": scope,
                "path": path,
                "actor": actor,
                "now": aware_now(self._clock),
                "format": MEMORY_FORMAT_VERSION,
            },
        )
        existed, format_ok, current_format = row
        if not existed:
            return False
        if not format_ok:
            raise MemoryFormatUnsupportedError(
                scope, path, f"neosian_format {current_format} is not supported"
            )
        return True

    async def rename(
        self, scope: str, src: str, dst: str, *, actor: str | None = None
    ) -> MemoryDocument:
        scope = parse_scope(scope)
        validate_document_path(src)
        validate_document_path(dst)
        row = await self._pool.fetch_one_retry(
            self._sql.rename_document,
            {
                "scope": scope,
                "src": src,
                "dst": dst,
                "actor": actor,
                "now": aware_now(self._clock),
                "format": MEMORY_FORMAT_VERSION,
            },
        )
        src_exists, dst_exists, format_ok, current_format = row[:4]
        content, version, created_at, updated_at, redacted, extra = row[4:]
        if not src_exists:
            raise MemoryDocumentNotFoundError(scope, src)
        if dst_exists:
            raise MemoryConflictError(scope, dst, "destination_exists")
        if not format_ok:
            raise MemoryFormatUnsupportedError(
                scope, src, f"neosian_format {current_format} is not supported"
            )
        return memory_document(
            scope,
            dst,
            (
                content,
                version,
                created_at,
                updated_at,
                actor,
                redacted,
                MEMORY_FORMAT_VERSION,
                extra,
            ),
        )

    async def list_documents(
        self, scope: str, *, prefix: str = ""
    ) -> tuple[MemoryEntry, ...]:
        scope = parse_scope(scope)
        rows = await self._pool.fetch(
            self._sql.list_documents,
            {"scope": scope, "pattern": like_prefix(prefix)},
        )
        return tuple(memory_entry(scope, row) for row in rows)

    async def versions(
        self, scope: str, path: str, *, limit: int = 50
    ) -> tuple[MemoryVersion, ...]:
        scope = parse_scope(scope)
        validate_document_path(path)
        if limit < 0:
            raise ValueError("limit must be >= 0")
        rows = await self._pool.fetch(
            self._sql.read_versions,
            {"scope": scope, "path": path, "limit": limit},
        )
        return tuple(memory_version(scope, row) for row in rows)

    async def redact(
        self, scope: str, *, path: str | None = None, actor: str | None = None
    ) -> int:
        scope = parse_scope(scope)
        if path is not None:
            validate_document_path(path)
        row = await self._pool.fetch_one_retry(
            self._sql.redact,
            {
                "scope": scope,
                "path": path,
                "actor": actor,
                "now": aware_now(self._clock),
            },
        )
        return int(row[0])

    async def history(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryVersion, ...]:
        scope = parse_scope(scope)
        since_window(since, limit)
        rows = await self._pool.fetch(
            self._sql.read_history, {"scope": scope, "since": since, "limit": limit}
        )
        return tuple(memory_version(scope, row) for row in rows)

    async def redactions(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryRedaction, ...]:
        scope = parse_scope(scope)
        since_window(since, limit)
        rows = await self._pool.fetch(
            self._sql.read_redactions, {"scope": scope, "since": since, "limit": limit}
        )
        return tuple(memory_redaction(row) for row in rows)
