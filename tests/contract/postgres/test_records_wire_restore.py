"""`store/restore_records` on Postgres with records extension 1 (DESIGN
§40.54 item 12): the design's example with the literals of the FileStore
contract and the unit digest of an independent Python reference, the two
`target_occupied` cases, and a staged restore that no read sees. A sibling
of `test_records_wire.py`, whose server fixture it shares."""

from __future__ import annotations

from typing import Any

import pytest

from tests.contract.postgres.test_records_wire import Database, count, recording
from tests.contract.postgres.test_records_wire_search import age
from tests.contract.records.reading import read
from tests.contract.records.restoring import (
    DIGEST,
    END,
    FIRST,
    HEADERS,
    ID,
    ONE,
    RESTORE,
    SECOND,
    SESSION,
    STREAM,
    TURNS,
    answer,
    conflict,
    header,
    part,
    position,
    projection,
    refused,
    status,
    unit_digest,
    why,
)
from tests.contract.records.searching import WHOLE_STORE, forbidden, sweep
from tests.contract.records.test_restore import example
from tests.contract.records.test_restore_conflicts import empty_commit
from tests.contract.records.test_restore_reads import ABSENT, seen
from tests.contract.support import LIMITED, Binary

pytestmark = pytest.mark.postgres
HI = [{"role": "user", "content": "hi"}]


def test_the_design_example_restores_in_two_parts_with_its_literal_bytes(
    database: Database, binary: Binary
) -> None:
    """The same requests answer the same bytes, position and digest as on
    files: 4,002 then 2,077 stored, 1,071 freed, `6ef0ac03...`."""
    assert unit_digest(STREAM) == DIGEST
    with recording(database, binary) as (server, connection, name):
        receipt = example(server)
        assert receipt["digest"] == DIGEST
        assert count(connection, name, "turns") == 3
        # Content after the commit, and another id on the taken target.
        details = conflict(server, SESSION, "restore_committed", restore=ID, **FIRST)
        assert details == {"position": END}
        assert conflict(server, SESSION, "target_occupied", restore="b", **FIRST) == {}
        inside = {"conversation_id": "allowed-1", "restore": ID}
        for body in [{}, inside, {**inside, "discard": True}]:
            assert forbidden(server, RESTORE, body, LIMITED) == WHOLE_STORE
        assert why(server, {"conversation_id": SESSION, "restore": ID, "x": 1}) == (
            "unknown parameter: 'x'"
        )


def test_an_occupied_target_refuses_the_first_part(
    database: Database, binary: Binary
) -> None:
    """`target_occupied` with no further details: a visible turn, or
    projections alone, and nothing is opened."""
    with recording(database, binary) as (server, _, _):
        server.post("conversation/append_turn", conversation_id=SESSION, messages=HI)
        for body in [
            FIRST,
            {"turns": HEADERS[:1]},
            {**FIRST, **SECOND, "commit": True},
        ]:
            details = conflict(server, SESSION, "target_occupied", restore=ID, **body)
            assert details == {}
        assert status(server, SESSION, ID) == answer(ID, "none", None)
        entry = projection(1, "log", "t")
        server.post(
            "conversation/append_projections", conversation_id="p", entries=[entry]
        )
        assert conflict(server, "p", "target_occupied", restore=ID, **FIRST) == {}
        turns = server.post("conversation/read_turns", conversation_id=SESSION)
        assert [turn["turn"] for turn in turns["turns"]] == [1]


def test_an_append_between_the_parts_refuses_the_commit(
    database: Database, binary: Binary
) -> None:
    """An `append_turn` lands on the empty target while a restore is
    staged; the commit refuses with `target_occupied` and keeps nothing of
    its own content; the appended turn stays and the restore is discarded
    with every byte it held."""
    with recording(database, binary) as (server, _, _):
        assert part(server, SESSION, ID, **FIRST)["stored_bytes"] == 4_002
        new = server.post(
            "conversation/append_turn", conversation_id=SESSION, messages=HI
        )
        assert new["turn"]["turn"] == 1
        for body in [{"commit": True}, {**SECOND, "commit": True}]:
            details = conflict(server, SESSION, "target_occupied", restore=ID, **body)
            assert details == {}
        at = position(1, 3, True)
        assert status(server, SESSION, ID) == answer(ID, "open", at)
        turns = server.post("conversation/read_turns", conversation_id=SESSION)
        assert turns["turns"] == [new["turn"]]
        gone = part(server, SESSION, ID, discard=True)
        assert gone == answer(ID, "discarded", at, freed=4_002)


def test_a_staged_restore_is_absent_until_its_commit_under_any_boundaries(
    database: Database, binary: Binary
) -> None:
    """Every item staged one request at a time and repeated whole: nothing
    is visible to any read, listing or search before the commit, a repeat
    stores nothing, and the digest is the example's."""
    with recording(database, binary) as (server, _, _):
        assert seen(server, SESSION) == ABSENT
        part(server, SESSION, ID, turns=HEADERS[:1])
        for item in ONE:
            part(server, SESSION, ID, records=[item])
        assert part(server, SESSION, ID, **FIRST)["stored_bytes"] == 0
        staged = part(server, SESSION, ID, **SECOND)
        assert staged == answer(ID, "open", END, 2_077 - 303 - 263)
        assert seen(server, SESSION) == ABSENT
        details = conflict(
            server,
            SESSION,
            "restore_gap",
            restore=ID,
            **{
                "redactions_from": 2,
                "redactions": SECOND["redactions"],
            },
        )
        assert details == {"position": END}
        done = part(server, SESSION, ID, commit=True)
        assert done == answer(ID, "committed", END, 303 + 263, 1_071, DIGEST)
        turns = server.post("conversation/read_turns", conversation_id=SESSION)
        assert turns["turns"] == TURNS
        assert read(server, SESSION)["turns"] == HEADERS


