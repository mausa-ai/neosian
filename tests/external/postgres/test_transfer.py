"""Store mobility with Postgres legs (NC4, §26.5): file ⇄ pg, pg ⇄ the
daemon over pg, the occupied gate, the NUL limit, and the parent row."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from neosian import PostgresStore, RemoteStore
from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.portable import ConversationArchive
from neosian._foundation.memory.transfer import archive_scope, transfer
from neosian._foundation.server.app import build_app
from neosian._foundation.shared.exceptions import MemoryConflictError
from tests.external.postgres.conftest import ManualClock
from tests.unit.memory.mobility import (
    CONVERSATIONS,
    SCOPES,
    assert_indistinguishable,
    assert_numbering_continues,
    seed,
)

pytestmark = [pytest.mark.external_postgres, pytest.mark.asyncio]

_TOKEN = "transfer-token"


@pytest.fixture
async def seeded(tmp_path: Path, manual_clock: ManualClock) -> FileStore:
    files = FileStore(tmp_path / "seeded", clock=manual_clock)
    await seed(files, plant_root=files._root)  # noqa: SLF001 — the substrate hook
    return files


@pytest.fixture
async def remote(store: PostgresStore) -> AsyncIterator[RemoteStore]:
    app = await build_app(store, token=_TOKEN)
    client = await RemoteStore.connect(
        "http://state-process", token=_TOKEN, transport=httpx.ASGITransport(app=app)
    )
    try:
        yield client
    finally:
        await client.aclose()


class TestThePairs:
    async def test_file_to_postgres_and_back(
        self, seeded: FileStore, store: PostgresStore, tmp_path: Path
    ) -> None:
        report = await transfer(seeded, store)
        assert [u.name for u in report.units] == [*SCOPES, *CONVERSATIONS]
        await assert_indistinguishable(seeded, store)
        back = FileStore(tmp_path / "back")
        await transfer(store, back)
        await assert_indistinguishable(seeded, back)
        await assert_numbering_continues(store)

    async def test_through_the_daemon_over_postgres(
        self,
        seeded: FileStore,
        store: PostgresStore,
        remote: RemoteStore,
        tmp_path: Path,
    ) -> None:
        assert type(remote).supports_optimistic_concurrency is True
        await transfer(seeded, remote)
        await assert_indistinguishable(seeded, store)
        back = FileStore(tmp_path / "back")
        await transfer(remote, back)
        await assert_indistinguishable(seeded, back)


class TestTheGate:
    async def test_occupied_refuses_and_a_second_import_too(
        self, seeded: FileStore, store: PostgresStore
    ) -> None:
        await transfer(seeded, store, scopes=[SCOPES[0]])
        with pytest.raises(MemoryConflictError) as caught:
            await transfer(seeded, store, scopes=[SCOPES[0]])
        assert caught.value.reason == "target_occupied"

    async def test_a_nul_row_fails_the_unit_whole(
        self, store: PostgresStore, tmp_path: Path
    ) -> None:
        files = FileStore(tmp_path / "nul")
        await files.write("user:n", "clean", "fine")
        await files.write("user:n", "dirty", "a\x00b")
        with pytest.raises(Exception, match="u0000|0x00|NUL"):
            await store.restore_scope(await archive_scope(files, "user:n"))
        assert await store.scopes() == ()  # nothing of the unit landed

    async def test_a_projection_only_conversation_restores(
        self, store: PostgresStore
    ) -> None:
        entry = ConversationProjection(turn=1, kind="log", text="x")
        await store.restore_conversation(ConversationArchive("lonely", (), (entry,)))
        assert await store.conversations() == ("lonely",)
        assert await store.read_projections("lonely") == (entry,)
        assert await store.last_turn_number("lonely") == 0
