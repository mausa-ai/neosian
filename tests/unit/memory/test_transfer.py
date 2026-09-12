"""`transfer` — a store moves whole, FileStore ⇄ FileStore (NC4, §26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.portable import Portable, UnitReport
from neosian._foundation.memory.transfer import archive_scope, transfer
from neosian._foundation.shared.exceptions import (
    ConversationConflictError,
    ConversationIdInvalidError,
    MemoryConflictError,
    MemoryScopeInvalidError,
)
from tests.support.clock import ManualClock

from .mobility import (
    CONVERSATIONS,
    SCOPES,
    assert_indistinguishable,
    assert_numbering_continues,
    seed,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def source(tmp_path: Path, manual_clock: ManualClock) -> FileStore:
    store = FileStore(tmp_path / "source", clock=manual_clock)
    await seed(store, plant_root=store._root)  # noqa: SLF001 — the substrate hook
    return store


@pytest.fixture
def target(tmp_path: Path, manual_clock: ManualClock) -> FileStore:
    return FileStore(tmp_path / "target", clock=manual_clock)


class TestTheDoneWhen:
    async def test_file_to_file_is_indistinguishable(
        self, source: FileStore, target: FileStore
    ) -> None:
        report = await transfer(source, target)
        assert [unit.name for unit in report.units] == [*SCOPES, *CONVERSATIONS]
        await assert_indistinguishable(source, target)
        await assert_numbering_continues(target)

    async def test_the_archive_is_a_store_a_second_hop_reads(
        self, source: FileStore, target: FileStore, tmp_path: Path
    ) -> None:
        """Export then import: the directory in between is a FileStore."""
        archive = FileStore(tmp_path / "archive")
        await transfer(source, archive)
        await transfer(archive, target)
        await assert_indistinguishable(source, target)

    async def test_the_report_counts_the_archive(
        self, source: FileStore, target: FileStore
    ) -> None:
        report = await transfer(source, target)
        scope = report.units[0]
        assert scope == UnitReport(
            "scope", SCOPES[0], documents=5, versions=13, redactions=1
        )
        assert report.units[2] == UnitReport(
            "conversation", "conv-a", turns=3, projections=4
        )
        assert report.units[3] == UnitReport("conversation", "conv-b", projections=1)

    async def test_archive_orders_rows_for_the_sidecar(self, source: FileStore) -> None:
        archive = await archive_scope(source, SCOPES[0])
        assert [(r.path, r.version) for r in archive.versions] == sorted(
            (r.path, r.version) for r in archive.versions
        )
        assert [d.path for d in archive.documents] == sorted(
            d.path for d in archive.documents
        )
        assert archive.redactions[0].created_at <= archive.redactions[-1].created_at


class TestNarrowing:
    async def test_named_units_only(self, source: FileStore, target: FileStore) -> None:
        report = await transfer(source, target, scopes=[SCOPES[1]])
        assert [unit.name for unit in report.units] == [SCOPES[1]]
        assert await target.list_documents(SCOPES[0]) == ()
        assert await target.last_turn_number("conv-a") == 0

    async def test_conversations_alone(
        self, source: FileStore, target: FileStore
    ) -> None:
        report = await transfer(
            source, target, conversations=["conv-b", "conv-a", "conv-a"]
        )
        assert [unit.name for unit in report.units] == ["conv-a", "conv-b"]
        assert await target.scopes() == ()

    async def test_an_empty_unit_is_a_zero_row(
        self, source: FileStore, target: FileStore
    ) -> None:
        report = await transfer(
            source, target, scopes=["user:nobody"], conversations=["ghost"]
        )
        assert report.units == (
            UnitReport("scope", "user:nobody"),
            UnitReport("conversation", "ghost"),
        )
        assert await target.scopes() == ()

    async def test_names_are_validated_before_any_io(
        self, source: FileStore, target: FileStore
    ) -> None:
        with pytest.raises(MemoryScopeInvalidError):
            await transfer(source, target, scopes=["not a scope"])
        with pytest.raises(ConversationIdInvalidError):
            await transfer(source, target, conversations=["a/b"])
        assert await target.scopes() == ()


class TestOccupied:
    async def test_preflight_refuses_the_whole_run(
        self, source: FileStore, target: FileStore
    ) -> None:
        await target.append_turn(
            "conv-b", (await source.read_turns("conv-a"))[0].messages
        )
        with pytest.raises(ConversationConflictError) as caught:
            await transfer(source, target)
        assert caught.value.reason == "target_occupied"
        assert (
            await target.scopes() == ()
        )  # the scopes came first and still did not land

    async def test_a_scope_with_only_history_is_occupied(
        self, source: FileStore, target: FileStore
    ) -> None:
        await target.write(SCOPES[0], "gone", "x")
        await target.delete(SCOPES[0], "gone")
        with pytest.raises(MemoryConflictError) as caught:
            await transfer(source, target, scopes=[SCOPES[0]])
        assert caught.value.reason == "target_occupied"
        assert caught.value.path is None
        assert SCOPES[0] in caught.value.message

    async def test_a_second_import_refuses(
        self, source: FileStore, target: FileStore
    ) -> None:
        await transfer(source, target)
        with pytest.raises(MemoryConflictError):
            await transfer(source, target)


class _MemoryOnly:
    """A host store without the protocol: the ABC reads, nothing else."""

    def __init__(self, inner: FileStore) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        if name in ("scopes", "conversations", "restore_scope", "restore_conversation"):
            raise AttributeError(name)
        return getattr(self._inner, name)


MemoryStore.register(_MemoryOnly)
ConversationStore.register(_MemoryOnly)


class TestRefusalByName:
    async def test_a_target_without_the_protocol(
        self, source: FileStore, target: FileStore
    ) -> None:
        with pytest.raises(TypeError, match="_MemoryOnly does not implement Portable"):
            await transfer(source, _MemoryOnly(target))

    async def test_a_source_that_cannot_enumerate(
        self, source: FileStore, target: FileStore
    ) -> None:
        with pytest.raises(TypeError, match="cannot enumerate"):
            await transfer(_MemoryOnly(source), target)

    async def test_export_is_privilege_free_with_names(
        self, source: FileStore, target: FileStore
    ) -> None:
        """Only reads are asked of a named source — a bare store works."""
        reads = _MemoryOnly(source)
        assert not isinstance(reads, Portable)
        await transfer(reads, target, scopes=SCOPES, conversations=CONVERSATIONS)
        await assert_indistinguishable(source, target)
