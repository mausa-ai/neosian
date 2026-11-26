"""Optional turn records v1: `store/restore_records`.

One conversation is moved verbatim onto an empty target in parts and
appears whole at the commit. These cases restore three turns, the second
erased, with one projection and one trail act, and check the probe and its
position, repeats under other part boundaries, every restore conflict with
its details, the discard, that a half-restored conversation reads as absent,
that actors and times are stored as sent, and both body bounds. The logical
bytes of each answer are computed here from the published size rule, and
the unit digest by an independent Python reference: SHA-256 over the
SHA-256 of each item's compact JSON in stream order. Values stay inside the
range every store accepts. Absent support is an explicit skip under the full
and base profiles.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.contract.records.reading import read
from tests.contract.records.restoring import (
    ANSWER_BOUND,
    EPOCH,
    HEADERS,
    ONE,
    RESTORE,
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
    restore_bookkeeping,
    send,
    status,
    unit_digest,
)
from tests.contract.records.support import BODY_CEILING, PART_PREFIX, record
from tests.contract.storage.support import (
    Storage,
    conflicted,
    message,
    refused,
    require_records,
    sent as answer_bytes,
    too_large,
)

ID = "r-1"
EMPTY_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
FIRST: dict[str, Any] = {"turns": HEADERS[:1], "records": ONE}


@pytest.fixture(autouse=True)
def records_capability(storage: Storage) -> None:
    require_records(storage)


def erased(storage: Storage) -> dict[str, Any]:
    """The trail act of the erased second turn, by an actor of its own."""
    return act(storage.conversation, [2], "client:default/ops", "2026-10-05T08:00:00Z")


def second(storage: Storage) -> dict[str, Any]:
    return {
        "turns": HEADERS[1:],
        "records": THREE,
        "projections_from": 0,
        "projections": [EPOCH],
        "redactions_from": 0,
        "redactions": [erased(storage)],
    }


def stream(storage: Storage) -> list[dict[str, Any]]:
    return [HEADERS[0], *ONE, HEADERS[1], HEADERS[2], *THREE, EPOCH, erased(storage)]


def conflict(storage: Storage, reason: str, restore: str = ID, **more: Any) -> Any:
    reply = send(storage.server, storage.conversation, restore, **more)
    return conflicted(reply, storage.conversation, reason)


def test_a_conversation_is_restored_in_two_parts_and_appears_whole(
    storage: Storage,
) -> None:
    server, session = storage.server, storage.conversation
    end = position(3, 1, True, 1, 1)
    digest = unit_digest(stream(storage))
    assert status(server, session, ID) == answer(ID, "none", None)
    opening = content_bytes(session, ID, FIRST) + restore_bookkeeping(session, ID)
    first = part(server, session, ID, **FIRST)
    assert first == answer(ID, "open", position(1, 3, True), opening)
    assert status(storage.peer, session, ID) == answer(ID, "open", position(1, 3, True))
    # Nothing of it is visible before the commit.
    assert storage.turns("read_turns")["turns"] == []
    assert storage.turns("last_turn_number") == {"turn": 0}
    assert read(server, session) == {"turns": [], "records": [], "continuation": None}
    assert storage.search("y" * 500) == []
    if storage.portable:
        assert session not in server.post("store/conversations")["conversations"]
    closing = content_bytes(session, ID, second(storage)) + marker(session, ID)
    done = part(server, session, ID, **second(storage), commit=True)
    assert done == answer(
        ID,
        "committed",
        end,
        closing + parent(session),
        restore_bookkeeping(session, ID),
        digest,
    )
    # Everything is visible at once, as it was sent: numbers, times and
    # actors verbatim, the caller's client never stamped.
    turns = [{**turn, "conversation_id": session} for turn in TURNS]
    assert storage.turns("read_turns") == {"turns": turns, "next_after": None}
    page = read(server, session)
    assert page["turns"] == HEADERS
    assert page["records"] == [
        {**item, "tail": None, "truncated": False} for item in [*ONE, *THREE]
    ]
    assert storage.turns("read_projections") == {"entries": [EPOCH], "next_after": None}
    assert storage.turns("last_turn_number") == {"turn": 3}
    if storage.capabilities["erasable"]:
        trail = server.post("conversation/turn_redactions", conversations=[session])
        assert trail == {"redactions": [erased(storage)]}
    if storage.portable:
        assert session in server.post("store/conversations")["conversations"]
    # A committed restore answers its receipt to a probe, a commit repeated
    # alone and the final request repeated whole.
    receipt = answer(ID, "committed", end, digest=digest)
    assert status(server, session, ID) == receipt
    assert part(storage.peer, session, ID, commit=True) == receipt
    assert part(server, session, ID, **second(storage), commit=True) == receipt
    # Anything else after the commit, and a discard, are refused.
    assert conflict(storage, "restore_committed", **FIRST) == {"position": end}
    assert conflict(storage, "restore_committed", discard=True) == {"position": end}
    # The store numbers on from the last turn.
    appended = storage.turns("append_turn", messages=[message("next")])
    assert appended["turn"]["turn"] == 4


def test_repeats_under_other_boundaries_are_harmless(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    whole = stream(storage)
    one = part(server, session, ID, turns=HEADERS[:1], records=ONE[:2])
    assert one["position"] == position(1, 2, False)
    held = one["stored_bytes"]
    # The same items again store nothing; an overlap stores what is new.
    again = part(server, session, ID, turns=HEADERS[:1], records=ONE[:1])
    assert again == answer(ID, "open", position(1, 2, False))
    overlap = part(server, session, ID, records=ONE[1:], turns=HEADERS[1:2])
    assert overlap["position"] == position(2, 0, True)
    held += overlap["stored_bytes"]
    rest = part(
        server,
        session,
        ID,
        turns=HEADERS,
        records=[*ONE, *THREE],
        projections_from=0,
        projections=[EPOCH],
    )
    assert rest["position"] == position(3, 1, True, 1, 0)
    held += rest["stored_bytes"]
    # Every turn and the projection are staged and still nothing is visible:
    # not to a record search, a projection read or the erasure trail.
    found = server.post(
        "conversation/search_records", query="y" * 500, conversations=[session]
    )
    assert found == {"hits": []}
    assert storage.turns("read_projections") == {"entries": [], "next_after": None}
    assert read(server, session) == {"turns": [], "records": [], "continuation": None}
    if storage.capabilities["erasable"]:
        trail = server.post("conversation/turn_redactions", conversations=[session])
        assert trail == {"redactions": []}
    body = {
        "turns": HEADERS,
        "records": [*ONE, *THREE],
        "projections": [EPOCH],
        "redactions": [erased(storage)],
    }
    total = content_bytes(session, ID, body) + restore_bookkeeping(session, ID)
    acts = act_bytes(session, erased(storage))
    assert held == total - acts
    done = part(
        server,
        session,
        ID,
        redactions_from=0,
        redactions=[erased(storage)],
        commit=True,
    )
    assert done == answer(
        ID,
        "committed",
        position(3, 1, True, 1, 1),
        acts + marker(session, ID) + parent(session),
        restore_bookkeeping(session, ID),
        unit_digest(whole),
    )


def test_each_restore_conflict_names_its_reason_and_details(
    storage: Storage,
) -> None:
    server, session = storage.server, storage.conversation
    # A place beside an empty list is checked on a restore that does not exist.
    absent = {"position": position(0, 0, True)}
    assert conflict(storage, "restore_gap", projections_from=1, projections=[]) == (
        absent
    )
    part(server, session, ID, turns=HEADERS[:1], records=ONE[:2])
    at = position(1, 2, False)
    # A record ahead of its `seq`, and a header before its turn is complete.
    assert conflict(storage, "restore_gap", turns=HEADERS[1:2]) == {"position": at}
    ahead = [placed(1, record(3, 3, "user", "prompt", "late"))]
    assert conflict(storage, "restore_gap", records=ahead) == {"position": at}
    late: dict[str, Any] = {"projections_from": 0, "projections": [EPOCH]}
    assert conflict(storage, "restore_gap", **late) == {"position": at}
    # An item at a place already stored must be the item stored there.
    other = [placed(1, record(0, 0, "system", "system", "q" * 32))]
    changed: dict[str, Any] = {"turns": HEADERS[:1], "records": other}
    assert conflict(storage, "restore_mismatch", **changed) == {"position": at}
    renamed = [{**HEADERS[0], "actor": "someone-else"}]
    assert conflict(storage, "restore_mismatch", turns=renamed) == {"position": at}
    # A commit before the last turn is complete.
    assert conflict(storage, "restore_incomplete", commit=True) == {"position": at}
    # Another restore of the same conversation while this one is open.
    assert conflict(storage, "restore_open", restore="r-2", **FIRST) == {"restore": ID}
    # Every refusal left the restore where it was.
    assert status(server, session, ID) == answer(ID, "open", at)
    # A projection naming a turn the restore does not hold fails the commit.
    part(server, session, ID, records=ONE[2:])
    stray = {"projections_from": 0, "projections": [projection(7, "log", "stray")]}
    held = part(server, session, ID, **stray)["position"]
    assert held == position(1, 3, True, 1, 0)
    assert conflict(storage, "restore_incomplete", commit=True) == {"position": held}
    freed = part(server, session, ID, discard=True)
    assert (freed["state"], freed["position"]) == ("discarded", held)
    body = {"turns": HEADERS[:1], "records": ONE, **stray}
    assert freed["freed_bytes"] == content_bytes(
        session, ID, body
    ) + restore_bookkeeping(session, ID)
    assert status(server, session, ID) == answer(ID, "none", None)
    assert part(server, session, ID, discard=True) == answer(ID, "none", None)


def test_an_occupied_target_refuses_the_first_part_and_the_commit(
    storage: Storage,
) -> None:
    server, session = storage.server, storage.conversation
    part(server, session, ID, **FIRST)
    # A turn appended meanwhile succeeds on the empty target.
    appended = storage.turns("append_turn", messages=[message("meanwhile")])
    assert appended["turn"]["turn"] == 1
    at = position(1, 3, True)
    assert conflict(storage, "target_occupied", commit=True) == {}
    assert status(server, session, ID) == answer(ID, "open", at)
    assert part(server, session, ID, discard=True)["state"] == "discarded"
    # On an occupied target the first part is refused, under any id.
    assert conflict(storage, "target_occupied", restore="r-3", **FIRST) == {}
    assert conflict(storage, "target_occupied", restore="r-3", commit=True) == {}
    assert status(server, session, "r-3") == answer("r-3", "none", None)
    assert [turn["turn"] for turn in storage.turns("read_turns")["turns"]] == [1]


def test_actors_and_times_are_stored_as_sent(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    headers = [
        header(2, "2026-10-02T11:15:00+02:00", None, 1, 1),
        header(5, "2026-10-02T09:16:00.250000Z", "someone/else", 1, 1),
    ]
    items = [
        placed(2, record(0, 0, "user", "prompt", "a")),
        placed(5, record(0, 0, "user", "prompt", "b")),
    ]
    # A time is kept as its instant and written in UTC with `Z`.
    written = [{**headers[0], "created_at": "2026-10-02T09:15:00Z"}, headers[1]]
    canonical = [written[0], items[0], written[1], items[1]]
    done = part(server, session, ID, turns=headers, records=items, commit=True)
    assert done["state"] == "committed"
    assert done["digest"] == unit_digest(canonical)
    assert done["position"] == position(5, 1, True)
    turns = storage.turns("read_turns")["turns"]
    assert [(t["turn"], t["created_at"], t["actor"]) for t in turns] == [
        (2, "2026-10-02T09:15:00Z", None),
        (5, "2026-10-02T09:16:00.250000Z", "someone/else"),
    ]
    assert read(server, session)["turns"] == written
    assert storage.turns("last_turn_number") == {"turn": 5}


def test_an_empty_commit_publishes_an_empty_restore(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    done = part(server, session, ID, commit=True)
    assert done == answer(
        ID,
        "committed",
        position(0, 0, True),
        restore_bookkeeping(session, ID) + marker(session, ID) + parent(session),
        restore_bookkeeping(session, ID),
        EMPTY_DIGEST,
    )
    assert unit_digest([]) == EMPTY_DIGEST
    assert storage.turns("last_turn_number") == {"turn": 0}
    if storage.portable:
        assert session not in server.post("store/conversations")["conversations"]
    at = {"position": position(0, 0, True)}
    assert conflict(storage, "restore_committed", **FIRST) == at
    # The target is not occupied: a restore under another id still succeeds.
    assert part(server, session, "r-2", **FIRST)["state"] == "open"
    assert part(server, session, "r-2", discard=True)["state"] == "discarded"


@pytest.mark.parametrize(
    "wrong",
    [
        {"note": 1},
        {"restore": "Upper"},
        {"restore": ""},
        {"restore": "x" * 65},
        {"turns": None},
        {"turns": {}},
        {"commit": "true"},
        {"commit": None},
        {"discard": 1},
        {"discard": True, "commit": True},
        {"discard": True, "turns": []},
        {"projections": []},
        {"projections_from": 0},
        {"redactions": []},
        {"redactions_from": 0},
        {"projections_from": -1, "projections": []},
        {"turns": [{**HEADERS[0], "weight": 1}]},
        {"turns": [{**HEADERS[0], "turn": 0}]},
        {"turns": [{**HEADERS[0], "created_at": "2026-10-02T09:15:00"}]},
        {"turns": [{**HEADERS[0], "created_at": "2026-10-02T09:15:00.0000001Z"}]},
        {"turns": [{**HEADERS[0], "records": 0}]},
        {"turns": [HEADERS[2], HEADERS[0]]},
        {"turns": HEADERS[:1], "records": [{**ONE[0], "tail": None}]},
        {"turns": HEADERS[:1], "records": [dict(list(ONE[0].items())[1:])]},
        {"turns": HEADERS[:1], "records": [{**ONE[0], "size": 31}]},
        {
            "turns": HEADERS[:1],
            "records": ONE,
            "projections_from": 0,
            "projections": [{**EPOCH, "kind": "summary"}],
        },
        {
            "turns": HEADERS[:1],
            "records": ONE,
            "projections_from": 0,
            "projections": [{**EPOCH, "note": 1}],
        },
        {
            "turns": HEADERS[:1],
            "records": ONE,
            "redactions_from": 0,
            "redactions": [{"conversation_id": "another", "turns": [1], "actor": None}],
        },
        {
            "turns": [
                header(n, "2026-10-02T09:15:00Z", None, 0, 0, redacted=True)
                for n in range(1, 2_050)
            ]
        },
    ],
)
def test_a_part_is_decoded_strictly_and_a_refusal_stores_nothing(
    storage: Storage, wrong: dict[str, Any]
) -> None:
    server, session = storage.server, storage.conversation
    reply = send(server, session, **{"restore": ID, **wrong})
    refused(reply, "value_error")
    assert status(server, session, ID) == answer(ID, "none", None)


def test_a_part_holds_2048_items_and_at_most_4_mib(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    blanks = [
        header(number, "2026-10-02T09:15:00Z", None, 0, 0, redacted=True)
        for number in range(1, 2_049)
    ]
    full = part(server, session, ID, turns=blanks)
    assert full["position"] == position(2_048, 0, True)
    assert full["stored_bytes"] == content_bytes(
        session, ID, {"turns": blanks}
    ) + restore_bookkeeping(session, ID)
    # The answer carries no actor and no header: at most 2,048 bytes as sent.
    code, sent = answer_bytes(
        server, RESTORE, {"conversation_id": session, "restore": ID}
    )
    assert code == 200 and len(sent) <= ANSWER_BOUND
    assert part(server, session, ID, discard=True)["freed_bytes"] == (
        full["stored_bytes"]
    )
    # The ceiling is checked while the body is read, before it is parsed.
    path = "/v1/" + RESTORE
    over = server.request(path, raw=b"x" * (BODY_CEILING + 1))
    assert too_large(over, PART_PREFIX) == f"request body exceeds {BODY_CEILING} bytes"
    refused(server.request(path, raw=b"x" * BODY_CEILING), "value_error")
    denied = server.request(path, {}, authenticated=False)
    assert (denied.status, denied.body) == (401, "unauthorized")
