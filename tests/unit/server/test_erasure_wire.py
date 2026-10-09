"""The eraser over the wire (N8, DESIGN §38): the codec, the handshake
flag read by `connect`, a backend without the protocol refused by name
on both ends, and an export from such a backend carrying no trail."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from neosian._foundation.conversation.types import (
    ConversationRedaction,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.transfer import transfer
from neosian._foundation.server.app import build_app
from neosian._foundation.server.remote import RemoteStore
from neosian._foundation.server.wire import (
    decode_turn,
    decode_turn_redaction,
    encode_turn,
    encode_turn_redaction,
)
from neosian._foundation.shared.exceptions import ConfigurationError
from tests.support.clock import ManualClock

from .conftest import BASE_URL, TOKEN

_STAMP = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class TestTheCodec:
    def test_a_skeleton_turn_round_trips(self) -> None:
        turn = ConversationTurn(
            conversation_id="c", turn=3, messages=(), created_at=_STAMP, redacted=True
        )
        encoded = encode_turn(turn)
        assert (encoded["messages"], encoded["redacted"]) == ([], True)
        assert decode_turn(encoded) == turn
        # A 5 client's turn names no flag: absent reads as false.
        plain = encode_turn(
            ConversationTurn(
                conversation_id="c",
                turn=1,
                messages=(Message(role=Role.USER, content="x"),),
                created_at=_STAMP,
            )
        )
        del plain["redacted"]
        assert decode_turn(plain).redacted is False

    def test_an_act_round_trips_and_is_typed_at_the_door(self) -> None:
        act = ConversationRedaction(
            conversation_id="c", turns=(1, 3), actor="ops", created_at=_STAMP
        )
        encoded = encode_turn_redaction(act)
        assert encoded == {
            "conversation_id": "c",
            "turns": [1, 3],
            "actor": "ops",
            "created_at": "2026-10-10T12:00:00Z",
        }
        assert decode_turn_redaction(encoded) == act
        for bad in ({**encoded, "turns": []}, {**encoded, "turns": [0]}):
            with pytest.raises(ValueError, match="turn numbers from 1"):
                decode_turn_redaction(bad)
        with pytest.raises(ValueError, match="'turns'"):
            decode_turn_redaction({**encoded, "turns": ["1"]})


class Plain(FileStore):
    """A backend that keeps every turn forever: no `Erasable`."""

    redact_turns = None  # type: ignore[assignment]


@pytest.fixture
async def plain(tmp_path: Path) -> AsyncIterator[tuple[Plain, RemoteStore]]:
    backing = Plain(tmp_path / "plain", clock=ManualClock())
    app = await build_app(backing, token=TOKEN)
    remote = await RemoteStore.connect(
        BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app)
    )
    try:
        yield backing, remote
    finally:
        await remote.aclose()


class TestABackendThatCannotErase:
    async def test_the_handshake_says_so(
        self, plain: tuple[Plain, RemoteStore]
    ) -> None:
        _, remote = plain
        assert (await remote.capabilities())["erasable"] is False

    async def test_the_client_refuses_before_any_request(
        self, plain: tuple[Plain, RemoteStore]
    ) -> None:
        _, remote = plain
        with pytest.raises(ConfigurationError, match="does not implement Erasable"):
            await remote.redact_turns("c1")
        with pytest.raises(ConfigurationError, match="does not implement Erasable"):
            await remote.turn_redactions()

    async def test_the_server_refuses_by_name(
        self, plain: tuple[Plain, RemoteStore]
    ) -> None:
        _, remote = plain
        with pytest.raises(ConfigurationError, match="Plain does not implement"):
            await remote._call(  # noqa: SLF001 — the raw route, the flag ignored
                "conversation/redact_turns", {"conversation_id": "c1"}
            )
        await remote.append_turn("c1", (Message(role=Role.USER, content="served"),))

    async def test_an_export_from_it_carries_no_trail(
        self, plain: tuple[Plain, RemoteStore], tmp_path: Path
    ) -> None:
        _, remote = plain
        await remote.append_turn("c1", (Message(role=Role.USER, content="x"),))
        target = FileStore(tmp_path / "target")
        report = await transfer(remote, target, conversations=["c1"])
        assert (report.units[0].turns, report.units[0].redactions) == (1, 0)
        assert await target.turn_redactions() == ()


class TestTheFlagOnConnect:
    async def test_connect_reads_erasable(
        self, tmp_path: Path, remote_over_file: object
    ) -> None:
        del tmp_path
        remote = remote_over_file.remote  # type: ignore[attr-defined]
        assert remote._erasable is True  # noqa: SLF001 — the handshake's word
        await remote.append_turn("c1", (Message(role=Role.USER, content="hunter2"),))
        assert await remote.redact_turns("c1") == 1
        (turn,) = await remote.read_turns("c1")
        assert (turn.messages, turn.redacted) == ((), True)
