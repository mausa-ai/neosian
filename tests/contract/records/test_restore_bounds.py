"""`store/restore_records` decoded strictly (DESIGN §40.54 items 8.2 and
12, rulings D3, R11 and R18): the body, and every header, record,
projection and trail act in it, each refusal in its own words. A refused
request stages nothing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.records.restoring import (
    HEADERS,
    ITEMS,
    RESTORE,
    answer,
    header,
    part,
    placed,
    position,
    projection,
    refused,
    status,
    why,
)
from tests.contract.records.searching import (
    post,
)
from tests.contract.records.support import (
    compact,
    record,
)
from tests.contract.support import Server

SESSION = "s-edge"
BASE: dict[str, Any] = {"conversation_id": SESSION, "restore": "r"}
HEAD = header(1, "2026-10-02T09:15:00Z", "a", 1, 1)
ITEM = placed(1, record(0, 0, "user", "prompt", "a"))
ENTRY = projection(1, "log", "t")
ACT: dict[str, Any] = {
    "conversation_id": SESSION,
    "turns": [1],
    "actor": "client:ops",
    "created_at": "2026-10-05T08:00:00Z",
}
INTEGER = "must be a non-negative integer"
TOGETHER = "come together"
DISCARD = "a discard carries no content and no commit"
GRANTS = """grant_format = 1
[[clients]]
actor = "client:contract"
operator = true
[[clients]]
actor = "client:reader"
[[clients.conversations]]
prefix = "s-"
read = true
append = true
"""


def less(item: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in item.items() if name != key}


def name(case: tuple[dict[str, Any], str]) -> str:
    return json.dumps(case[0])[:70]


BODIES: list[tuple[dict[str, Any], str]] = [
    ({"nonsense": 1}, "unknown parameter: 'nonsense'"),
    ({"draft": "d"}, "unknown parameter: 'draft'"),
    ({"actor": "me"}, "unknown parameter: 'actor'"),
    ({"Restore": "r"}, "unknown parameter: 'Restore'"),
    ({"restore": None}, "parameter 'restore' must be a string"),
    ({"restore": 7}, "parameter 'restore' must be a string"),
    ({"turns": None}, "parameter 'turns' must be a list"),
    ({"turns": {}}, "parameter 'turns' must be a list"),
    ({"records": "[]"}, "parameter 'records' must be a list"),
    ({"records": None}, "parameter 'records' must be a list"),
    ({"commit": None}, "parameter 'commit' must be a boolean"),
    ({"commit": 1}, "parameter 'commit' must be a boolean"),
    ({"commit": "true"}, "parameter 'commit' must be a boolean"),
    ({"discard": None}, "parameter 'discard' must be a boolean"),
    ({"discard": 0}, "parameter 'discard' must be a boolean"),
    (
        {"projections_from": None, "projections": []},
        f"parameter 'projections_from' {INTEGER}",
    ),
    (
        {"projections_from": -1, "projections": []},
        f"parameter 'projections_from' {INTEGER}",
    ),
    (
        {"projections_from": 0.5, "projections": []},
        f"parameter 'projections_from' {INTEGER}",
    ),
    (
        {"projections_from": True, "projections": []},
        f"parameter 'projections_from' {INTEGER}",
    ),
    (
        {"redactions_from": "0", "redactions": []},
        f"parameter 'redactions_from' {INTEGER}",
    ),
    (
        {"projections_from": 0, "projections": None},
        "parameter 'projections' must be a list",
    ),
    (
        {"redactions_from": 0, "redactions": {}},
        "parameter 'redactions' must be a list",
    ),
    # A list and the place of its first entry are named together.
    ({"projections": []}, f"projections and projections_from {TOGETHER}"),
    ({"projections": [ENTRY]}, f"projections and projections_from {TOGETHER}"),
    ({"projections_from": 0}, f"projections and projections_from {TOGETHER}"),
    ({"redactions": []}, f"redactions and redactions_from {TOGETHER}"),
    ({"redactions_from": 3}, f"redactions and redactions_from {TOGETHER}"),
    # Ruling R11: beside a discard a content field or `commit` counts when
    # named, also at its default.
    ({"discard": True, "turns": []}, DISCARD),
    ({"discard": True, "records": []}, DISCARD),
    ({"discard": True, "projections_from": 0, "projections": []}, DISCARD),
    ({"discard": True, "redactions_from": 0, "redactions": []}, DISCARD),
    ({"discard": True, "commit": False}, DISCARD),
    ({"discard": True, "commit": True}, DISCARD),
    ({"discard": True, "turns": [HEAD], "records": [ITEM]}, DISCARD),
]
HEADS: list[tuple[Any, str]] = [
    (None, "a turn header must be an object"),
    ([], "a turn header must be an object"),
    ({**HEAD, "zeta": 1}, 'unknown header field "zeta"'),
    ({**HEAD, "draft": "d"}, 'unknown header field "draft"'),
    ({**HEAD, "conversation_id": SESSION}, 'unknown header field "conversation_id"'),
    (less(HEAD, "turn"), 'header field "turn" is required'),
    (less(HEAD, "created_at"), 'header field "created_at" is required'),
    (less(HEAD, "redacted"), 'header field "redacted" is required'),
    (less(HEAD, "records"), 'header field "records" is required'),
    (less(HEAD, "bytes"), 'header field "bytes" is required'),
    ({**HEAD, "turn": 0}, 'header field "turn" must be an integer >= 1'),
    ({**HEAD, "turn": None}, f'header field "turn" {INTEGER}'),
    ({**HEAD, "turn": "1"}, f'header field "turn" {INTEGER}'),
    ({**HEAD, "turn": 1.5}, f'header field "turn" {INTEGER}'),
    ({**HEAD, "records": -1}, f'header field "records" {INTEGER}'),
    ({**HEAD, "bytes": True}, f'header field "bytes" {INTEGER}'),
    ({**HEAD, "actor": 7}, 'header field "actor" must be a string or null'),
    ({**HEAD, "redacted": None}, 'header field "redacted" must be a boolean'),
    ({**HEAD, "redacted": 0}, 'header field "redacted" must be a boolean'),
    (
        {**HEAD, "created_at": "2026-10-02T09:15:00"},
        'header field "created_at" must be an ISO-8601 time with an offset',
    ),
    (
        {**HEAD, "created_at": None},
        'header field "created_at" must be an ISO-8601 time with an offset',
    ),
    # Ruling R25: nothing of a time is dropped silently.
    (
        {**HEAD, "created_at": "2026-10-02T09:15:00.1234567Z"},
        'header field "created_at" has more than six fractional digits',
    ),
    (
        {**HEAD, "created_at": "0000-01-01T00:00:00+00:01"},
        'header field "created_at" is outside years 0000 to 9999 in UTC',
    ),
    (
        {**HEAD, "created_at": "9999-12-31T23:59:59.999999-00:01"},
        'header field "created_at" is outside years 0000 to 9999 in UTC',
    ),
    # A header against itself (the shared restore validation).
    (
        {**HEAD, "records": 0, "bytes": 0},
        "turn 1: an unredacted turn must have at least one record",
    ),
    ({**HEAD, "redacted": True}, "turn 1: a redacted turn has no records"),
    (
        {**HEAD, "redacted": True, "records": 0},
        "turn 1: a redacted turn has no records",
    ),
    ({**HEAD, "records": 0}, "turn 1: a turn without records holds no bytes"),
]
ENTRIES: list[tuple[Any, str]] = [
    ("epoch", "projections[0] must be an object"),
    ({**ENTRY, "extra": 1}, "unknown parameter: 'projections[0].extra'"),
    (
        {**ENTRY, "conversation_id": SESSION},
        "unknown parameter: 'projections[0].conversation_id'",
    ),
    (less(ENTRY, "turn"), "missing parameter: 'projections[0].turn'"),
    (less(ENTRY, "kind"), "missing parameter: 'projections[0].kind'"),
    (less(ENTRY, "text"), "missing parameter: 'projections[0].text'"),
    ({**ENTRY, "turn": None}, f"parameter 'projections[0].turn' {INTEGER}"),
    ({**ENTRY, "span": None}, f"parameter 'projections[0].span' {INTEGER}"),
    ({**ENTRY, "span": "1"}, f"parameter 'projections[0].span' {INTEGER}"),
    ({**ENTRY, "text": None}, "parameter 'projections[0].text' must be a string"),
    ({**ENTRY, "kind": 7}, "parameter 'projections[0].kind' must be a string"),
    ({**ENTRY, "turn": 0}, "projections[0]: turn must be >= 1, got 0"),
    (
        {**ENTRY, "span": 0},
        "projections[0]: span must satisfy 1 <= span <= turn, got span=0 for turn=1",
    ),
    (
        {**ENTRY, "span": 2},
        "projections[0]: span must satisfy 1 <= span <= turn, got span=2 for turn=1",
    ),
    (
        {**ENTRY, "kind": "note"},
        "projections[0]: kind must be one of ['digest', 'epoch', 'log'], got 'note'",
    ),
]
ACTS: list[tuple[Any, str]] = [
    (7, "redactions[1] must be an object"),
    ({**ACT, "count": 1}, "unknown parameter: 'redactions[1].count'"),
    ({**ACT, "path": None}, "unknown parameter: 'redactions[1].path'"),
    (
        less(ACT, "conversation_id"),
        "missing parameter: 'redactions[1].conversation_id'",
    ),
    (less(ACT, "turns"), "missing parameter: 'redactions[1].turns'"),
    (less(ACT, "created_at"), "missing parameter: 'redactions[1].created_at'"),
    (
        {**ACT, "conversation_id": None},
        "parameter 'redactions[1].conversation_id' must be a string",
    ),
    (
        {**ACT, "conversation_id": "s-other"},
        'redactions[1]: a trail act names conversation "s-other", not this one',
    ),
    ({**ACT, "turns": None}, "parameter 'redactions[1].turns' must be a list"),
    ({**ACT, "turns": []}, "redactions[1]: turns must name turn numbers from 1"),
    ({**ACT, "turns": [0]}, "redactions[1]: turns must name turn numbers from 1"),
    ({**ACT, "turns": [1, "2"]}, "redactions[1]: turns must name turn numbers from 1"),
    ({**ACT, "actor": 7}, "parameter 'redactions[1].actor' must be a string or null"),
    (
        {**ACT, "created_at": None},
        "parameter 'redactions[1].created_at' must be a string",
    ),
    (
        {**ACT, "created_at": "2026-10-05T08:00:00"},
        "redactions[1]: naive wire timestamp refused: '2026-10-05T08:00:00'",
    ),
    ({**ACT, "created_at": "soon"}, "redactions[1]: Invalid isoformat string: 'soon'"),
    (
        {**ACT, "created_at": "2026-10-05T08:00:00.0000001Z"},
        "redactions[1]: a time has at most six fractional digits:"
        " '2026-10-05T08:00:00.0000001Z'",
    ),
    (
        {**ACT, "created_at": "9999-12-31T23:59:59-00:01"},
        "redactions[1]: a time is within years 0000 to 9999 in UTC:"
        " '9999-12-31T23:59:59-00:01'",
    ),
]
ROWS: list[tuple[Any, str]] = [
    (None, "a record must be an object"),
    (less(ITEM, "turn"), 'record field "turn" is required'),
    ({**ITEM, "turn": 0}, 'record field "turn" must be an integer >= 1'),
    ({**ITEM, "turn": None}, 'record field "turn" must be an integer >= 1'),
    ({**ITEM, "tail": None}, 'unknown record field "tail"'),
    ({**ITEM, "truncated": False}, 'unknown record field "truncated"'),
    ({**ITEM, "seq": "0"}, f'record field "seq" {INTEGER}'),
    ({**ITEM, "text": None}, 'record field "text" is required'),
    ({**ITEM, "kind": "note"}, 'unknown record kind "note"'),
]


@pytest.mark.parametrize("case", BODIES, ids=map(name, BODIES))
def test_a_body_is_decoded_strictly(
    records: Server, case: tuple[dict[str, Any], str]
) -> None:
    """Item 8.2: an unknown field, a mistyped value and `null` are
    `400 value_error`; no field of this route accepts `null`."""
    more, message = case
    assert why(records, {**BASE, **more}) == message
    assert status(records, SESSION, "r") == answer("r", "none", None)


def test_every_item_is_decoded_strictly(records: Server) -> None:
    """Ruling D3: headers, records, projections and trail acts refuse an
    unknown key and a mistyped value, each in its own words, and a part
    holding one such item stages none of its items."""
    for head, message in HEADS:
        assert why(records, {**BASE, "turns": [head], "records": [ITEM]}) == message
    for row, message in ROWS:
        assert why(records, {**BASE, "turns": [HEAD], "records": [row]}) == message
    staged = {"turns": [HEAD], "records": [ITEM]}
    for entry, message in ENTRIES:
        body = {**BASE, **staged, "projections_from": 0, "projections": [entry]}
        assert why(records, body) == message, entry
    for one, message in ACTS:
        body = {**BASE, **staged, "redactions_from": 0, "redactions": [ACT, one]}
        assert why(records, body) == message, one
    assert status(records, SESSION, "r") == answer("r", "none", None)
    # What a part's lists must be among themselves.
    for more, message in [
        ({"turns": [HEADERS[2], HEADERS[0]]}, "a part's turns are ascending"),
        ({"turns": [HEAD, HEAD]}, "a part's turns are ascending"),
        (
            {"turns": [HEAD], "records": [ITEM, {**ITEM, "seq": 2}]},
            "a part's records follow their turns in seq order",
        ),
        (
            {"turns": [HEAD], "records": [{**ITEM, "text": "ab", "size": 2}]},
            "turn 1: its records reach 2 bytes, past its header's 1",
        ),
        (
            {"turns": [{**HEAD, "bytes": 5}], "records": [ITEM]},
            "turn 1: its records hold 1 bytes where its header says 5",
        ),
        (
            {"turns": [HEAD], "records": [ITEM, {**ITEM, "seq": 1, "message": 1}]},
            "turn 1: record 1 is past its header's 1 records",
        ),
    ]:
        assert why(records, {**BASE, **more}) == message
    assert status(records, SESSION, "r") == answer("r", "none", None)
    whole = part(
        records,
        SESSION,
        "r",
        **staged,
        projections_from=0,
        projections=[ENTRY],
        redactions_from=0,
        redactions=[ACT],
        commit=True,
    )
    assert whole["position"] == position(1, 1, True, 1, 1)


def test_the_ids_are_required_and_keep_their_grammars(records: Server) -> None:
    assert why(records, {"restore": "r"}) == "missing parameter: 'conversation_id'"
    assert why(records, {"conversation_id": SESSION}) == "missing parameter: 'restore'"
    assert why(records, {**BASE, "conversation_id": 7}) == (
        "parameter 'conversation_id' must be a string"
    )
    for bad in ["", "a/b", "..", "x" * 129]:
        body = {**BASE, "conversation_id": bad}
        refused(records, body, 400, "agent_conversation_id_invalid")
    for bad in ["", "r" * 65, "Restore", "a.b", "a b", "é", "a/b"]:
        quoted = json.dumps(bad, ensure_ascii=False)
        message = f"restore id {quoted} must be 1 to 64 of [a-z0-9_-]"
        assert why(records, {**BASE, "restore": bad}) == message
        assert why(records, {**BASE, "restore": bad, "turns": [HEAD]}) == message
    for good in ["a", "r" * 64, "0_-z", "-", "_"]:
        assert status(records, "x" * 128, good) == answer(good, "none", None)


def test_a_refusal_does_not_grow_with_the_request(records: Server) -> None:
    """Ruling R18: a value of the request repeated in a refusal is cut to
    its first 128 scalars."""
    long = "é" * 128 + "x" * 200_000
    staged = {"turns": [HEAD], "records": [ITEM]}
    for body, start in [
        ({**BASE, long: 1}, "unknown parameter: '"),
        ({**BASE, "restore": long}, 'restore id "'),
        ({**BASE, "turns": [{**HEAD, long: 1}]}, 'unknown header field "'),
        (
            {
                **BASE,
                **staged,
                "projections_from": 0,
                "projections": [{**ENTRY, long: 1}],
            },
            "unknown parameter: 'projections[0].",
        ),
        (
            {
                **BASE,
                **staged,
                "projections_from": 0,
                "projections": [{**ENTRY, "kind": long}],
            },
            "projections[0]: kind must be one of ['digest', 'epoch', 'log'], got '",
        ),
        (
            {**BASE, **staged, "redactions_from": 0, "redactions": [{**ACT, long: 1}]},
            "unknown parameter: 'redactions[0].",
        ),
        (
            {
                **BASE,
                **staged,
                "redactions_from": 0,
                "redactions": [{**ACT, "conversation_id": long}],
            },
            'redactions[0]: a trail act names conversation "',
        ),
        (
            {
                **BASE,
                **staged,
                "redactions_from": 0,
                "redactions": [{**ACT, "created_at": long}],
            },
            "redactions[0]: Invalid isoformat string: '",
        ),
        # Ruling R26: what a record's own decoder repeats is cut too.
        (
            {**BASE, "turns": [HEAD], "records": [{**ITEM, long: 1}]},
            'unknown record field "',
        ),
        (
            {**BASE, "turns": [HEAD], "records": [{**ITEM, "role": long}]},
            'unknown record role "',
        ),
        (
            {**BASE, "turns": [HEAD], "records": [{**ITEM, "kind": long}]},
            'unknown record kind "',
        ),
        (
            {**BASE, "turns": [HEAD], "records": [{**ITEM, "content": long}]},
            'unknown content form "',
        ),
    ]:
        reply = post(records, RESTORE, body)
        assert reply.status == 400
        message = reply.body["error"]["message"]
        assert message.startswith(start + "é" * 128), message[:80]
        assert message.removeprefix(start)[128:] in (
            "'",
            '"',
            '", not this one',
            '" must be 1 to 64 of [a-z0-9_-]',
        ), message[-40:]
        assert len(compact(reply.body)) < 512


def test_a_part_holds_at_most_2048_items_across_its_lists(records: Server) -> None:
    """Item 8.2: the four lists together; the count answers before any
    item is decoded."""
    many = "a part carries at most 2048 items"
    blank = [
        header(n, "2026-10-02T09:15:00Z", None, 0, 0, redacted=True)
        for n in range(1, ITEMS + 2)
    ]
    assert why(records, {**BASE, "turns": blank}) == many
    assert why(records, {**BASE, "turns": ["x"] * (ITEMS + 1)}) == many
    split = {
        "turns": blank[:1_000],
        "records": [],
        "projections_from": 0,
        "projections": [ENTRY] * 1_000,
        "redactions_from": 0,
        "redactions": [ACT] * 49,
    }
    assert why(records, {**BASE, **split}) == many
    assert status(records, SESSION, "r") == answer("r", "none", None)
    at = part(records, SESSION, "r", turns=blank[:ITEMS])
    assert at["position"] == position(ITEMS, 0, True)
