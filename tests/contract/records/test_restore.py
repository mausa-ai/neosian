"""`store/restore_records` (DESIGN §40.54 items 8.2, 12, 14 and 22): one
conversation moved verbatim onto an empty target in parts.

Literal requests and literal answers. The byte counts are computed here
from the size rule and the unit digest by an independent Python reference:
SHA-256 over the SHA-256 of each item's compact JSON in stream order,
written from the design and sharing no code with the binary.
"""

from __future__ import annotations

import hashlib
from typing import Any

from tests.contract.records.reading import read
from tests.contract.records.restoring import (
    ANSWER_BOUND,
    DIGEST,
    END,
    EPOCH,
    ERASED,
    FIRST,
    HEADERS,
    ID,
    ONE,
    RESTORE,
    SECOND,
    SESSION,
    STREAM,
    THREE,
    TURNS,
    act,
    act_bytes,
    answer,
    content_bytes,
    header,
    marker,
    parent,
    part,
    placed,
    position,
    projection,
    projection_bytes,
    restore_bookkeeping,
    status,
    turn_header_bytes,
    unit_digest,
)
from tests.contract.records.searching import raw
from tests.contract.records.support import compact, record, record_bytes
from tests.contract.support import Binary, Server, serve


def example(server: Server) -> dict[str, Any]:
    """Item 12's example with its literal answers, then what every read
    answers of it; the committed restore's receipt is returned."""
    assert "i" + hashlib.sha256(SESSION.encode()).hexdigest()[:32] == ID
    # Item 14, from the request alone; the design prints the same numbers.
    assert turn_header_bytes(SESSION, ID, HEADERS[0]) == 589
    assert sum(record_bytes(SESSION, item) for item in ONE) == 2_342
    assert restore_bookkeeping(SESSION, ID) == 1_071
    assert content_bytes(SESSION, ID, FIRST) + 1_071 == 4_002
    assert turn_header_bytes(SESSION, ID, HEADERS[1]) == 286
    assert (projection_bytes(SESSION, EPOCH), act_bytes(SESSION, ERASED)) == (158, 164)
    assert (marker(SESSION, ID), parent(SESSION)) == (303, 263)
    assert content_bytes(SESSION, ID, SECOND) + 303 + 263 == 2_077
    # The unit digest of the stream, by an independent Python reference.
    assert unit_digest(STREAM) == DIGEST
    assert status(server, SESSION, ID) == answer(ID, "none", None)
    first = part(server, SESSION, ID, **FIRST)
    assert first == answer(ID, "open", position(1, 3, True), 4_002)
    assert first == {
        "restore": "iecf9f710d465bc0ea0fe2fbba8915596",
        "state": "open",
        "position": {
            "turn": 1,
            "seq": 3,
            "complete": True,
            "projections": 0,
            "redactions": 0,
        },
        "stored_bytes": 4002,
        "freed_bytes": 0,
        "digest": None,
    }
    assert status(server, SESSION, ID) == answer(ID, "open", position(1, 3, True))
    second = part(server, SESSION, ID, **SECOND, commit=True)
    assert second == {
        "restore": "iecf9f710d465bc0ea0fe2fbba8915596",
        "state": "committed",
        "position": {
            "turn": 3,
            "seq": 1,
            "complete": True,
            "projections": 1,
            "redactions": 1,
        },
        "stored_bytes": 2077,
        "freed_bytes": 1071,
        "digest": "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32",
    }
    # Everything is visible at once, as it was sent.
    turns = server.post("conversation/read_turns", conversation_id=SESSION)
    assert turns == {"turns": TURNS, "next_after": None}
    page = read(server, SESSION)
    assert page["turns"] == HEADERS
    assert [(item["turn"], item["seq"]) for item in page["records"]] == [
        (1, 0),
        (1, 1),
        (1, 2),
        (3, 0),
    ]
    entries = server.post("conversation/read_projections", conversation_id=SESSION)
    assert entries == {"entries": [EPOCH], "next_after": None}
    trail = server.post("conversation/turn_redactions", conversations=[SESSION])
    assert trail == {"redactions": [ERASED]}
    last = server.post("conversation/last_turn_number", conversation_id=SESSION)
    assert last == {"turn": 3}
    assert server.post("store/conversations") == {"conversations": [SESSION]}
    # A committed restore answers its receipt to a probe and a commit.
    receipt = answer(ID, "committed", END, digest=DIGEST)
    assert status(server, SESSION, ID) == receipt
    assert part(server, SESSION, ID, commit=True) == receipt
    assert part(server, SESSION, ID, **SECOND, commit=True) == receipt
    return receipt


