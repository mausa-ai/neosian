"""Store mobility over the wire (NC4, §26): the archive codec, the four
`store/*` routes, verbatim restores without the actor stamp, and the
done-when on every keyless pair that crosses the daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.transfer import (
    archive_conversation,
    archive_scope,
    transfer,
)
from neosian._foundation.server.app import build_app
from neosian._foundation.server.remote import RemoteStore
from neosian._foundation.server.wire_archive import (
    decode_conversation_archive,
    decode_scope_archive,
    encode_conversation_archive,
    encode_scope_archive,
)
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    ConversationConflictError,
    MemoryConflictError,
)
from tests.unit.memory.mobility import (
    CONVERSATIONS,
    SCOPES,
    assert_indistinguishable,
    assert_numbering_continues,
    seed,
)

from .conftest import BASE_URL, TOKEN, RemoteOverFile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tests.unit.memory.conftest import ManualClock

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded(tmp_path: Path, manual_clock: ManualClock) -> FileStore:
    store = FileStore(tmp_path / "seeded", clock=manual_clock)
    await seed(store, plant_root=store._root)  # noqa: SLF001 — the substrate hook
    return store


@pytest.fixture
async def second_remote(
    tmp_path: Path, manual_clock: ManualClock
) -> AsyncIterator[RemoteOverFile]:
    root = tmp_path / "second"
    backing = FileStore(root, clock=manual_clock)
    app = await build_app(backing, token=TOKEN)
    remote = await RemoteStore.connect(
        BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app)
    )
    try:
        yield RemoteOverFile(backing, root, remote)
    finally:
        await remote.aclose()


class TestCodec:
    async def test_archives_round_trip(self, seeded: FileStore) -> None:
        scope = await archive_scope(seeded, SCOPES[0])
        assert decode_scope_archive(encode_scope_archive(scope)) == scope
        conversation = await archive_conversation(seeded, CONVERSATIONS[0])
        encoded = encode_conversation_archive(conversation)
        assert decode_conversation_archive(encoded) == conversation


class TestThePairs:
    async def test_file_to_remote(
        self, seeded: FileStore, remote_over_file: RemoteOverFile
    ) -> None:
        await transfer(seeded, remote_over_file.remote)
        await assert_indistinguishable(seeded, remote_over_file.remote)
        await assert_indistinguishable(seeded, remote_over_file.backing)
        await assert_numbering_continues(remote_over_file.remote)

    async def test_remote_to_file(
        self, seeded: FileStore, remote_over_file: RemoteOverFile, tmp_path: Path
    ) -> None:
        await transfer(seeded, remote_over_file.backing)
        target = FileStore(tmp_path / "back")
        report = await transfer(remote_over_file.remote, target)
        assert [u.name for u in report.units] == [*SCOPES, *CONVERSATIONS]
        await assert_indistinguishable(seeded, target)

    async def test_remote_to_remote(
        self,
        seeded: FileStore,
        remote_over_file: RemoteOverFile,
        second_remote: RemoteOverFile,
    ) -> None:
        await transfer(seeded, remote_over_file.remote)
        await transfer(remote_over_file.remote, second_remote.remote)
        await assert_indistinguishable(seeded, second_remote.backing)

    async def test_restored_actors_are_verbatim(
        self, seeded: FileStore, remote_over_file: RemoteOverFile
    ) -> None:
        """No `client:default/` prefix — the archive's actors are the truth."""
        await transfer(seeded, remote_over_file.remote, scopes=[SCOPES[0]])
        actors = {row.actor for row in await remote_over_file.remote.history(SCOPES[0])}
        assert "conv:x#1" in actors
        assert not any(a.startswith("client:") for a in actors if a)
        written = await remote_over_file.remote.write(
            SCOPES[0], "fresh", "x", actor="me"
        )
        assert written.actor == "client:default/me"  # ordinary writes still stamp


class TestTheRoutes:
    async def test_enumeration_after_writes(
        self, remote_over_file: RemoteOverFile
    ) -> None:
        remote = remote_over_file.remote
        assert await remote.scopes() == ()
        await remote.write("user:z", "a", "1")
        await remote.write("user:a", "a", "1")
        assert await remote.scopes() == ("user:a", "user:z")
        assert await remote.conversations() == ()

    async def test_occupied_crosses_as_the_typed_conflicts(
        self, seeded: FileStore, remote_over_file: RemoteOverFile
    ) -> None:
        await transfer(seeded, remote_over_file.remote)
        with pytest.raises(MemoryConflictError) as memory:
            await remote_over_file.remote.restore_scope(
                await archive_scope(seeded, SCOPES[0])
            )
        assert memory.value.reason == "target_occupied"
        assert memory.value.path is None
        with pytest.raises(ConversationConflictError) as conversation:
            await remote_over_file.remote.restore_conversation(
                await archive_conversation(seeded, CONVERSATIONS[0])
            )
        assert conversation.value.reason == "target_occupied"

    async def test_a_backend_without_the_protocol(self, tmp_path: Path) -> None:
        class Plain(FileStore):
            scopes = None  # type: ignore[assignment]

        app = await build_app(Plain(tmp_path / "plain"), token=TOKEN)
        remote = await RemoteStore.connect(
            BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app)
        )
        try:
            with pytest.raises(ConfigurationError, match="Plain does not implement"):
                await remote.scopes()
            await remote.write("user:a", "a", "still serves the ABCs")
        finally:
            await remote.aclose()
