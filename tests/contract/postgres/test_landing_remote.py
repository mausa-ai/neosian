"""The rows of 17.4 that reach the store, over HTTP to a `neosian serve` on
PostgreSQL with records extension 1 (section C of the T1e1 plan, T1e1c): a
turn in two parts and a seal, a seal whose answer was lost, and a draft that
expired between parts, each through the forwarding stub and `neosian
record-land --home H`. The FileStore twins are in
`tests/contract/landing/test_rows.py`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg import sql

from tests.contract.landing.conftest import Hold, hold_lock
from tests.contract.landing.lander import (
    PART_BYTES,
    draft_id,
    land,
    opening,
    pending,
    queue,
    retry,
    turns,
)
from tests.contract.landing.stub import (
    Exchange,
    Rule,
    Stub,
    forwarding,
    resumed_without_resending,
)
from tests.contract.postgres.test_landing_records import Database, dsn, install_records
from tests.contract.support import Binary, Server, serve

pytestmark = pytest.mark.postgres
WRITE = "conversation/write_records"
LARGE = 1_000_000
__all__ = ["hold_lock"]


@pytest.fixture
def remote(
    database: Database, binary: Binary
) -> Iterator[tuple[Server, Stub, Database]]:
    """A server started once the extension is installed, so it declares
    turn records, and the stub in front of it."""
    connection, name, _ = database
    install_records(connection, binary, name)
    with serve(binary, postgres_dsn=dsn(), schema=name) as server:
        assert server.request("/v1/capabilities", method="GET").body["turn_records"]
        with forwarding(server.url) as stub:
            yield server, stub, database


def headers(database: Database, session: str) -> list[tuple[object, ...]]:
    connection, name, _ = database
    query = "SELECT turn, records, bytes, draft FROM {s}.record_turns"
    query += " WHERE conversation_id = %s ORDER BY turn"
    statement = sql.SQL(query).format(s=sql.Identifier(name))
    return connection.execute(statement, (session,)).fetchall()


def parts_of(stub: Stub, draft: str) -> list[Exchange]:
    return [e for e in stub.records() if e.body["draft"] == draft and e.body["records"]]


def test_a_turn_lands_in_two_parts_and_a_seal(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub, Database]
) -> None:
    server, stub, database = remote
    session = "pg-parts"
    n = queue(binary, hold_lock, stub.url, session, "build it", LARGE)
    draft = draft_id(session, opening(binary, session, n))
    assert land(binary)["landed"] == 1
    parts = parts_of(stub, draft)
    assert len(parts) == 2 and all(len(e.raw) <= PART_BYTES for e in stub.log)
    assert [e.body.get("seal", False) for e in parts] == [False, True]
    count = sum(len(e.body["records"]) for e in parts)
    assert headers(database, session) == [
        (1, count, parts[0].body["turn_bytes"], draft)
    ]
    [turn] = turns(server, session)
    assert len(turn["messages"][2]["content"]) == LARGE


def test_a_lost_seal_answer_is_finished_once(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub, Database]
) -> None:
    server, stub, database = remote
    session = "pg-lost-seal"
    n = queue(binary, hold_lock, stub.url, session, "sealed once", LARGE)
    stub.add(
        Rule(
            when=lambda e: e.route == WRITE and e.body.get("seal") is True, kind="drop"
        )
    )
    assert land(binary)["landed"] == 0 and pending(binary, session) == [n]
    # No answer: the destination waits (T1e1e); the person retries now.
    assert len(retry(binary)) == 1
    before = len(stub.log)
    stub.next_run()
    assert land(binary)["landed"] == 1
    again = [e for e in stub.log[before:] if e.route == WRITE]
    assert [(e.body["records"], e.answer["state"]) for e in again] == [([], "sealed")]
    assert len(turns(server, session)) == len(headers(database, session)) == 1
    resumed_without_resending(stub.log)


def test_a_draft_expired_between_parts_starts_again_and_is_stored_once(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub, Database]
) -> None:
    server, stub, database = remote
    session = "pg-expired"
    n = queue(binary, hold_lock, stub.url, session, "expires", LARGE)
    draft = draft_id(session, opening(binary, session, n))

    def second(e: Exchange) -> bool:
        if e.route != WRITE or e.body["draft"] != draft or not e.body["records"]:
            return False
        return bool(e.body["records"][0]["seq"] > 0)

    stub.add(Rule(when=second, kind="expire", status=410, body="gone"))
    assert land(binary)["landed"] == 1
    sent = stub.records(session)
    [cut] = [i for i, e in enumerate(sent) if not e.forwarded]
    assert sent[cut + 1].answer["state"] == "none"
    assert sent[cut + 2].body["records"][0]["seq"] == 0
    assert len(turns(server, session)) == len(headers(database, session)) == 1
    resumed_without_resending(stub.log)