def test_the_design_example_restores_in_two_parts_with_its_literal_bytes(
    enabled: Binary,
) -> None:
    """Item 12's example: `cc-7f3a` under the id item 22 derives for it.
    The committed restore answers the same receipt after a restart."""
    with serve(enabled) as server:
        receipt = example(server)
    with serve(enabled) as server:
        assert status(server, SESSION, ID) == receipt
        again = server.post("conversation/read_turns", conversation_id=SESSION)
        assert again["turns"] == TURNS


def test_the_answer_writes_its_keys_in_the_tables_order_within_its_bound(
    records: Server,
) -> None:
    """Items 8.2 and 14, on the bytes as sent: `restore`, `state`,
    `position` (its five keys in order), `stored_bytes`, `freed_bytes`,
    `digest`; a 200 answer is at most 2,048 bytes and carries no actor."""
    base = {"conversation_id": SESSION, "restore": ID}
    seen = []
    for body, expected in [
        (base, answer(ID, "none", None)),
        ({**base, **FIRST}, answer(ID, "open", position(1, 3, True), 4_002)),
        (base, answer(ID, "open", position(1, 3, True))),
        (
            {**base, **SECOND, "commit": True},
            answer(ID, "committed", END, 2_077, 1_071, DIGEST),
        ),
        (base, answer(ID, "committed", END, digest=DIGEST)),
    ]:
        code, data = raw(records, RESTORE, body)
        assert (code, data) == (200, compact(expected))
        seen.append(data)
    assert seen[1] == (
        b'{"restore":"iecf9f710d465bc0ea0fe2fbba8915596","state":"open",'
        b'"position":{"turn":1,"seq":3,"complete":true,"projections":0,'
        b'"redactions":0},"stored_bytes":4002,"freed_bytes":0,"digest":null}'
    )
    assert seen[3] == (
        b'{"restore":"iecf9f710d465bc0ea0fe2fbba8915596","state":"committed",'
        b'"position":{"turn":3,"seq":1,"complete":true,"projections":1,'
        b'"redactions":1},"stored_bytes":2077,"freed_bytes":1071,"digest":'
        b'"6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32"}'
    )
    assert [len(data) for data in seen] == [125, 189, 186, 259, 253]
    # The widest answer measured: a 64-byte id, a 128-byte conversation, an
    # actor of 4,096 escaped bytes that the answer does not carry, and the
    # largest turn number a header takes. That number is a FileStore value:
    # PostgreSQL keeps a turn number in 31 bits and refuses a larger one.
    wide, long, last = "w" * 128, "r" * 64, 2**64 - 1
    actor = "\u0001" * 4_096
    turns = [
        header(last - 1, "2026-10-02T09:15:00.000001Z", actor, 1, 1),
        header(last, "2026-10-02T09:15:00.000002Z", actor, 0, 0, redacted=True),
    ]
    only = [placed(last - 1, record(0, 0, "user", "prompt", "a"))]
    opened = {"conversation_id": wide, "restore": long, "turns": turns, "records": only}
    code, data = raw(records, RESTORE, opened)
    stored = content_bytes(wide, long, opened) + restore_bookkeeping(wide, long)
    assert (code, data) == (
        200,
        compact(answer(long, "open", position(last, 0, True), stored)),
    )
    stream = [turns[0], only[0], turns[1]]
    final = marker(wide, long) + parent(wide)
    done = answer(
        long,
        "committed",
        position(last, 0, True),
        final,
        restore_bookkeeping(wide, long),
        unit_digest(stream),
    )
    code, data = raw(
        records, RESTORE, {"conversation_id": wide, "restore": long, "commit": True}
    )
    assert (code, data) == (200, compact(done))
    assert len(data) == 308 and len(data) <= ANSWER_BOUND
    # The widest a committed answer can be spelled: every number at twenty
    # digits. A discarded one has no digest and is shorter.
    top = position(last, last, True, last, last)
    assert len(compact(answer(long, "committed", top, last, last, "f" * 64))) == 398
    open_top = position(last, last, False, last, last)
    assert len(compact(answer(long, "discarded", open_top, last, last))) == 337
    code, data = raw(
        records,
        RESTORE,
        {"conversation_id": "gone", "restore": long, "turns": turns[:1]},
    )
    assert code == 200
    code, data = raw(
        records,
        RESTORE,
        {"conversation_id": "gone", "restore": long, "discard": True},
    )
    freed = turn_header_bytes("gone", long, turns[0]) + restore_bookkeeping(
        "gone", long
    )
    assert (code, data) == (
        200,
        compact(answer(long, "discarded", position(last - 1, 0, False), freed=freed)),
    )
    assert len(data) <= ANSWER_BOUND


