"""The concurrency slice of the conformance kit (NQ2, MC-14).

`MemoryStoreContract` inherits this; it lives beside the kit because the
kit module sits at the size gate. Same rules: a `store` and a `scope`
fixture, every test keyless.

Two things the kit did not ask for before, and a store could therefore
ignore `expected_version` and still pass:

- **A mismatch raises on every store.** `supports_optimistic_concurrency`
  declares whether the check is race-safe *across workers*, never whether
  the check exists (DESIGN §8). A store that drops the keyword now fails
  here instead of passing on "a matching version is accepted", which
  ignoring also satisfies.
- **C2, read-your-writes within a task**, is a named test rather than an
  invariant every other test leans on silently.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from neosian._foundation.shared.exceptions import MemoryConflictError

if TYPE_CHECKING:
    from neosian._foundation.memory.base import MemoryStore

_asyncio = pytest.mark.asyncio


class ConcurrencyContract:
    """`expected_version` and C2, pinned on every substrate."""

    @_asyncio
    async def test_matching_expected_version_is_accepted(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "doc", "v1")
        document = await store.write(scope, "doc", "v2", expected_version=1)
        assert document.version == 2

    @_asyncio
    async def test_expected_version_mismatch_raises_conflict(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "doc", "v1")
        with pytest.raises(MemoryConflictError) as caught:
            await store.write(scope, "doc", "v2", expected_version=99)
        assert caught.value.reason == "version_mismatch"
        assert (await store.read(scope, "doc")).content == "v1"  # type: ignore[union-attr]

    @_asyncio
    async def test_expected_version_on_an_absent_document_raises(
        self, store: MemoryStore, scope: str
    ) -> None:
        with pytest.raises(MemoryConflictError) as caught:
            await store.write(scope, "absent", "x", expected_version=1)
        assert caught.value.reason == "document_absent"
        assert (await store.read(scope, "absent")) is None

    @_asyncio
    async def test_only_one_of_two_racing_writers_wins(
        self, store: MemoryStore, scope: str
    ) -> None:
        """Both claim version 1; the loser is told, never merged.

        A store that ignores `expected_version` lands two writes here and
        fails. Whether the arbitration is race-safe across *workers* is
        what `supports_optimistic_concurrency` declares — this asks only
        that the store arbitrate at all.
        """
        await store.write(scope, "doc", "v1")
        outcomes = await asyncio.gather(
            store.write(scope, "doc", "a", expected_version=1),
            store.write(scope, "doc", "b", expected_version=1),
            return_exceptions=True,
        )
        conflicts = [o for o in outcomes if isinstance(o, MemoryConflictError)]
        assert len(conflicts) == 1, outcomes
        assert (await store.read(scope, "doc")).version == 2  # type: ignore[union-attr]

    # C2 — read-your-writes within a task ---------------------------------

    @_asyncio
    async def test_a_write_is_visible_to_the_next_read(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "c2", "first")
        assert (await store.read(scope, "c2")).content == "first"  # type: ignore[union-attr]
        await store.write(scope, "c2", "second")
        document = await store.read(scope, "c2")
        assert document is not None
        assert (document.content, document.version) == ("second", 2)
        assert [e.path for e in await store.list_documents(scope)] == ["c2"]
        assert (await store.versions(scope, "c2"))[0].version == 2

    @_asyncio
    async def test_a_delete_is_visible_to_the_next_read(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "c2", "first")
        assert await store.delete(scope, "c2") is True
        assert (await store.read(scope, "c2")) is None
        assert await store.list_documents(scope) == ()

    @_asyncio
    async def test_a_rename_and_a_redact_are_visible_to_the_next_read(
        self, store: MemoryStore, scope: str
    ) -> None:
        await store.write(scope, "c2", "first")
        await store.rename(scope, "c2", "moved")
        assert (await store.read(scope, "c2")) is None
        assert (await store.read(scope, "moved")).content == "first"  # type: ignore[union-attr]
        assert await store.redact(scope, path="moved") == 1
        document = await store.read(scope, "moved")
        assert document is not None
        assert (document.content, document.redacted) == ("", True)
