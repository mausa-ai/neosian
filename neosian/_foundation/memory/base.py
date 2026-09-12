"""The MemoryStore ABC — the host-facing storage contract (DESIGN §8).

Seven constraints keep this host-implementable (C1–C7): async signatures
with zero I/O ownership (no __init__, no connect/commit/DDL; methods must
be safe inside a caller-owned transaction and must not assume durability
on return); read-your-writes within a task; scope-wide redact; tz-aware
UTC datetimes; per-document monotonic versions from 1 with full content
on version rows; a refused-when-newer format marker; and no policy — no
mounts, no read-only enforcement, no path semantics.

Cross-implementation invariants (pinned by `testing.MemoryStoreContract`):

- `read` returns None for never-written or deleted paths; redacted
  documents read back with empty content and `redacted=True`.
- Every mutation (write/delete/rename) appends exactly one version row
  per path touched; `redact` appends none — version counts survive it.
- Versions are per-document, monotonic from 1, never reused: a delete
  consumes the next number, a re-create continues from the maximum ever
  issued for that path (and resets `created_at`).
- `versions()` is newest-first; `limit` takes the most recent N;
  `limit=0` and unknown paths yield `()`; the newest row mirrors the
  live document unless its action is "deleted".
- A write with identical content still bumps the version.
- `rename` appends "deleted" at src and "created" at dst (dst continues
  its own history); missing src raises MemoryDocumentNotFoundError;
  an occupied dst — src == dst included — raises MemoryConflictError,
  never a silent overwrite. Checked in that order.
- `redact` clears content on the current document and every version row,
  preserves paths/versions/timestamps/actor, deletes nothing, leaves
  `updated_at` untouched, and returns the count of distinct paths
  matched — idempotent in effect and in return value. `redacted` is
  per-state, never sticky: a later write yields `redacted=False`.
- `list_documents` returns current documents only (deleted excluded,
  redacted included), sorted by path ascending; `prefix` is a plain
  string prefix — never validated, never a raiser.
- Every method validates scope then path, raising
  MemoryScopeInvalidError / MemoryPathInvalidError.
- `expected_version=` raises MemoryConflictError on mismatch (reason
  "version_mismatch") and on an absent document ("document_absent"), and
  accepts a *matching* version — on **every** store (NQ2, MC-14).
  `supports_optimistic_concurrency` declares whether that check is
  race-safe across workers, never whether it happens: FileStore arbitrates
  in-process and says False because files cannot arbitrate between
  processes. A store that drops the keyword fails the kit.

**Reserved for a 1.x minor** (NQ2, ledger #230) — neosian will not claim
this name for anything else, so a host may implement it early:

    async def search(
        self, scope: str, query: str, *, limit: int | None = None
    ) -> tuple[MemoryEntry, ...]: ...
        # Documents whose content matches `query`, best first.

DESIGN §8's activation criteria were checked at NQ2 and recorded unmet
(2026-09-12): neither leg has happened — no harness evidence at scale
that the budgeted index plus `view` paging misses facts the store holds,
and no deployment asking. So the Postgres `tsvector` + GIN column stays
a dormant operator escape with no API surface, and activation is an
ECOSYSTEM §12 session-pair, never a quiet method on a reference store.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from datetime import datetime

    from neosian._foundation.memory.types import (
        MemoryDocument,
        MemoryEntry,
        MemoryRedaction,
        MemoryVersion,
    )


class MemoryStore(ABC):
    """Abstract async memory store over (scope, path) → document.

    `actor` is the writing conversation_id — opaque, recorded on version
    rows for audit, never interpreted.
    """

    # True only where a version check is race-safe across workers
    # (PostgresStore, N3). FileStore stays False: it checks in-process,
    # but files cannot arbitrate between processes.
    supports_optimistic_concurrency: ClassVar[bool] = False

    @abstractmethod
    async def read(self, scope: str, path: str) -> MemoryDocument | None:
        """Return the current document, or None if absent or deleted."""

    @abstractmethod
    async def write(
        self,
        scope: str,
        path: str,
        content: str,
        *,
        actor: str | None = None,
        expected_version: int | None = None,
    ) -> MemoryDocument:
        """Create or update a document, appending one version row."""

    @abstractmethod
    async def delete(self, scope: str, path: str, *, actor: str | None = None) -> bool:
        """Delete the current document; False (and no row) if absent."""

    @abstractmethod
    async def rename(
        self, scope: str, src: str, dst: str, *, actor: str | None = None
    ) -> MemoryDocument:
        """Move a document to a new path; both paths gain a version row."""

    @abstractmethod
    async def list_documents(
        self, scope: str, *, prefix: str = ""
    ) -> tuple[MemoryEntry, ...]:
        """List current documents in a scope, sorted by path."""

    @abstractmethod
    async def versions(
        self, scope: str, path: str, *, limit: int = 50
    ) -> tuple[MemoryVersion, ...]:
        """Return the most recent `limit` version rows, newest first."""

    @abstractmethod
    async def redact(
        self, scope: str, *, path: str | None = None, actor: str | None = None
    ) -> int:
        """Clear content everywhere for one path — or the whole scope
        when `path=None` — preserving the audit skeleton (C3)."""

    # The ledger's reads (NL, DESIGN §20) — additive since v0.83.

    @abstractmethod
    async def history(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryVersion, ...]:
        """Every version row in the scope across every path ever written,
        deleted paths included, newest first (`created_at` desc, then
        path, then version desc). `since` is inclusive and must be
        tz-aware (C4; naive is a `ValueError`); `limit=None` is all."""

    @abstractmethod
    async def redactions(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryRedaction, ...]:
        """The scope's erasure trail, newest first; `since`/`limit` as
        `history`. Redaction appends no version row (C3) — this is the
        only place an erasure is visible as an act."""
