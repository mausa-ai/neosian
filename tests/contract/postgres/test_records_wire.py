"""`conversation/write_records` on Postgres with records extension 1 (DESIGN
§40.54 items 8.2, 9 and 14): the design's example with its literal bytes,
every conflict reason with its details, and the two 413 refusals. The same
literals as the FileStore contracts in `tests/contract/records/`."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from tests.contract.records.example import DRAFT, EXAMPLE, MESSAGES, SESSION
from tests.contract.records.support import (
    BODY_CEILING,
    ROUTE,
    bookkeeping,
    conflict,
    conversation_row,
    header_bytes,
    nothing,
    probe,
    prompts,
    record,
    records_bytes,
    refused,
    send,
    write,
)
from tests.contract.support import ADMIN, LIMITED, Binary, Server, serve

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


@contextmanager
def recording(
    database: Database, binary: Binary, **env: str
) -> Iterator[tuple[Server, psycopg.Connection[tuple[object, ...]], str]]:
    """A server started after the operator installed the extension."""
    connection, name, plain = database
    absent = refused(plain, {}, 400, "agent_configuration_error")
    assert absent == {
        "code": "agent_configuration_error",
        "message": "records are not enabled on this store;"
        " install Postgres records extension 1",
        "details": {},
    }
    ddl = binary.run("schema", "records-postgres", "--schema", name)
    assert ddl.returncode == 0, ddl.stderr
    connection.execute(ddl.stdout)
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    with serve(binary, postgres_dsn=dsn, schema=name, extra_env=env) as server:
        yield server, connection, name


def count(
    connection: psycopg.Connection[tuple[object, ...]], name: str, table: str
) -> object:
    query = psycopg.sql.SQL("SELECT count(*) FROM {}.{}").format(
        psycopg.sql.Identifier(name), psycopg.sql.Identifier(table)
    )
    row = connection.execute(query).fetchone()
    assert row is not None
    return row[0]


def test_the_design_example_lands_in_two_parts_with_its_literal_bytes(
    database: Database, binary: Binary
) -> None:
    actor = "client:default/claude-code:7f3a"
    first, second = EXAMPLE[:9], EXAMPLE[9:]
    assert records_bytes(SESSION, first) + bookkeeping(SESSION, DRAFT) == 458_588
    sealed_bytes = records_bytes(SESSION, second) + header_bytes(SESSION, DRAFT, actor)
    assert sealed_bytes == 359_889
    token = f"client:default={ADMIN}"
    with recording(database, binary, NEOSIAN_SERVE_TOKEN=token) as (server, link, name):
        for number in range(1, 42):
            server.post(
                "conversation/append_turn",
                conversation_id=SESSION,
                messages=[{"role": "user", "content": f"turn {number}"}],
            )
        assert probe(server, SESSION, DRAFT) == nothing(DRAFT)
        opened = write(
            server,
            SESSION,
            DRAFT,
            first,
            actor="claude-code:7f3a",
            turn_bytes=812_400,
        )
        assert opened == {
            "draft": "d7c1e0b2a9f44e57",
            "state": "open",
            "next_seq": 9,
            "turn": None,
            "stored_bytes": 458_588,
            "freed_bytes": 0,
        }
        assert count(link, name, "turn_records") == 9
        number = server.post("conversation/last_turn_number", conversation_id=SESSION)
        assert number == {"turn": 41}
        # A part repeated under other boundaries stores nothing.
        assert write(server, SESSION, DRAFT, first[3:7]) == {
            **opened,
            "stored_bytes": 0,
        }
        sealed = write(
            server, SESSION, DRAFT, second, actor="claude-code:7f3a", seal=True
        )
        created_at = sealed["turn"]["created_at"]
        assert sealed == {
            "draft": "d7c1e0b2a9f44e57",
            "state": "sealed",
            "next_seq": 16,
            "turn": {
                "turn": 42,
                "created_at": created_at,
                "actor": "client:default/claude-code:7f3a",
                "redacted": False,
                "records": 16,
                "bytes": 812_400,
            },
            "stored_bytes": 359_889,
            "freed_bytes": 1_054,
        }
        assert count(link, name, "turn_records") == 16
        again = {**sealed, "stored_bytes": 0, "freed_bytes": 0}
        assert probe(server, SESSION, DRAFT) == again
        assert write(server, SESSION, DRAFT, second, seal=True) == again
        read = server.post("conversation/read_turns", conversation_id=SESSION, after=41)
        assert read["turns"] == [
            {
                "conversation_id": SESSION,
                "turn": 42,
                "messages": MESSAGES,
                "created_at": created_at,
                "actor": actor,
                "redacted": False,
            }
        ]


def test_every_conflict_reason_carries_its_details(
    database: Database, binary: Binary
) -> None:
    session = "s-conflict"
    body: dict[str, Any] = {"conversation_id": session, "draft": "d"}
    one = prompts(3)
    with recording(database, binary) as (server, _, _):
        opened = write(server, session, "d", one[:1], actor="me", turn_bytes=3)
        assert opened["stored_bytes"] == (
            records_bytes(session, one[:1]) + bookkeeping(session, "d")
        )
        assert server.post("store/conversations") == {"conversations": []}
        gap = {**body, "records": one[2:]}
        assert conflict(server, session, "draft_gap", gap) == {"next_seq": 1}
        changed = {**body, "records": [{**one[0], "text": "b"}]}
        assert conflict(server, session, "draft_mismatch", changed) == {
            "next_seq": 1,
            "seq": 0,
        }
        other = {**body, "records": one[1:2], "actor": "you"}
        assert conflict(server, session, "draft_actor", other) == {"draft": "d"}
        drop = {**body, "records": [], "discard": True}
        assert conflict(server, session, "draft_actor", {**drop, "actor": "you"}) == {
            "draft": "d"
        }
        assert conflict(server, session, "draft_size", {**drop, "turn_bytes": 4}) == {
            "turn_bytes": 4,
            "bytes": 1,
        }
        named = {**body, "records": one[1:2], "turn_bytes": 4}
        assert conflict(server, session, "draft_size", named) == {
            "turn_bytes": 4,
            "bytes": 1,
        }
        short = {**body, "records": one[1:2], "seal": True}
        assert conflict(server, session, "draft_size", short) == {
            "turn_bytes": 3,
            "bytes": 2,
        }
        past = [*one[1:], record(3, 3, "user", "prompt", "a")]
        assert conflict(server, session, "draft_size", {**body, "records": past}) == {
            "turn_bytes": 3,
            "bytes": 4,
        }
        assert probe(server, session, "d") == {**opened, "stored_bytes": 0}
        sealed = write(server, session, "d", one[1:], seal=True)
        header = sealed["turn"]
        assert header["actor"] == "client:contract/me"
        assert sealed["stored_bytes"] == (
            records_bytes(session, one[1:])
            + header_bytes(session, "d", "client:contract/me")
            + conversation_row(session)
        )
        more = {**body, "records": [record(3, 3, "user", "prompt", "a")]}
        assert conflict(server, session, "draft_sealed", more) == {"turn": header}
        drop = {**body, "records": [], "discard": True}
        assert conflict(server, session, "draft_sealed", drop) == {"turn": header}
        for index in range(16):
            write(server, session, f"open{index}", one[:1])
        extra = {**body, "draft": "open16", "records": one[:1]}
        assert conflict(server, session, "drafts_open", extra) == {"limit": 16}
        dropped = write(server, session, "open0", [], discard=True)
        assert (dropped["state"], dropped["freed_bytes"]) == (
            "discarded",
            records_bytes(session, one[:1]) + bookkeeping(session, "open0"),
        )
        bare = write(server, session, "open16", one, seal=True)
        assert bare["turn"]["actor"] == "client:contract"
        assert bare["turn"]["turn"] == 2


def test_the_two_413_refusals_strictness_and_the_allowance(
    database: Database, binary: Binary
) -> None:
    session = "allowed-1"
    body: dict[str, Any] = {"conversation_id": session, "draft": "d"}
    first = record(0, 0, "user", "prompt", "a" * 40_000)
    second = record(1, 1, "assistant", "reply", "b" * 30_000)
    low = {"NEOSIAN_MAX_RECORD_TURN_BYTES": "65536"}
    with recording(database, binary, **low) as (server, link, name):
        path = "/v1/" + ROUTE
        over = server.request(path, raw=b"x" * (BODY_CEILING + 1))
        assert (over.status, over.body["error"]) == (
            413,
            {
                "code": "value_error",
                "message": "request body exceeds 4194304 bytes",
                "details": {},
            },
        )
        at = server.request(path, raw=b"x" * BODY_CEILING)
        assert (at.status, at.body["error"]["code"]) == (400, "value_error")
        stated = refused(
            server, {**body, "records": [first], "turn_bytes": 70_000}, 413
        )
        assert stated == {
            "code": "value_error",
            "message": "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded:"
            " 70000 > 65536; this store does not keep a turn this large",
            "details": {},
        }
        assert probe(server, session, "d") == nothing("d")
        assert write(server, session, "d", [first])["next_seq"] == 1
        assert refused(server, {**body, "records": [second]}, 413) == stated
        assert probe(server, session, "d")["next_seq"] == 1
        refused(server, {**body, "records": [], "note": 1})
        refused(server, {**body, "records": [{**second, "turn": 1}]})
        refused(server, {**body, "records": [], "seal": None})
        refused(server, {**body, "records": prompts(2_049, 1)})
        assert count(link, name, "turn_records") == 1
        inside = send(server, {**body, "records": []}, token=LIMITED)
        assert (inside.status, inside.body["next_seq"]) == (200, 1)
        outside = send(
            server, {**body, "conversation_id": "other", "records": []}, token=LIMITED
        )
        assert (outside.status, outside.body["error"]["code"]) == (403, "forbidden")