def test_a_stale_restore_is_swept_and_a_probe_is_not_a_touch(
    database: Database, binary: Binary
) -> None:
    """Items 12 and 13 and ruling R19: a staged restore aged in the
    database is still stale after a probe and is swept with its bytes; a
    repeated part makes it fresh. A header past the store's bound is the
    store's own 413."""
    low = {"NEOSIAN_MAX_RECORD_TURN_BYTES": "65536"}
    with recording(database, binary, **low) as (server, connection, name):
        big = header(2, "2026-10-02T09:20:00Z", "a", 2, 65_537)
        error = refused(
            server, {"conversation_id": SESSION, "restore": ID, "turns": [big]}, 413
        )
        assert error["message"] == (
            "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded:"
            " 65537 > 65536; this store does not keep a turn this large"
        )
        assert status(server, SESSION, ID) == answer(ID, "none", None)
        part(server, SESSION, ID, **FIRST)
        age(connection, name, SESSION)
        at = position(1, 3, True)
        assert status(server, SESSION, ID) == answer(ID, "open", at)
        row = {
            "conversation_id": SESSION,
            "drafts": 0,
            "restores": 1,
            "freed_bytes": 4_002,
        }
        assert sweep(server)["conversations"] == [row]
        assert part(server, SESSION, ID, **FIRST) == answer(ID, "open", at)
        assert sweep(server)["conversations"] == []
        age(connection, name, SESSION)
        assert sweep(server, write=True)["conversations"] == [row]
        assert status(server, SESSION, ID) == answer(ID, "none", None)
        assert part(server, SESSION, ID, **FIRST)["stored_bytes"] == 4_002


def test_a_commit_of_nothing_publishes_an_empty_restore(
    database: Database, binary: Binary
) -> None:
    """The FileStore contract word for word: the same literal answer,
    charges, digest of no item and later `restore_committed`."""
    with recording(database, binary) as (server, _, _):
        empty_commit(server)


def test_an_empty_lists_place_is_checked(database: Database, binary: Binary) -> None:
    """Ruling R28 as on files: a `_from` beside an empty list is checked
    against what the restore holds, and nothing is opened by a refusal or
    by a request whose lists are all empty."""
    with recording(database, binary) as (server, connection, name):
        nothing = answer(ID, "none", None)
        assert part(server, SESSION, ID, turns=[], records=[]) == nothing
        empty = {"projections_from": 1, "projections": []}
        details = conflict(server, SESSION, "restore_gap", restore=ID, **empty)
        assert details == {"position": position(0, 0, True)}
        details = conflict(
            server, SESSION, "restore_gap", restore=ID, **empty, commit=True
        )
        assert details == {"position": position(0, 0, True)}
        assert count(connection, name, "record_drafts") == 0
        assert part(server, SESSION, ID, **FIRST)["stored_bytes"] == 4_002
        at = position(1, 3, True)
        late = {"redactions_from": 1, "redactions": []}
        details = conflict(server, SESSION, "restore_gap", restore=ID, **late)
        assert details == {"position": at}
        here = {"redactions_from": 0, "redactions": []}
        assert part(server, SESSION, ID, **here) == answer(ID, "open", at)
        done = part(server, SESSION, ID, **SECOND, commit=True)
        assert (done["stored_bytes"], done["digest"]) == (2_077, DIGEST)


def test_values_past_the_database_types_are_refused(
    database: Database, binary: Binary
) -> None:
    """Ruling R29: PostgreSQL refuses a NUL in an actor, a text, a call id
    and a name, and a number past its column type, as `value_error`; a
    FileStore takes both. Nothing of the refused part is staged."""
    with recording(database, binary) as (server, _, _):
        base = {"conversation_id": SESSION, "restore": ID}
        null = "restore field {} contains a null character"
        wide = "restore field {} is outside the PostgreSQL storage range"
        head = HEADERS[0]
        cases: list[tuple[dict[str, Any], str]] = [
            ({"turns": [{**head, "actor": "a\u0000b"}]}, null.format("actor")),
            (
                {"turns": [head], "records": [{**ONE[0], "text": "x" * 31 + "\u0000"}]},
                null.format("text"),
            ),
            ({"turns": [{**head, "turn": 2**31}]}, wide.format("turn")),
            ({"turns": [{**head, "records": 2**31}]}, wide.format("records")),
        ]
        for more, message in cases:
            assert why(server, {**base, **more}) == message
        assert status(server, SESSION, ID) == answer(ID, "none", None)
        top = {**head, "turn": 2**31 - 1, "redacted": True, "records": 0, "bytes": 0}
        opened = part(server, SESSION, ID, turns=[top])
        assert opened["position"] == position(2**31 - 1, 0, True)