def test_a_probe_answers_the_position_to_resume_from(records: Server) -> None:
    """Item 12 "Resume": `turn` is the last header received, `seq` its
    records received, `complete` whether it has them all, then the counts
    of projections and acts. A probe stores nothing."""
    steps: list[tuple[dict[str, Any], dict[str, Any]]] = [
        ({"turns": HEADERS[:1]}, position(1, 0, False)),
        ({"records": ONE[:1]}, position(1, 1, False)),
        ({"records": ONE[1:2]}, position(1, 2, False)),
        ({"records": ONE[2:]}, position(1, 3, True)),
        ({"turns": HEADERS[1:2]}, position(2, 0, True)),
        ({"turns": HEADERS[2:]}, position(3, 0, False)),
        ({"records": THREE}, position(3, 1, True)),
        ({"projections_from": 0, "projections": [EPOCH]}, position(3, 1, True, 1)),
        ({"redactions_from": 0, "redactions": [ERASED]}, END),
    ]
    total = 0
    assert status(records, SESSION, ID) == answer(ID, "none", None)
    for index, (body, at) in enumerate(steps):
        stored = content_bytes(SESSION, ID, body)
        if index == 0:
            stored += restore_bookkeeping(SESSION, ID)
        assert part(records, SESSION, ID, **body) == answer(ID, "open", at, stored)
        assert status(records, SESSION, ID) == answer(ID, "open", at)
        total += stored
    assert total == 4_002 + 2_077 - 303 - 263
    done = part(records, SESSION, ID, commit=True)
    assert done == answer(ID, "committed", END, 303 + 263, 1_071, DIGEST)
    turns = records.post("conversation/read_turns", conversation_id=SESSION)
    assert turns["turns"] == TURNS


def test_repeats_under_other_part_boundaries_store_nothing(records: Server) -> None:
    """Item 12 "Repeats are harmless": every item has a place in the
    stream, so a part cut elsewhere repeats what is stored, adds only what
    is new and ends in the same digest."""
    assert part(records, SESSION, ID, **FIRST)["stored_bytes"] == 4_002
    at = position(1, 3, True)
    for body in [
        FIRST,
        {"turns": HEADERS[:1]},
        {"records": ONE[1:]},
        {"turns": HEADERS[:1], "records": ONE[:2]},
        {"records": [ONE[2]]},
    ]:
        assert part(records, SESSION, ID, **body) == answer(ID, "open", at)
    # A part overlapping what is stored adds the new items alone.
    overlap = {"turns": HEADERS[:2], "records": ONE[1:]}
    new = turn_header_bytes(SESSION, ID, HEADERS[1])
    assert part(records, SESSION, ID, **overlap) == answer(
        ID, "open", position(2, 0, True), new
    )
    rest = {**SECOND, "turns": HEADERS, "records": ONE + THREE}
    added = content_bytes(SESSION, ID, SECOND) - new
    assert part(records, SESSION, ID, **rest) == answer(ID, "open", END, added)
    assert part(records, SESSION, ID, **rest) == answer(ID, "open", END)
    whole = part(records, SESSION, ID, **rest, commit=True)
    assert whole == answer(ID, "committed", END, 303 + 263, 1_071, DIGEST)
    # The same stream in one request on another target: the same digest.
    other, name = "cc-other", "one-part"
    trail = [{**ERASED, "conversation_id": other}]
    single = part(records, other, name, **{**rest, "redactions": trail}, commit=True)
    assert single["position"] == END
    assert single["digest"] == unit_digest([*STREAM[:-1], trail[0]])
    assert single["digest"] != DIGEST
    assert single["freed_bytes"] == restore_bookkeeping(other, name)


