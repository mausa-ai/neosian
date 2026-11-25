"""`conversation/write_records` and `conversation/read_records` on Postgres
with records extension 1 (DESIGN §40.54 items 8.2, 9, 10 and 14): the
design's examples with their literal bytes, every conflict reason with its
details, the two 413 refusals, and a read in views and pages. The same
literals as the FileStore contracts in `tests/contract/records/`."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from tests.contract.records.example import (
    DRAFT,
    EXAMPLE,
    LINED,
    LINED_CHUNKS,
    LINED_EXAMPLE,
    MESSAGES,
    SESSION,
)
from tests.contract.records.reading import (
    EVERY,
    EVERY_BYTES,
    EVERY_MESSAGES,
    all_answered,
    answered,
    header,
    joined,
    message,
    page_bytes,
    pages,
    post,
    raw,
    read,
    refusal,
)
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
from tests.contract.records.test_read_pages import (
    SESSION as PAGED,
    conversation,
    forgeries,
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
    for body in [{}, {"conversation_id": "c"}, {"conversation_id": "c", "limit": 1}]:
        assert refusal(plain, body, 400, "agent_configuration_error") == absent
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


def test_the_design_example_is_read_in_its_light_view_then_whole(
    database: Database, binary: Binary
) -> None:
    """Item 10's example on Postgres: turn 42 under tool heads of 200
    bytes, `turns` before `records` in the raw answer, then the result
    fetched whole in pages of 262,144, three chunks a page."""
    token = f"client:default={ADMIN}"
    with recording(database, binary, NEOSIAN_SERVE_TOKEN=token) as (server, _, _):
        for number in range(1, 42):
            server.post(
                "conversation/append_turn",
                conversation_id=SESSION,
                messages=[{"role": "user", "content": f"turn {number}"}],
            )
        write(server, SESSION, DRAFT, LINED_EXAMPLE[:9], actor="claude-code:7f3a")
        sealed = write(
            server,
            SESSION,
            DRAFT,
            LINED_EXAMPLE[9:],
            actor="claude-code:7f3a",
            seal=True,
        )
        turn = {
            "turn": 42,
            "created_at": sealed["turn"]["created_at"],
            "actor": "client:default/claude-code:7f3a",
            "redacted": False,
            "records": 16,
            "bytes": 812_400,
        }
        assert sealed["turn"] == turn
        head = "a" * 129 + "\n" + "a" * 67
        tail = "a" * 91 + "\n" + "a" * 105
        light = [
            answered(42, LINED_EXAMPLE[0]),
            answered(42, LINED_EXAMPLE[1]),
            answered(42, LINED_EXAMPLE[2], text=head, tail=tail, truncated=True),
            answered(42, LINED_EXAMPLE[15]),
        ]
        assert LINED.startswith(head) and LINED.endswith(tail)
        view = {
            "conversation_id": "cc-7f3a",
            "turn": 42,
            "kinds": ["prompt", "reply", "tool_call", "tool_result"],
            "heads": {"tool_result": 200},
        }
        assert raw(server, view) == (200, page_bytes([turn], light, None))
        chunks = all_answered(42, LINED_EXAMPLE[2:15])
        fetched = pages(server, SESSION, 262_144, turn=42, seq=2, piece=True)
        assert [len(page["records"]) for page in fetched] == [3, 3, 3, 4]
        assert [item["seq"] for item in fetched[0]["records"]] == [2, 3, 4]
        assert joined(fetched) == ([turn], chunks)
        assert [item["text"] for item in chunks] == LINED_CHUNKS
        # Turns stored whole are converted by the same route, and the whole
        # conversation pages at the default under 1 MiB an answer.
        earlier = read(server, SESSION, through=41)
        assert [item["text"] for item in earlier["records"]] == [
            f"turn {number}" for number in range(1, 42)
        ]
        assert [item["records"] for item in earlier["turns"]] == [1] * 41
        default = pages(server, SESSION, None, after=41)
        assert joined(default) == ([turn], all_answered(42, LINED_EXAMPLE))
        status, first = raw(server, {"conversation_id": SESSION, "after": 41})
        assert status == 200 and len(first) <= 983_040 + 4_160


def test_a_mixed_conversation_reads_in_views_and_pages(
    database: Database, binary: Binary
) -> None:
    """A format-1 turn, the same turn as records, a redacted turn, an open
    draft and a last turn: every view, pages of 1 byte and of 700, a
    continuation bound to its selection, strictness and the allowance."""
    session = "allowed-1"
    text: dict[str, Any] = {"reasoning": None, "tool_calls": [], "tool_call_id": None}
    with recording(database, binary) as (server, _, _):
        nothing_yet: dict[str, Any] = {"turns": [], "records": [], "continuation": None}
        assert read(server, session) == nothing_yet
        server.post(
            "conversation/append_turn", conversation_id=session, messages=EVERY_MESSAGES
        )
        write(server, session, "every", EVERY, seal=True, actor="me")
        gone = [{"role": "user", "content": "forget this", **text}]
        server.post("conversation/append_turn", conversation_id=session, messages=gone)
        erased = server.post(
            "conversation/redact_turns", conversation_id=session, turns=[3]
        )
        assert erased == {"count": 1}
        assert write(server, session, "open", EVERY[:3])["state"] == "open"
        last = [record(0, 0, "user", "prompt", "bye")]
        write(server, session, "last", last, seal=True)
        stored = server.post("conversation/read_turns", conversation_id=session)
        headers = [
            header(stored["turns"][0], 9, EVERY_BYTES),
            header(stored["turns"][1], 9, EVERY_BYTES),
            header(stored["turns"][2], 0, 0, redacted=True),
            header(stored["turns"][3], 1, 3),
        ]
        assert [turn["actor"] for turn in headers] == [
            "client:contract",
            "client:contract/me",
            "client:contract",
            "client:contract",
        ]
        items = all_answered(1, EVERY) + all_answered(2, EVERY) + all_answered(4, last)
        whole = {"turns": headers, "records": items, "continuation": None}
        assert read(server, session) == whole
        assert raw(server, {"conversation_id": session}) == (
            200,
            page_bytes(headers, items, None),
        )
        selections: list[dict[str, Any]] = [
            {},
            {"kinds": ["prompt", "reply"]},
            {"kinds": ["tool_call"]},
            {"kinds": []},
            {"after": 1, "through": 3},
            {"turn": 2, "seq": 4},
            {"heads": {"attachment": 10}},
        ]
        for selection in selections:
            one = read(server, session, **selection)
            assert one["continuation"] is None
            for bound in [1, 700]:
                paged = pages(server, session, bound, **selection)
                assert joined(paged) == (one["turns"], one["records"]), selection
        chat = read(server, session, kinds=["prompt", "reply"])
        assert chat["turns"] == headers
        repeated = ["reply"] * 100_000 + ["prompt"] * 100_000
        assert read(server, session, kinds=repeated) == chat
        assert [(item["turn"], item["seq"]) for item in chat["records"]] == [
            (1, 1),
            (1, 3),
            (2, 1),
            (2, 3),
            (4, 0),
        ]
        # Ten bytes hold eight within the quotes, and a quote costs two:
        # `{"type` is seven and `":null}` is eight.
        cut = read(server, session, turn=2, heads={"attachment": 10})
        assert cut["records"][2] == answered(
            2, EVERY[2], text='{"type', tail='":null}', truncated=True
        )
        assert read(server, session, turn=1, seq=2, piece=True)["records"] == [
            answered(1, EVERY[2])
        ]
        first = read(server, session, kinds=["prompt"], page_bytes=1)
        token = first["continuation"]
        assert isinstance(token, str)
        other = {"conversation_id": session, "kinds": ["reply"], "continuation": token}
        assert message(server, other) == (
            "page continuation belongs to a different request"
        )
        rest = read(server, session, kinds=["prompt"], continuation=token)
        assert [item["turn"] for item in rest["records"]] == [2, 4]
        assert message(server, {"conversation_id": session, "limit": 1}) == (
            "unknown parameter: 'limit'"
        )
        assert message(server, {"conversation_id": session, "turn": None}) == (
            "parameter 'turn' must be a non-negative integer"
        )
        inside = post(server, {"conversation_id": session}, token=LIMITED)
        assert (inside.status, inside.body) == (200, whole)
        for body in [{"conversation_id": "other"}, {"conversation_id": "ghost"}, {}]:
            outside = post(server, body, token=LIMITED)
            assert (outside.status, outside.body["error"]["code"]) == (403, "forbidden")
        long = {"id": "i" * 1_025, "name": "Bash", "arguments": {}}
        calling = {**text, "role": "assistant", "content": None, "tool_calls": [long]}
        server.post(
            "conversation/append_turn", conversation_id=session, messages=[calling]
        )
        reason = (
            "turn 5: message 0 has a call id longer than 1024 bytes,"
            " which records cannot carry"
        )
        unsupported = refusal(
            server,
            {"conversation_id": session},
            400,
            "agent_conversation_format_unsupported",
        )
        assert unsupported == {
            "code": "agent_conversation_format_unsupported",
            "message": f"Unsupported turn format in conversation '{session}': {reason}",
            "details": {"conversation_id": session, "reason": reason},
        }
        assert read(server, session, through=4) == whole


def test_a_made_up_continuation_reads_nothing_beyond_its_request(
    database: Database, binary: Binary
) -> None:
    """Ruling R12 on Postgres, the cases of the FileStore contract."""
    with recording(database, binary) as (server, _, _):
        conversation(server)
        forgeries(server, PAGED)
        only = read(server, PAGED, kinds=[])
        assert [turn["records"] for turn in only["turns"]] == [9, 1, 10, 0, 9]
