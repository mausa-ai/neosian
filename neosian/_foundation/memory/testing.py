"""MemoryStoreContract — the shipped conformance kit (DESIGN §8).

Subclass it in your test suite, provide a `store` fixture, and inherit
the cross-implementation tests that keep host stores honest
(ECOSYSTEM §10). Requires pytest and pytest-asyncio; neosian itself
never imports this module at runtime.

    class TestMyStore(MemoryStoreContract):
        @pytest.fixture
        def store(self) -> MyStore: ...

The `store` fixture must be function-scoped, empty and isolated —
`test_store_starts_empty` fails loudly when it leaks state. Override
`plant_raw_document` to enable the two substrate-planting tests
(format refusal, unknown-key preservation); by default they skip.
Two paths differing only in case are never used (case-insensitive
filesystems are a legal substrate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from neosian._foundation.memory.scope import Scope, parse_scope
from neosian._foundation.memory.testing_audit import LedgerContract
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryFormatUnsupportedError,
    MemoryPathInvalidError,
    MemoryScopeInvalidError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from neosian._foundation.memory.base import MemoryStore

_asyncio = pytest.mark.asyncio

_BAD_SCOPES = ("", "user", "user:", "User:1", "user:a:b", "user:1\n", "user:..")
_BAD_PATHS = ("", "/a", "a/", "a//b", ".", "..", "a/../b", "a\\b", "a b")
_ROUND_TRIP = ("", "x", "x\n", "\n", "a\r\nb", "---\ntitle: t\n---\nbody", "café ✓")


class MemoryStoreContract(LedgerContract):
    """Inherit ~25 conformance tests; provide a `store` fixture."""

    @pytest.fixture
    def scope(self) -> Scope:
        """The scope under test; override to exercise another."""
        return parse_scope("user:contract-kit")

    def stamped(self, store: MemoryStore, actor: str) -> str:
        """What the store records for a write made as `actor`. Identity
        everywhere but the daemon, which prefixes its asserted client
        (§20); the Remote subclasses override."""
        del store
        return actor

    async def plant_raw_document(
        self,
        store: MemoryStore,
        scope: str,
        path: str,
        *,
        content: str,
        format_version: int,
        extra: Mapping[str, Any],
    ) -> None:
        """Write a document directly into the substrate, bypassing the
        store. Override per substrate; the default skips its tests."""
        del store, scope, path, content, format_version, extra
        pytest.skip("plant_raw_document not implemented for this substrate")

    # Reads and writes ----------------------------------------------------

    @_asyncio
    async def test_store_starts_empty(self, store: MemoryStore, scope: Scope) -> None:
        assert await store.list_documents(scope) == ()

    @_asyncio
    async def test_supports_optimistic_concurrency_is_declared(
        self, store: MemoryStore
    ) -> None:
        assert isinstance(type(store).supports_optimistic_concurrency, bool)

    @_asyncio
    async def test_read_missing_returns_none(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        assert await store.read(scope, "never-written") is None

    @_asyncio
    async def test_write_creates_at_version_one_and_reads_back(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        written = await store.write(scope, "notes/api", "body", actor="conv-1")
        assert written.version == 1
        assert written.redacted is False
        document = await store.read(scope, "notes/api")
        assert document is not None
        assert document.content == "body"
        assert document.version == 1
        assert document.actor == self.stamped(store, "conv-1")
        assert document.path == "notes/api"
        assert document.scope == scope
        assert [e.path for e in await store.list_documents(scope)] == ["notes/api"]
        assert len(await store.versions(scope, "notes/api")) == 1

    @pytest.mark.parametrize("content", _ROUND_TRIP)
    @_asyncio
    async def test_content_round_trips_byte_exact(
        self, store: MemoryStore, scope: Scope, content: str
    ) -> None:
        await store.write(scope, "doc", content)
        document = await store.read(scope, "doc")
        assert document is not None
        assert document.content == content

    @_asyncio
    async def test_write_empty_content_is_stored_not_redacted(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "empty", "")
        document = await store.read(scope, "empty")
        assert document is not None
        assert document.content == ""
        assert document.redacted is False

    @_asyncio
    async def test_update_bumps_version_and_preserves_created_at(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        first = await store.write(scope, "doc", "v1")
        second = await store.write(scope, "doc", "v2")
        assert second.version == 2
        assert second.created_at == first.created_at
        assert second.updated_at >= first.updated_at

    @_asyncio
    async def test_write_identical_content_still_appends_a_version(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "doc", "same")
        second = await store.write(scope, "doc", "same")
        assert second.version == 2
        assert len(await store.versions(scope, "doc")) == 2

    @_asyncio
    async def test_every_returned_datetime_is_utc_aware(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        document = await store.write(scope, "doc", "x")
        stamps = [document.created_at, document.updated_at]
        for entry in await store.list_documents(scope):
            stamps += [entry.created_at, entry.updated_at]
        stamps += [row.created_at for row in await store.versions(scope, "doc")]
        for stamp in stamps:
            assert stamp.tzinfo is not None, "naive datetime at a seam"
            assert stamp.utcoffset() is not None

    # Delete and version history ------------------------------------------

    @_asyncio
    async def test_delete_removes_document_and_appends_a_deleted_row(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "doc", "content", actor="a1")
        assert await store.delete(scope, "doc", actor="a2") is True
        assert await store.read(scope, "doc") is None
        rows = await store.versions(scope, "doc")
        assert rows[0].action == "deleted"
        assert rows[0].version == 2
        assert rows[0].content == "content"
        assert rows[0].actor == self.stamped(store, "a2")

    @_asyncio
    async def test_delete_missing_returns_false_and_appends_nothing(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        assert await store.delete(scope, "ghost") is False
        assert await store.versions(scope, "ghost") == ()

    @_asyncio
    async def test_recreate_after_delete_continues_version_numbering(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "doc", "v1")
        await store.write(scope, "doc", "v2")
        await store.delete(scope, "doc")  # consumes version 3
        recreated = await store.write(scope, "doc", "again")
        assert recreated.version == 4
        rows = await store.versions(scope, "doc")
        assert [row.version for row in rows] == [4, 3, 2, 1]
        assert rows[0].action == "created"

    @_asyncio
    async def test_versions_are_newest_first_and_mirror_the_document(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        for content in ("v1", "v2", "v3"):
            document = await store.write(scope, "doc", content, actor="conv")
        rows = await store.versions(scope, "doc")
        assert [row.version for row in rows] == [3, 2, 1]
        newest = rows[0]
        assert newest.version == document.version
        assert newest.content == document.content == "v3"
        assert newest.action == "modified"
        assert rows[-1].action == "created"
        assert all(row.content == f"v{row.version}" for row in rows)
        assert all(row.actor == self.stamped(store, "conv") for row in rows)

    @_asyncio
    async def test_versions_respect_limit_and_unknown_path_is_empty(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        for content in ("v1", "v2", "v3"):
            await store.write(scope, "doc", content)
        limited = await store.versions(scope, "doc", limit=2)
        assert [row.version for row in limited] == [3, 2]
        assert await store.versions(scope, "doc", limit=0) == ()
        assert await store.versions(scope, "unknown") == ()

    # Listing --------------------------------------------------------------

    @_asyncio
    async def test_list_documents_is_sorted_and_excludes_deleted(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        for path in ("zebra", "alpha", "gone"):
            await store.write(scope, path, "x")
        await store.delete(scope, "gone")
        entries = await store.list_documents(scope)
        assert [entry.path for entry in entries] == ["alpha", "zebra"]

    @_asyncio
    async def test_list_prefix_is_a_plain_string_prefix(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        for path in ("notes/a", "notes/b", "notebook", "other"):
            await store.write(scope, path, "x")
        matched = await store.list_documents(scope, prefix="note")
        assert [e.path for e in matched] == ["notebook", "notes/a", "notes/b"]
        assert await store.list_documents(scope, prefix="no match //") == ()

    # Rename ---------------------------------------------------------------

    @_asyncio
    async def test_rename_moves_content_and_leaves_src_absent(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "old", "body")
        moved = await store.rename(scope, "old", "new", actor="conv-r")
        assert moved.path == "new"
        assert moved.content == "body"
        assert await store.read(scope, "old") is None
        document = await store.read(scope, "new")
        assert document is not None
        assert document.content == "body"

    @_asyncio
    async def test_rename_appends_deleted_at_src_and_created_at_dst(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "old", "body")
        await store.rename(scope, "old", "new")
        src_rows = await store.versions(scope, "old")
        dst_rows = await store.versions(scope, "new")
        assert src_rows[0].action == "deleted"
        assert dst_rows[0].action == "created"
        assert dst_rows[0].version == 1  # a fresh path starts its own history
        assert dst_rows[0].content == "body"

    @_asyncio
    async def test_rename_missing_src_raises_document_not_found(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        with pytest.raises(MemoryDocumentNotFoundError):
            await store.rename(scope, "ghost", "anywhere")

    @_asyncio
    async def test_rename_onto_an_occupied_destination_raises_conflict(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "a", "x")
        await store.write(scope, "b", "y")
        with pytest.raises(MemoryConflictError):
            await store.rename(scope, "a", "b")
        with pytest.raises(MemoryConflictError):
            await store.rename(scope, "a", "a")  # src == dst is occupied too
        document = await store.read(scope, "b")
        assert document is not None
        assert document.content == "y"  # never a silent overwrite

    # Redaction (C3) -------------------------------------------------------

    @_asyncio
    async def test_redact_path_clears_content_and_preserves_the_skeleton(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "doc", "secret v1", actor="conv")
        await store.write(scope, "doc", "secret v2", actor="conv")
        before = await store.versions(scope, "doc")
        assert await store.redact(scope, path="doc") == 1
        document = await store.read(scope, "doc")
        assert document is not None
        assert document.content == ""
        assert document.redacted is True
        assert document.version == 2
        after = await store.versions(scope, "doc")
        assert len(after) == len(before)  # C3: version counts unchanged
        for cleared, original in zip(after, before, strict=True):
            assert cleared.content == ""
            assert cleared.redacted is True
            assert cleared.version == original.version
            assert cleared.action == original.action
            assert cleared.actor == original.actor
            assert cleared.created_at == original.created_at
        listed = await store.list_documents(scope)
        assert listed[0].redacted is True

    @_asyncio
    async def test_redact_does_not_bump_updated_at(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        written = await store.write(scope, "doc", "secret")
        await store.redact(scope, path="doc")
        document = await store.read(scope, "doc")
        assert document is not None
        assert document.updated_at == written.updated_at

    @_asyncio
    async def test_redact_scope_wide_covers_deleted_paths_with_a_stable_count(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "alive", "secret")
        await store.write(scope, "dead", "secret")
        await store.delete(scope, "dead")
        assert await store.redact(scope) == 2
        assert await store.redact(scope) == 2  # idempotent, same count
        assert await store.read(scope, "dead") is None  # still deleted
        dead_rows = await store.versions(scope, "dead")
        assert all(row.content == "" and row.redacted for row in dead_rows)

    @_asyncio
    async def test_redact_of_nothing_returns_zero(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        assert await store.redact(scope) == 0
        assert await store.redact(scope, path="ghost") == 0

    @_asyncio
    async def test_write_after_redact_produces_an_unredacted_document(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "doc", "secret")
        await store.redact(scope, path="doc")
        fresh = await store.write(scope, "doc", "new life")
        assert fresh.redacted is False
        assert fresh.content == "new life"
        rows = await store.versions(scope, "doc")
        assert rows[0].redacted is False  # the new row
        assert rows[-1].redacted is True  # history stays redacted

    # Validation -----------------------------------------------------------

    @pytest.mark.parametrize("bad_scope", _BAD_SCOPES)
    @_asyncio
    async def test_invalid_scopes_are_rejected_on_every_method(
        self, store: MemoryStore, bad_scope: str
    ) -> None:
        with pytest.raises(MemoryScopeInvalidError):
            await store.read(bad_scope, "doc")
        with pytest.raises(MemoryScopeInvalidError):
            await store.write(bad_scope, "doc", "x")
        with pytest.raises(MemoryScopeInvalidError):
            await store.delete(bad_scope, "doc")
        with pytest.raises(MemoryScopeInvalidError):
            await store.rename(bad_scope, "a", "b")
        with pytest.raises(MemoryScopeInvalidError):
            await store.list_documents(bad_scope)
        with pytest.raises(MemoryScopeInvalidError):
            await store.versions(bad_scope, "doc")
        with pytest.raises(MemoryScopeInvalidError):
            await store.redact(bad_scope)
        # Scope is validated before path: both invalid → the scope error.
        with pytest.raises(MemoryScopeInvalidError):
            await store.write(bad_scope, "a//bad", "x")

    @pytest.mark.parametrize("bad_path", _BAD_PATHS)
    @_asyncio
    async def test_invalid_paths_are_rejected_on_every_method(
        self, store: MemoryStore, scope: Scope, bad_path: str
    ) -> None:
        with pytest.raises(MemoryPathInvalidError):
            await store.read(scope, bad_path)
        with pytest.raises(MemoryPathInvalidError):
            await store.write(scope, bad_path, "x")
        with pytest.raises(MemoryPathInvalidError):
            await store.delete(scope, bad_path)
        with pytest.raises(MemoryPathInvalidError):
            await store.rename(scope, bad_path, "ok")
        with pytest.raises(MemoryPathInvalidError):
            await store.rename(scope, "ok", bad_path)
        with pytest.raises(MemoryPathInvalidError):
            await store.versions(scope, bad_path)
        with pytest.raises(MemoryPathInvalidError):
            await store.redact(scope, path=bad_path)

    # Optimistic concurrency ----------------------------------------------

    @_asyncio
    async def test_matching_expected_version_is_accepted(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await store.write(scope, "doc", "v1")
        document = await store.write(scope, "doc", "v2", expected_version=1)
        assert document.version == 2

    @_asyncio
    async def test_expected_version_mismatch_raises_conflict(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        if not type(store).supports_optimistic_concurrency:
            pytest.skip("store does not declare optimistic concurrency")
        await store.write(scope, "doc", "v1")
        with pytest.raises(MemoryConflictError):
            await store.write(scope, "doc", "v2", expected_version=99)
        with pytest.raises(MemoryConflictError):
            await store.write(scope, "absent", "x", expected_version=1)

    # Format discipline (C6) ----------------------------------------------

    @_asyncio
    async def test_unknown_keys_survive_a_write(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await self.plant_raw_document(
            store,
            scope,
            "doc",
            content="planted",
            format_version=1,
            extra={"custom": "kept", "count": 3, "flag": True},
        )
        await store.write(scope, "doc", "updated")
        document = await store.read(scope, "doc")
        assert document is not None
        assert document.extra["custom"] == "kept"
        assert document.extra["count"] == 3
        assert document.extra["flag"] is True

    @_asyncio
    async def test_newer_format_version_is_refused(
        self, store: MemoryStore, scope: Scope
    ) -> None:
        await self.plant_raw_document(
            store, scope, "doc", content="future", format_version=999, extra={}
        )
        with pytest.raises(MemoryFormatUnsupportedError):
            await store.read(scope, "doc")
