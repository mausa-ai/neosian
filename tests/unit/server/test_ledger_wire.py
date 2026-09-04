"""The ledger over the wire (NL, DESIGN §20): the redaction codec, the two
routes' typed door, and the version step."""

from datetime import UTC, datetime

import httpx
import pytest

from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.audit import audit
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.types import MemoryRedaction
from neosian._foundation.server.app import build_app
from neosian._foundation.server.wire import (
    WIRE_VERSION,
    decode_redaction,
    encode_redaction,
)

from .conftest import BASE_URL, TOKEN, RemoteOverFile


class TestRedactionCodec:
    def test_round_trips_including_a_scope_wide_act(self) -> None:
        for path in ("notes/a", None):
            act = MemoryRedaction(
                path=path,
                actor="cli:local",
                created_at=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
                count=3,
            )
            encoded = encode_redaction(act)
            assert encoded["created_at"] == "2026-09-02T12:00:00Z"
            assert decode_redaction(encoded) == act

    def test_the_step(self) -> None:
        assert WIRE_VERSION == 3  # NL: two reads + the turn author; NC4: store/*


@pytest.fixture
async def client(tmp_path):  # type: ignore[no-untyped-def]
    app = await build_app(FileStore(tmp_path / "state"), token=TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http:
        yield http


class TestLedgerRoutes:
    async def test_history_and_redactions_answer(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post(
            "/v1/memory/write",
            json={"scope": "user:me", "path": "a", "content": "x", "actor": "w"},
        )
        await client.post("/v1/memory/redact", json={"scope": "user:me", "path": "a"})
        history = await client.post("/v1/memory/history", json={"scope": "user:me"})
        assert history.status_code == 200
        (row,) = history.json()["versions"]
        assert (row["path"], row["redacted"], row["content"]) == ("a", True, "")
        acts = await client.post("/v1/memory/redactions", json={"scope": "user:me"})
        assert acts.status_code == 200
        (act,) = acts.json()["redactions"]
        assert (act["path"], act["count"]) == ("a", 1)

    async def test_a_naive_since_is_refused_at_the_door(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/v1/memory/history",
            json={"scope": "user:me", "since": "2026-09-02T12:00:00"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "value_error"


class TestAuditThroughTheDaemon:
    async def test_the_engine_answers_over_remote_store(
        self, remote_over_file: RemoteOverFile
    ) -> None:
        """The same `audit()` over `RemoteStore`: the daemon's prefix on
        every actor, the turn merged in, one answer for every substrate."""
        remote = remote_over_file.remote
        await remote.write("user:me", "a", "x", actor="conv:x#1")
        await remote.append_turn(
            "x", [Message(role=Role.USER, content="hi")], actor="conv:x"
        )
        await remote.redact("user:me", path="a")
        entries = await audit(remote, "user:me", conversation_id="x")
        assert [(e.event, e.actor) for e in entries] == [
            ("redacted", "client:default"),
            ("turn", "client:default/conv:x"),
            ("created", "client:default/conv:x#1"),
        ]
        assert (
            await audit(
                remote, "user:me", conversation_id="x", actor="client:default/conv:x"
            )
            == entries[1:]
        )
