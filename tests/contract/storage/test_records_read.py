"""Optional turn records v1: `conversation/read_records`.

A conversation is read as records under a view, a page at a time. These
cases write one turn of all eight kinds in parts and append the same
messages whole, and check that both answer the same records, every view,
heads and tails, a piece fetched whole, pages checked for their bound and
for being full, continuations, the refusals of the request table and the
answer's published bound measured on its bytes. A page's fill is computed
here from the answer alone. Fixtures are ASCII. Absent support is an
explicit skip under the full and base profiles.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.records.reading import (
    DEFAULT_PAGE,
    EVERY,
    EVERY_BYTES,
    EVERY_MESSAGES,
    FRAMING,
    KINDS,
    READ,
    RECORD_BYTES,
    all_answered,
    answered,
    cost,
    head_of,
    header,
    joined,
    pages,
    post,
    read,
    tail_of,
)
from tests.contract.records.support import record, write
from tests.contract.storage.support import (
    Storage,
    refused,
    require_records,
    sent as answer_bytes,
)

OTHER_REQUEST = "page continuation belongs to a different request"
EMPTY: dict[str, Any] = {"turns": [], "records": [], "continuation": None}
# One tool result of 1,500 bytes in three chunks, between a prompt and a reply.
RESULT = "".join(f"line {number:04d}\n" for number in range(150))
CHUNKED = [
    record(0, 0, "user", "prompt", "run it"),
    record(
        1,
        1,
        "assistant",
        "tool_call",
        '{"command":"run"}',
        content="none",
        call=0,
        call_id="c1",
        name="Bash",
    ),
    *[
        record(
            2 + index,
            2,
            "tool",
            "tool_result",
            RESULT[start : start + 500],
            content="text" if index == 0 else None,
            call_id="c1",
            chunk=index,
            size=1_500,
        )
        for index, start in enumerate(range(0, 1_500, 500))
    ],
    record(5, 3, "assistant", "reply", "done"),
]


@pytest.fixture(autouse=True)
def records_capability(storage: Storage) -> None:
    require_records(storage)


def sealed(storage: Storage, draft: str, items: list[Any]) -> dict[str, Any]:
    """One turn written in two parts; its header."""
    half = len(items) // 2
    write(storage.server, storage.conversation, draft, items[:half])
    answer = write(storage.server, storage.conversation, draft, items[half:], seal=True)
    return dict(answer["turn"])


def test_a_turn_reads_as_the_same_records_however_it_is_stored(
    storage: Storage,
) -> None:
    server, session = storage.server, storage.conversation
    first = sealed(storage, "every", EVERY)
    assert (first["turn"], first["records"], first["bytes"]) == (1, 9, EVERY_BYTES)
    storage.turns("append_turn", messages=EVERY_MESSAGES)
    turns = storage.turns("read_turns")["turns"]
    # Both turns are the same messages to a whole read.
    assert [turn["messages"] for turn in turns] == [EVERY_MESSAGES, EVERY_MESSAGES]
    second = header(turns[1], 9, EVERY_BYTES)
    page = read(server, session)
    assert page == {
        "turns": [first, second],
        "records": all_answered(1, EVERY) + all_answered(2, EVERY),
        "continuation": None,
    }
    assert read(storage.peer, session) == page
    # The selections of the table.
    only = {"turns": [second], "records": all_answered(2, EVERY), "continuation": None}
    assert read(server, session, turn=2) == only
    assert read(server, session, after=1) == only
    assert read(server, session, after=0, through=2) == page
    assert read(server, session, through=1)["turns"] == [first]
    assert read(server, session, turn=2, seq=7)["records"] == all_answered(2, EVERY[7:])
    # A turn or a range holding nothing, and a conversation that is absent.
    assert read(server, session, turn=3) == EMPTY
    assert read(server, session, after=2) == EMPTY
    assert read(server, session + "-absent") == EMPTY
    # A `seq` past the last record answers the header and no record.
    assert read(server, session, turn=1, seq=9) == {**EMPTY, "turns": [first]}


def test_a_view_selects_kinds_and_every_header_is_answered(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    first = sealed(storage, "every", EVERY)
    spoken = [item for item in EVERY if item["kind"] in ("prompt", "reply")]
    view = read(server, session, kinds=["prompt", "reply"])
    assert view == {
        "turns": [first],
        "records": all_answered(1, spoken),
        "continuation": None,
    }
    # `kinds` is a set: order and repeats do not matter.
    assert read(server, session, kinds=["reply", "prompt", "reply"]) == view
    assert read(server, session, kinds=KINDS)["records"] == all_answered(1, EVERY)
    for kind in KINDS:
        chosen = [item for item in EVERY if item["kind"] == kind]
        assert chosen, kind
        assert read(server, session, kinds=[kind]) == {
            "turns": [first],
            "records": all_answered(1, chosen),
            "continuation": None,
        }
    # Headers only.
    assert read(server, session, kinds=[]) == {**EMPTY, "turns": [first]}


def test_heads_cut_a_long_piece_and_a_piece_is_fetched_whole(
    storage: Storage,
) -> None:
    server, session = storage.server, storage.conversation
    turn = sealed(storage, "chunked", CHUNKED)
    assert (turn["records"], turn["bytes"]) == (6, 6 + 17 + 1_500 + 4)
    head = answered(
        1,
        CHUNKED[2],
        text=head_of(RESULT, 64),
        tail=tail_of(RESULT, 64),
        truncated=True,
    )
    assert head["size"] == 1_500 and head["chunk"] == 0
    assert len(head["text"]) < 64 > len(head["tail"])
    light = read(server, session, heads={"tool_result": 64})
    assert light == {
        "turns": [turn],
        "records": [
            answered(1, CHUNKED[0]),
            answered(1, CHUNKED[1]),
            head,
            answered(1, CHUNKED[5]),
        ],
        "continuation": None,
    }
    # A piece that encodes to at most twice the bytes is answered whole.
    whole = all_answered(1, CHUNKED)
    assert read(server, session, heads={"tool_result": 32_768})["records"] == whole
    # A head of another kind leaves this piece alone.
    assert read(server, session, heads={"reply": 64})["records"] == whole
    # The piece itself, by the `seq` of any of its chunks; `heads` is ignored.
    piece = {"turns": [turn], "records": whole[2:5], "continuation": None}
    for seq in (2, 3, 4):
        assert read(server, session, turn=1, seq=seq, piece=True) == piece
    assert (
        read(server, session, turn=1, seq=3, piece=True, heads={"tool_result": 64})
        == piece
    )
    assert read(server, session, turn=1, seq=5, piece=True)["records"] == whole[5:]
    assert read(server, session, turn=1, seq=2, piece=True, kinds=[]) == {
        **EMPTY,
        "turns": [turn],
    }
    paged = pages(server, session, 1, turn=1, seq=2, piece=True)
    assert len(paged) == 3 and joined(paged) == ([turn], whole[2:5])


def test_pages_hold_what_fits_and_continuations_resume(storage: Storage) -> None:
    server, session = storage.server, storage.conversation
    first = sealed(storage, "one", CHUNKED)
    second = sealed(storage, "two", EVERY)
    third = sealed(storage, "three", CHUNKED)
    headers = [first, second, third]
    records = (
        all_answered(1, CHUNKED) + all_answered(2, EVERY) + all_answered(3, CHUNKED)
    )
    whole = read(server, session)
    assert whole == {"turns": headers, "records": records, "continuation": None}
    # One group a page, a bound inside a turn, and the default.
    smallest = pages(server, session, 1)
    assert len(smallest) == len(records)
    assert all(len(page["records"]) == 1 for page in smallest)
    for asked in (1, 700, 2_000, None):
        assert joined(pages(server, session, asked)) == (headers, records)
    middle = pages(server, session, 700)
    assert 3 < len(middle) < len(records)
    # A page that ends inside a turn is followed by one repeating its header.
    inside = [
        (before, after)
        for before, after in zip(middle, middle[1:], strict=False)
        if before["records"][-1]["turn"] == after["records"][0]["turn"]
    ]
    assert inside and all(
        before["turns"][-1] == after["turns"][0] for before, after in inside
    )
    # `page_bytes` may differ from page to page.
    start = smallest[0]["continuation"]
    rest = pages(server, session, 2_000, start=start)
    assert joined([smallest[0], *rest]) == (headers, records)
    # A view pages too, and a header without records is a group of its own.
    bare = pages(server, session, 1, kinds=[])
    assert [page["turns"] for page in bare] == [[turn] for turn in headers]
    replies = pages(server, session, 1, kinds=["reply"])
    assert joined(replies)[1] == [item for item in records if item["kind"] == "reply"]
    # A continuation binds its selection, never the page size.
    token = middle[0]["continuation"]
    others: list[dict[str, Any]] = [
        {"turn": 1},
        {"after": 1},
        {"through": 2},
        {"kinds": ["prompt"]},
        {"heads": {"tool_result": 64}},
    ]
    for other in others:
        reply = post(
            server, {"conversation_id": session, "continuation": token, **other}
        )
        refused(reply, "value_error")
        assert reply.body["error"]["message"] == OTHER_REQUEST
    elsewhere = post(
        server, {"conversation_id": session + "-other", "continuation": token}
    )
    refused(elsewhere, "value_error")
    assert elsewhere.body["error"]["message"] == OTHER_REQUEST
    refused(
        post(server, {"conversation_id": session, "continuation": "made-up"}),
        "value_error",
    )


def test_an_answer_is_within_its_published_bound(storage: Storage) -> None:
    """At most `max(page_bytes, 81,920 + h) + 4,160` bytes as sent, `h`
    being the page's first header; the service may serve a smaller page."""
    server, session = storage.server, storage.conversation
    # No turn here is past 65,536 bytes, the least bound a store may set.
    longest = [record(0, 0, "user", "prompt", "a" * 65_534)]
    assert write(server, session, "long", longest, seal=True)["turn"]["bytes"] == 65_534
    reply = [record(0, 0, "assistant", "reply", "b" * 9_000)]
    write(server, session, "reply", reply, seal=True)
    sealed(storage, "short", CHUNKED)
    for asked in (1, 4_096, 70_000, DEFAULT_PAGE):
        continuation = None
        while True:
            body: dict[str, Any] = {"conversation_id": session, "page_bytes": asked}
            if continuation is not None:
                body["continuation"] = continuation
            status, sent = answer_bytes(server, READ, body)
            assert status == 200
            page = json.loads(sent)
            first = cost(page["turns"][0])
            assert len(sent) <= max(asked, RECORD_BYTES + first) + FRAMING
            continuation = page["continuation"]
            if continuation is None:
                break
    # A page past its size holds exactly one record and its turn's header.
    lone = read(server, session, page_bytes=1)
    assert (len(lone["turns"]), len(lone["records"])) == (1, 1)
    assert lone["records"][0]["text"] == "a" * 65_534