def test_a_discard_drops_everything_staged(records: Server) -> None:
    assert part(records, SESSION, ID, discard=True) == answer(ID, "none", None)
    part(records, SESSION, ID, **FIRST)
    added = {"turns": HEADERS[1:], "records": []}
    more = part(records, SESSION, ID, **added)["stored_bytes"]
    assert more == content_bytes(SESSION, ID, added)
    gone = part(records, SESSION, ID, discard=True)
    assert gone == answer(ID, "discarded", position(3, 0, False), freed=4_002 + more)
    assert status(records, SESSION, ID) == answer(ID, "none", None)
    assert part(records, SESSION, ID, discard=True) == answer(ID, "none", None)
    assert records.post("store/conversations") == {"conversations": []}
    # The id is free again, and so is the target.
    assert part(records, SESSION, ID, **FIRST)["stored_bytes"] == 4_002
    done = part(records, SESSION, ID, **SECOND, commit=True)
    assert done["digest"] == DIGEST


def test_actors_times_and_redacted_headers_are_stored_as_sent(
    records: Server,
) -> None:
    """Item 12 "Verbatim": the caller's client is never stamped, a null
    actor stays null, a time keeps its instant and is written with `Z`
    and microseconds only when not zero, a redacted turn has no records."""
    session, name = "v-1", "verbatim"
    sent = [
        header(1, "2026-10-02T11:15:00.5+02:00", "someone else/élan", 1, 2),
        header(2, "2026-10-02T09:20:11.000000Z", None, 0, 0, redacted=True),
        {  # A null actor may be left out; key order is free in a request.
            "bytes": 3,
            "records": 1,
            "redacted": False,
            "created_at": "2026-10-02T04:31:40-05:00",
            "turn": 5,
        },
    ]
    stored = [
        header(1, "2026-10-02T09:15:00.500000Z", "someone else/élan", 1, 2),
        header(2, "2026-10-02T09:20:11Z", None, 0, 0, redacted=True),
        header(5, "2026-10-02T09:31:40Z", None, 1, 3),
    ]
    items = [
        placed(1, record(0, 0, "user", "prompt", "é")),
        placed(5, record(0, 0, "assistant", "reply", "ok!")),
    ]
    acts = [
        act(session, [2], None, "2026-10-05T10:00:00+02:00"),
        {  # The same: no actor named, keys in another order.
            "created_at": "2026-10-06T08:00:00.000250Z",
            "turns": [2, 5],
            "conversation_id": session,
        },
    ]
    kept = [
        act(session, [2], None, "2026-10-05T08:00:00Z"),
        act(session, [2, 5], None, "2026-10-06T08:00:00.000250Z"),
    ]
    entries = [projection(1, "log", "first"), {"turn": 5, "kind": "digest", "text": ""}]
    listed = [projection(1, "log", "first"), projection(5, "digest", "")]
    done = part(
        records,
        session,
        name,
        turns=sent,
        records=items,
        projections_from=0,
        projections=entries,
        redactions_from=0,
        redactions=acts,
        commit=True,
    )
    # The digest is over the canonical forms, whatever the spelling sent.
    stream = [stored[0], items[0], stored[1], stored[2], items[1], *listed, *kept]
    assert done["digest"] == unit_digest(stream)
    assert done["position"] == position(5, 1, True, 2, 2)
    canonical = {
        "turns": stored,
        "records": items,
        "projections": listed,
        "redactions": kept,
    }
    assert done["stored_bytes"] == (
        content_bytes(session, name, canonical)
        + restore_bookkeeping(session, name)
        + marker(session, name)
        + parent(session)
    )
    assert read(records, session)["turns"] == stored
    turns = records.post("conversation/read_turns", conversation_id=session)["turns"]
    assert [(t["turn"], t["created_at"], t["actor"], t["redacted"]) for t in turns] == [
        (h["turn"], h["created_at"], h["actor"], h["redacted"]) for h in stored
    ]
    assert turns[1]["messages"] == []
    trail = records.post("conversation/turn_redactions", conversations=[session])
    assert trail == {"redactions": kept[::-1]}, "the trail is listed newest first"
    found = records.post("conversation/read_projections", conversation_id=session)
    assert found["entries"] == listed
    # The store numbers on from the archive's last turn.
    said = [{"role": "user", "content": "next"}]
    new = records.post(
        "conversation/append_turn", conversation_id=session, messages=said
    )
    assert (new["turn"]["turn"], new["turn"]["actor"]) == (6, "client:contract")
