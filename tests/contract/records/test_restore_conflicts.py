"""The conflicts of `store/restore_records` (DESIGN §40.54 items 8.2 and
12): each reason with its details in their order, and that a refused
request changes nothing: the probe answers the same position after it."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from tests.contract.records.restoring import (
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
    THREE,
    TURNS,
    act,
    answer,
    conflict,
    content_bytes,
    header,
    marker,
    parent,
    part,
    position,
    projection,
    restore_bookkeeping,
    status,
    why,
)
from tests.contract.records.searching import raw
from tests.contract.records.support import compact
from tests.contract.support import Binary, Server, serve

HI = [{"role": "user", "content": "hi"}]


def refused(server: Server, reason: str, at: dict[str, Any], **more: Any) -> None:
    """One conflict naming `at`, after which the restore stands where it did."""
    details = conflict(server, SESSION, reason, restore=ID, **more)
    assert details == {"position": at}
    assert status(server, SESSION, ID)["position"] == at


def test_an_item_past_the_next_place_is_a_gap(records: Server) -> None:
    """`restore_gap` with the position to resume from; nothing is opened by
    a refused first part."""
    start = position(0, 0, True)
    details = conflict(records, SESSION, "restore_gap", restore=ID, records=ONE)
    assert details == {"position": start}
    assert status(records, SESSION, ID) == answer(ID, "none", None)
    part(records, SESSION, ID, turns=HEADERS[:1], records=ONE[:1])
    at = position(1, 1, False)
    refused(records, "restore_gap", at, records=ONE[2:])
    refused(records, "restore_gap", at, records=THREE)
    # The next header waits for the turn before it to be complete.
    refused(records, "restore_gap", at, turns=HEADERS[1:2])
    refused(records, "restore_gap", at, turns=HEADERS[2:], records=THREE)
    refused(records, "restore_gap", at, projections_from=0, projections=[EPOCH])
    refused(records, "restore_gap", at, redactions_from=0, redactions=[ERASED])
    # A part whose first items are in place and whose last is not stores none.
    refused(records, "restore_gap", at, turns=HEADERS[:2], records=ONE[:1])
    part(records, SESSION, ID, records=ONE[1:])
    at = position(1, 3, True)
    refused(records, "restore_gap", at, projections_from=1, projections=[EPOCH])
    refused(records, "restore_gap", at, redactions_from=1, redactions=[ERASED])
    first = projection(1, "log", "t")
    part(records, SESSION, ID, projections_from=0, projections=[first])
    at = position(1, 3, True, 1)
    refused(records, "restore_gap", at, projections_from=2, projections=[first])
    refused(records, "restore_gap", at, redactions_from=3, redactions=[ERASED])
    # Out of the stream's order is the request's fault, not a gap.
    assert why(
        records, {"conversation_id": SESSION, "restore": ID, "turns": HEADERS[1:2]}
    ) == ("turn 2: turns come before projections and trail acts")
    assert status(records, SESSION, ID)["position"] == at


def test_a_repeat_that_differs_is_a_mismatch(records: Server) -> None:
    """`restore_mismatch`: an item at a place already stored must be the
    item stored there, whatever kind of item it is."""
    seen = act(SESSION, [1], "client:ops", "2026-10-05T08:00:00Z")
    part(
        records,
        SESSION,
        ID,
        **FIRST,
        projections_from=0,
        projections=[projection(1, "log", "t")],
        redactions_from=0,
        redactions=[seen],
    )
    at = position(1, 3, True, 1, 1)
    changed: list[dict[str, Any]] = [
        {"records": [{**ONE[0], "text": "q" * 32}]},
        {"records": [ONE[0], {**ONE[1], "kind": "attachment", "block": 0}]},
        {"turns": [{**HEADERS[0], "actor": "another"}]},
        {"turns": [{**HEADERS[0], "created_at": "2026-10-02T09:15:00.000002Z"}]},
        {"turns": [{**HEADERS[0], "actor": None}], "records": ONE},
        {"projections_from": 0, "projections": [projection(1, "log", "u")]},
        {"projections_from": 0, "projections": [projection(1, "digest", "t")]},
        {"redactions_from": 0, "redactions": [{**seen, "actor": None}]},
        {"redactions_from": 0, "redactions": [{**seen, "turns": [1, 1]}]},
    ]
    for body in changed:
        refused(records, "restore_mismatch", at, **body)
        # A commit carrying the differing item publishes nothing.
        refused(records, "restore_mismatch", at, **body, commit=True)
    assert records.post("store/conversations") == {"conversations": []}
    # The same items in another spelling are the same items.
    same = {
        "turns": [{**HEADERS[0], "created_at": "2026-10-02T11:15:00.000001+02:00"}],
        "redactions_from": 0,
        "redactions": [{**seen, "created_at": "2026-10-05T10:00:00+02:00"}],
    }
    assert part(records, SESSION, ID, **same) == answer(ID, "open", at)


def test_a_commit_before_the_stream_is_whole_is_incomplete(records: Server) -> None:
    """`restore_incomplete`: the last turn lacks records, or a projection
    or an act names a turn the restore does not hold. Nothing of the
    refused request is kept, its content included."""
    part(records, SESSION, ID, turns=HEADERS[:1], records=ONE[:2])
    at = position(1, 2, False)
    refused(records, "restore_incomplete", at, commit=True)
    part(records, SESSION, ID, records=ONE[2:], turns=HEADERS[1:])
    at = position(3, 0, False)
    refused(records, "restore_incomplete", at, commit=True)
    part(records, SESSION, ID, records=THREE)
    at = position(3, 1, True)
    for body in [
        {"projections_from": 0, "projections": [projection(4, "log", "t")]},
        {"projections_from": 0, "projections": [EPOCH, projection(9, "epoch", "", 2)]},
        {
            "redactions_from": 0,
            "redactions": [act(SESSION, [2, 4], None, "2026-10-05T08:00:00Z")],
        },
    ]:
        refused(records, "restore_incomplete", at, **body, commit=True)
    # Staged first, the same items refuse the commit that follows them.
    stray = act(SESSION, [7], None, "2026-10-05T08:00:00Z")
    part(records, SESSION, ID, redactions_from=0, redactions=[stray])
    at = position(3, 1, True, 0, 1)
    refused(records, "restore_incomplete", at, commit=True)
    turns = records.post("conversation/read_turns", conversation_id=SESSION)
    assert turns["turns"] == []


def test_a_committed_restore_takes_nothing_more(records: Server) -> None:
    """`restore_committed`: other content, the final part without its
    commit, and a discard, each with the position the restore ended at."""
    part(records, SESSION, ID, **FIRST)
    done = part(records, SESSION, ID, **SECOND, commit=True)
    assert done["digest"] == DIGEST
    later: list[dict[str, Any]] = [
        {"discard": True},
        SECOND,
        FIRST,
        {"turns": [header(4, "2026-10-03T00:00:00Z", None, 0, 0, redacted=True)]},
        {**SECOND, "projections": [projection(3, "epoch", "other", 3)], "commit": True},
        {"redactions_from": 1, "redactions": [ERASED], "commit": True},
    ]
    for body in later:
        details = conflict(records, SESSION, "restore_committed", restore=ID, **body)
        assert details == {"position": END}
    receipt = answer(ID, "committed", END, digest=DIGEST)
    assert status(records, SESSION, ID) == receipt
    turns = records.post("conversation/read_turns", conversation_id=SESSION)
    assert turns["turns"] == TURNS
    # Another id finds the target taken.
    details = conflict(records, SESSION, "target_occupied", restore="again", **FIRST)
    assert details == {}


def aged(home: Path, conversation: str, restore: str) -> None:
    """Date a staged restore the wire opened to the year 2000, in its own
    state file."""
    state = home / ".records" / "restore" / conversation / restore / "state"
    text = state.read_text(encoding="utf-8")
    old, count = re.subn(
        r'"touched_at":"[^"]*"', '"touched_at":"2000-01-01T00:00:00Z"', text
    )
    assert count == 1
    state.write_text(old, encoding="utf-8")


def test_another_open_restore_is_named_and_a_stale_one_is_discarded(
    enabled: Binary,
) -> None:
    """`restore_open` carries the open restore's id. One untouched for
    longer than `NEOSIAN_DRAFT_SECONDS` is discarded by the next opening,
    its bytes reported in `freed_bytes`."""
    with serve(enabled) as server:
        part(server, SESSION, "first", turns=HEADERS[:1], records=ONE[:2])
        details = conflict(server, SESSION, "restore_open", restore="second", **FIRST)
        assert details == {"restore": "first"}
        code, data = raw(
            server, RESTORE, {"conversation_id": SESSION, "restore": "second", **FIRST}
        )
        assert (code, data) == (
            400,
            compact(
                {
                    "error": {
                        "code": "agent_conversation_conflict",
                        "message": "Conversation conflict on 'cc-7f3a': restore_open",
                        "details": {
                            "conversation_id": "cc-7f3a",
                            "reason": "restore_open",
                            "restore": "first",
                        },
                    }
                }
            ),
        )
        # A probe and a discard of an id that is not open open nothing; a
        # commit of nothing would open it, and is refused like any opening.
        assert status(server, SESSION, "second") == answer("second", "none", None)
        details = conflict(
            server, SESSION, "restore_open", restore="second", commit=True
        )
        assert details == {"restore": "first"}
        assert part(server, SESSION, "second", discard=True)["state"] == "none"
        # Another conversation is not in the way.
        assert part(server, "cc-else", "second", **FIRST)["state"] == "open"
        held = content_bytes(
            SESSION, "first", {"turns": HEADERS[:1], "records": ONE[:2]}
        ) + restore_bookkeeping(SESSION, "first")
        aged(enabled.home, SESSION, "first")
        opened = part(server, SESSION, "second", **FIRST)
        stored = content_bytes(SESSION, "second", FIRST) + restore_bookkeeping(
            SESSION, "second"
        )
        assert opened == answer("second", "open", position(1, 3, True), stored, held)
        assert status(server, SESSION, "first") == answer("first", "none", None)
        details = conflict(server, SESSION, "restore_open", restore="first", **FIRST)
        assert details == {"restore": "second"}


def test_an_occupied_target_refuses_the_first_part(records: Server) -> None:
    """`target_occupied`, with no details beyond the two shared keys: the
    conversation holds a visible turn, or projections alone."""
    records.post("conversation/append_turn", conversation_id=SESSION, messages=HI)
    for body in [
        FIRST,
        {"turns": HEADERS[:1]},
        {"commit": True},
        {**FIRST, **SECOND, "commit": True},
    ]:
        assert conflict(records, SESSION, "target_occupied", restore=ID, **body) == {}
    assert status(records, SESSION, ID) == answer(ID, "none", None)
    code, data = raw(
        records, RESTORE, {"conversation_id": SESSION, "restore": ID, **FIRST}
    )
    assert (code, data) == (
        400,
        b'{"error":{"code":"agent_conversation_conflict","message":'
        b'"Conversation conflict on \'cc-7f3a\': target_occupied","details":'
        b'{"conversation_id":"cc-7f3a","reason":"target_occupied"}}}',
    )
    entry = projection(1, "log", "t")
    records.post(
        "conversation/append_projections", conversation_id="p", entries=[entry]
    )
    assert conflict(records, "p", "target_occupied", restore=ID, **FIRST) == {}
    turns = records.post("conversation/read_turns", conversation_id=SESSION)
    assert [turn["turn"] for turn in turns["turns"]] == [1]


def test_an_append_between_the_parts_refuses_the_commit(records: Server) -> None:
    """Item 12: an `append_turn` from another client succeeds on the empty
    target while a restore is staged, and the commit then refuses with
    `target_occupied`, explicitly. The appended turn stays as it is."""
    part(records, SESSION, ID, **FIRST)
    new = records.post("conversation/append_turn", conversation_id=SESSION, messages=HI)
    assert new["turn"]["turn"] == 1
    for body in [{"commit": True}, {**SECOND, "commit": True}]:
        assert conflict(records, SESSION, "target_occupied", restore=ID, **body) == {}
    # The refused commit kept nothing of its content; the restore is open.
    at = position(1, 3, True)
    assert status(records, SESSION, ID) == answer(ID, "open", at)
    turns = records.post("conversation/read_turns", conversation_id=SESSION)
    assert turns["turns"] == [new["turn"]]
    gone = part(records, SESSION, ID, discard=True)
    assert gone == answer(ID, "discarded", at, freed=4_002)
    # A whole restore meanwhile does the same.
    other = "cc-whole"
    part(records, other, ID, **FIRST)
    whole = {**TURNS[2], "conversation_id": other, "turn": 1}
    records.post(
        "store/restore_conversation",
        conversation_id=other,
        turns=[whole],
        projections=[],
    )
    assert conflict(records, other, "target_occupied", restore=ID, commit=True) == {}
    # Projections alone occupy it too.
    third, entry = "cc-notes", projection(1, "log", "t")
    part(records, third, ID, **FIRST)
    records.post(
        "conversation/append_projections", conversation_id=third, entries=[entry]
    )
    assert conflict(records, third, "target_occupied", restore=ID, commit=True) == {}
    assert status(records, third, ID)["state"] == "open"


EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def empty_commit(server: Server) -> None:
    """A `commit` without content on a restore that does not exist
    publishes an empty committed restore: the position before any header,
    the unit digest of no item (SHA-256 of nothing, from an independent
    Python reference), the bookkeeping, the marker and the conversation row
    charged and the bookkeeping freed. The conversation is listed nowhere
    and its target stays empty, but the id is committed: content under it
    is `restore_committed` until the conversation is erased."""
    assert hashlib.sha256(b"").hexdigest() == EMPTY
    start = position(0, 0, True)
    first = part(server, SESSION, ID, commit=True)
    assert first == {
        "restore": "iecf9f710d465bc0ea0fe2fbba8915596",
        "state": "committed",
        "position": {
            "turn": 0,
            "seq": 0,
            "complete": True,
            "projections": 0,
            "redactions": 0,
        },
        "stored_bytes": 1637,
        "freed_bytes": 1071,
        "digest": EMPTY,
    }
    assert restore_bookkeeping(SESSION, ID) + marker(SESSION, ID) + parent(SESSION) == (
        1_637
    )
    receipt = answer(ID, "committed", start, digest=EMPTY)
    assert part(server, SESSION, ID, commit=True) == receipt
    assert status(server, SESSION, ID) == receipt
    assert server.post("store/conversations") == {"conversations": []}
    last = server.post("conversation/last_turn_number", conversation_id=SESSION)
    assert last == {"turn": 0}
    turns = server.post("conversation/read_turns", conversation_id=SESSION)
    assert turns["turns"] == []
    for body in [FIRST, {"turns": HEADERS[:1]}, {"discard": True}]:
        details = conflict(server, SESSION, "restore_committed", restore=ID, **body)
        assert details == {"position": start}
    # The target is not occupied: another id restores the conversation,
    # charged no second conversation row.
    other = "b"
    opened = part(server, SESSION, other, **FIRST)
    assert opened["stored_bytes"] == (
        content_bytes(SESSION, other, FIRST) + restore_bookkeeping(SESSION, other)
    )
    done = part(server, SESSION, other, **SECOND, commit=True)
    assert done["digest"] == DIGEST
    assert done["stored_bytes"] == (
        content_bytes(SESSION, other, SECOND) + marker(SESSION, other)
    )
    assert server.post("store/conversations") == {"conversations": [SESSION]}
    # The first id stays committed until the conversation is erased whole.
    assert status(server, SESSION, ID) == receipt
    assert server.post("conversation/redact_turns", conversation_id=SESSION) == {
        "count": 3
    }
    assert status(server, SESSION, ID) == answer(ID, "none", None)


def test_a_commit_of_nothing_publishes_an_empty_restore(records: Server) -> None:
    empty_commit(records)


def test_a_place_beside_an_empty_list_is_checked_like_any_other(
    records: Server,
) -> None:
    """Ruling R28: `projections_from` and `redactions_from` are checked
    against what the restore holds also when their list is empty. Past it
    is `restore_gap`; on a restore that does not exist the count is 0 and
    nothing is opened. A request whose lists are all empty, without
    `commit` or `discard`, is a probe."""

    def placed_at(projections: int, redactions: int, **more: Any) -> dict[str, Any]:
        return {
            "projections_from": projections,
            "projections": [],
            "redactions_from": redactions,
            "redactions": [],
            **more,
        }

    start = position(0, 0, True)
    nothing = answer(ID, "none", None)
    assert part(records, SESSION, ID, **placed_at(0, 0)) == nothing
    for body in [placed_at(1, 0), placed_at(0, 1), placed_at(5, 0, commit=True)]:
        details = conflict(records, SESSION, "restore_gap", restore=ID, **body)
        assert details == {"position": start}
    assert status(records, SESSION, ID) == nothing
    part(records, SESSION, ID, **FIRST, projections_from=0, projections=[EPOCH][:0])
    first = projection(1, "log", "t")
    part(records, SESSION, ID, projections_from=0, projections=[first])
    at = position(1, 3, True, 1)
    # At or below the count an empty list is a probe; past it, a gap.
    probes: list[dict[str, Any]] = [
        placed_at(0, 0),
        placed_at(1, 0),
        {"turns": [], "records": []},
    ]
    for body in probes:
        assert part(records, SESSION, ID, **body) == answer(ID, "open", at)
    for body in [placed_at(2, 0), placed_at(1, 1), placed_at(2, 0, commit=True)]:
        refused(records, "restore_gap", at, **body)
    # Beside content of another list, the empty list's place still counts.
    late = {"redactions_from": 1, "redactions": [], "turns": HEADERS[:1]}
    refused(records, "restore_gap", at, **late)
    done = part(records, SESSION, ID, **placed_at(1, 0, commit=True))
    assert done["state"] == "committed"
    receipt = answer(ID, "committed", at, digest=done["digest"])
    assert part(records, SESSION, ID, **placed_at(1, 0)) == receipt
    details = conflict(records, SESSION, "restore_gap", restore=ID, **placed_at(2, 0))
    assert details == {"position": at}