def test_a_redacted_turn_answers_its_header_and_no_record(storage: Storage) -> None:
    storage.require("erasable")
    server, session = storage.server, storage.conversation
    first = sealed(storage, "kept", EVERY)
    second = sealed(storage, "erased", CHUNKED)
    assert storage.turns("redact_turns", turns=[2]) == {"count": 1}
    blank = {**second, "redacted": True, "records": 0, "bytes": 0}
    assert read(server, session) == {
        "turns": [first, blank],
        "records": all_answered(1, EVERY),
        "continuation": None,
    }
    assert read(server, session, turn=2) == {**EMPTY, "turns": [blank]}
    assert read(server, session, turn=2, seq=2, piece=True) == {
        **EMPTY,
        "turns": [blank],
    }
    assert storage.turns("read_turns")["turns"][1]["messages"] == []


@pytest.mark.parametrize(
    "wrong",
    [
        {"note": 1},
        {"turn": 0},
        {"turn": "1"},
        {"turn": None},
        {"turn": 1, "after": 0},
        {"turn": 1, "after": 1},
        {"turn": 1, "through": 1},
        {"after": -1},
        {"through": 0},
        {"seq": 0},
        {"turn": 1, "seq": -1},
        {"piece": True},
        {"piece": False},
        {"turn": 1, "piece": True},
        {"turn": 1, "seq": 0, "piece": 1},
        {"kinds": ["thought"]},
        {"kinds": "prompt"},
        {"kinds": None},
        {"heads": {"prompt": 0}},
        {"heads": {"prompt": 32_769}},
        {"heads": {"thought": 64}},
        {"heads": ["prompt"]},
        {"page_bytes": 0},
        {"page_bytes": "1"},
        {"page_bytes": None},
        {"continuation": ""},
        {"continuation": "x" * 4_097},
        {"continuation": None},
    ],
)
def test_each_rule_of_the_request_table_refuses(
    storage: Storage, wrong: dict[str, Any]
) -> None:
    reply = post(storage.server, {"conversation_id": storage.conversation, **wrong})
    refused(reply, "value_error")
