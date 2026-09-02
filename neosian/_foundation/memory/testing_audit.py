"""The ledger's slice of the conformance kit (NL, DESIGN §20).

`MemoryStoreContract` inherits this; it lives beside the kit because the
kit module sits at the size gate. Same rules: a `store` and a `scope`
fixture, every test keyless.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from neosian._foundation.memory.base import MemoryStore

_asyncio = pytest.mark.asyncio


class LedgerContract:
    """The `history` and `redactions` reads, pinned on every substrate."""

    @_asyncio
    async def test_history_spans_every_path_deleted_ones_included(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "a", "1", actor="w1")
        await store.write(scope, "b", "2", actor="w2")
        await store.delete(scope, "a", actor="w3")
        rows = await store.history(scope)
        assert {(r.path, r.version, r.action) for r in rows} == {
            ("a", 1, "created"),
            ("b", 1, "created"),
            ("a", 2, "deleted"),
        }
        # Newest first; the delete is the newest act on every clock.
        stamps = [r.created_at for r in rows]
        assert stamps == sorted(stamps, reverse=True)
        assert (rows[0].path, rows[0].action) == ("a", "deleted")
        assert all(r.created_at.tzinfo is not None for r in rows)
        assert [r.actor for r in rows if r.path == "b"] == ["w2"]

    @_asyncio
    async def test_history_since_is_inclusive_and_limit_takes_the_newest(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "a", "1")
        await store.write(scope, "a", "2")
        await store.write(scope, "b", "3")
        rows = await store.history(scope)
        newest = rows[0]
        recent = await store.history(scope, since=newest.created_at)
        assert recent and all(r.created_at >= newest.created_at for r in recent)
        assert recent[0] == newest
        assert await store.history(scope, limit=1) == (newest,)
        assert await store.history(scope, limit=0) == ()

    @_asyncio
    async def test_history_of_an_unknown_scope_is_empty(
        self, store: MemoryStore, scope: str
    ) -> None:
        assert await store.history(scope) == ()
        assert await store.redactions(scope) == ()

    @_asyncio
    async def test_history_refuses_a_naive_since(
        self, store: MemoryStore, scope: str
    ) -> None:
        naive = datetime(2026, 9, 2, 12, 0, 0)  # noqa: DTZ001 - the point
        with pytest.raises(ValueError, match="timezone-aware"):
            await store.history(scope, since=naive)
        with pytest.raises(ValueError, match="timezone-aware"):
            await store.redactions(scope, since=naive)
        with pytest.raises(ValueError):
            await store.history(scope, limit=-1)

    @_asyncio
    async def test_redactions_record_the_path_and_the_scope_wide_act(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "a", "1")
        await store.write(scope, "b", "2")
        assert await store.redact(scope, path="a", actor="eraser") == 1
        assert await store.redact(scope, actor="eraser") == 2
        acts = await store.redactions(scope)
        assert [(a.path, a.count, a.actor) for a in acts] == [
            (None, 2, "eraser"),
            ("a", 1, "eraser"),
        ]
        assert all(a.created_at.tzinfo is not None for a in acts)
        assert acts[0].created_at >= acts[1].created_at
        assert await store.redactions(scope, limit=1) == (acts[0],)
        recent = await store.redactions(scope, since=acts[0].created_at)
        assert recent[0] == acts[0]
        # A no-op redaction leaves no act behind.
        assert await store.redact(scope, path="never") == 0
        assert len(await store.redactions(scope)) == 2

    @_asyncio
    async def test_history_keeps_the_skeleton_after_redaction(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "a", "secret", actor="w1")
        await store.redact(scope, path="a")
        (row,) = await store.history(scope)
        assert (row.content, row.redacted, row.actor) == ("", True, "w1")
        assert row.created_at.tzinfo is UTC or row.created_at.utcoffset() is not None
