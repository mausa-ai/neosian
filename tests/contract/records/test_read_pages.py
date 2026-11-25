"""`conversation/read_records`, its pages (DESIGN §40.54 items 10 and 14):
pages by bytes inside a turn, continuations that resume exactly and bind
their selection, the server's page limit, and the answered-bytes bound
measured on the raw answer."""

from __future__ import annotations

from typing import Any

import pytest

from tests.contract.records.reading import (
    DEFAULT_PAGE,
    EVERY,
    EVERY_MESSAGES,
    FRAMING,
    RECORD_BYTES,
    all_answered,
    cost,
    forged,
    groups,
    joined,
    message,
    pages,
    raw,
    read,
)
from tests.contract.records.support import compact, record, write
from tests.contract.support import Binary, Server, serve

SESSION = "s-pages"
OTHER = "page continuation belongs to a different request"
TEXT: dict[str, Any] = {"reasoning": None, "tool_calls": [], "tool_call_id": None}


def chunked(seq: int, index: int, kind: str, role: str, texts: list[str]) -> list[Any]:
    size = sum(len(text.encode()) for text in texts)
    return [
        record(
            seq + chunk,
            index,
            role,
            kind,
            text,
            content="text" if chunk == 0 else None,
            chunk=chunk,
            size=size,
        )
        for chunk, text in enumerate(texts)
    ]


WIDE = [
    *chunked(0, 0, "prompt", "user", ["p" * 300, "q" * 200]),
    *chunked(2, 1, "reply", "assistant", ["r" * 700, "s" * 650, "t" * 90, "u" * 1_500]),
    *chunked(6, 2, "prompt", "user", ["é\n" * 40]),
    *chunked(7, 3, "reply", "assistant", ["v" * 10, "w" * 2_000, "x" * 400]),
]
SELECTIONS: list[dict[str, Any]] = [
    {},
    {"kinds": ["prompt"]},
    {"kinds": ["reply", "attachment"]},
    {"kinds": []},
    {"after": 1, "through": 3},
    {"turn": 3},
    {"turn": 3, "seq": 3},
    {"turn": 3, "seq": 4, "piece": True},
    {"heads": {"reply": 100}},
    {"kinds": ["reply"], "heads": {"reply": 100}, "after": 2},
]


def ask(server: Server, body: dict[str, Any], **more: Any) -> dict[str, Any]:
    """One page of the body's selection."""
    rest = {key: value for key, value in body.items() if key != "conversation_id"}
    return read(server, body["conversation_id"], **rest, **more)


def conversation(server: Server) -> None:
    """Five turns: all eight kinds stored whole, a short turn, a wide turn
    of ten records, a redacted turn and all eight kinds as records."""
    for messages in [
        EVERY_MESSAGES,
        [{"role": "user", "content": "short", **TEXT}],
    ]:
        server.post(
            "conversation/append_turn", conversation_id=SESSION, messages=messages
        )
    write(server, SESSION, "wide", WIDE, seal=True)
    gone = [{"role": "user", "content": "gone", **TEXT}]
    server.post("conversation/append_turn", conversation_id=SESSION, messages=gone)
    server.post("conversation/redact_turns", conversation_id=SESSION, turns=[4])
    write(server, SESSION, "every", EVERY, seal=True)


@pytest.mark.parametrize("selection", SELECTIONS, ids=lambda s: compact(s).decode())
def test_pages_of_every_size_reassemble_the_read(
    records: Server, selection: dict[str, Any]
) -> None:
    """At 1 byte a page is one group; at a bound inside a turn the next
    page repeats that turn's header; at the default the answer is whole.
    Every page is checked for its bound and for being full."""
    conversation(records)
    whole = read(records, SESSION, **selection)
    assert whole["continuation"] is None
    expected = (whole["turns"], whole["records"])
    assert pages(records, SESSION, None, **selection) == [whole]
    single = pages(records, SESSION, 1, **selection)
    assert joined(single) == expected
    assert all(len(groups(page)) == 1 for page in single) or expected == ([], [])
    assert len(single) == max(len(groups(whole)), 1)
    for bound in [300, 1_000, 2_500, 5_000, 20_000]:
        assert joined(pages(records, SESSION, bound, **selection)) == expected


def test_a_page_ends_between_records_inside_a_turn(records: Server) -> None:
    """The wide turn's header costs 123 bytes with its separator and its ten
    records 491, 389, 896, 844, 284, 1,694, 351, 206, 2,194 and 594. Pages
    of 1,500 therefore hold these records, each page repeating the header,
    and the two records longer than a page stand alone on theirs."""
    conversation(records)
    whole = read(records, SESSION, turn=3)
    header = whole["turns"][0]
    wide = all_answered(3, WIDE)
    assert whole["records"] == wide
    costs = [491, 389, 896, 844, 284, 1_694, 351, 206, 2_194, 594]
    assert (cost(header), [cost(item) for item in wide]) == (123, costs)
    answers = pages(records, SESSION, 1_500, turn=3)
    assert [[item["seq"] for item in page["records"]] for page in answers] == [
        [0, 1],
        [2],
        [3, 4],
        [5],
        [6, 7],
        [8],
        [9],
    ]
    assert all(page["turns"] == [header] for page in answers)
    assert [sum(groups(page)) for page in answers] == [
        123 + 491 + 389,
        123 + 896,
        123 + 844 + 284,
        123 + 1_694,
        123 + 351 + 206,
        123 + 2_194,
        123 + 594,
    ]
    assert [page["continuation"] is None for page in answers] == [False] * 6 + [True]


def test_a_continuation_resumes_exactly_under_any_page_size(records: Server) -> None:
    """`page_bytes` is not part of what a continuation binds."""
    conversation(records)
    whole = read(records, SESSION)
    first = read(records, SESSION, page_bytes=600)
    token = first["continuation"]
    assert isinstance(token, str) and first["records"]
    seen = len(first["records"])
    rest = read(records, SESSION, continuation=token)
    assert rest["continuation"] is None
    assert first["records"] + rest["records"] == whole["records"]
    assert rest["turns"][0] == first["turns"][-1], "the turn it goes on inside"
    again = read(records, SESSION, continuation=token, page_bytes=1)
    assert again["records"] == whole["records"][seen : seen + 1]
    # Sent twice it answers twice the same: a continuation is not consumed.
    assert read(records, SESSION, continuation=token) == rest
    # Kinds in another order and repeated are the same selection.
    kinds = ["reply", "prompt"]
    paged = read(records, SESSION, kinds=kinds, page_bytes=600)
    same = read(
        records,
        SESSION,
        kinds=["prompt", "reply", "prompt"],
        continuation=paged["continuation"],
    )
    assert same["continuation"] is None
    chat = read(records, SESSION, kinds=kinds)["records"]
    assert paged["records"] + same["records"] == chat


@pytest.mark.parametrize(
    "other",
    [
        {"conversation_id": "s-other"},
        {"after": 1},
        {"through": 5},
        {"turn": 1, "after": None},
        {"kinds": ["prompt", "reply", "system"]},
        {"kinds": ["prompt"]},
        {"kinds": None},
        {"kinds": []},
        {"heads": {"reply": 51}},
        {"heads": {"reply": 50, "prompt": 50}},
        {"heads": None},
    ],
    ids=lambda other: compact(other).decode(),
)
def test_a_continuation_of_another_selection_is_refused(
    records: Server, other: dict[str, Any]
) -> None:
    conversation(records)
    mine: dict[str, Any] = {
        "conversation_id": SESSION,
        "after": 0,
        "kinds": ["prompt", "reply"],
        "heads": {"reply": 50},
    }
    first = ask(records, mine, page_bytes=1)
    token = first["continuation"]
    assert isinstance(token, str)
    changed = {**mine, **other, "continuation": token}
    asked = {key: value for key, value in changed.items() if value is not None}
    assert message(records, asked) == OTHER
    resumed = ask(records, mine, continuation=token, page_bytes=1)
    assert resumed["records"][0]["seq"] == 3


def test_a_continuation_binds_seq_and_piece_too(records: Server) -> None:
    conversation(records)
    base: dict[str, Any] = {"conversation_id": SESSION, "turn": 3, "seq": 2}
    first = ask(records, base, page_bytes=1)
    token = first["continuation"]
    assert isinstance(token, str)
    others: list[dict[str, Any]] = [
        {"seq": 3},
        {"piece": True},
        {"seq": None},
        {"turn": 2},
    ]
    for other in others:
        changed = {**base, **other, "continuation": token}
        asked = {key: value for key, value in changed.items() if value is not None}
        assert message(records, asked) == OTHER, other
    fetched = ask(records, base, piece=True, page_bytes=1)
    assert message(records, {**base, "continuation": fetched["continuation"]}) == OTHER


def forgeries(server: Server, session: str) -> None:
    """Ruling R12, on a conversation whose turn 3 is `WIDE`: a continuation
    made up for a place outside the request's own selection is refused like
    one of another request; inside it, it reads from that place."""

    def made_up(at: int, record: int, /, **selection: Any) -> dict[str, Any]:
        """The request's own continuation, moved to turn `at`, record `record`."""
        own = read(server, session, **selection, page_bytes=1)["continuation"]
        assert isinstance(own, str)
        return {
            "conversation_id": session,
            **selection,
            "continuation": forged(own, at, record),
        }

    def seqs(body: dict[str, Any]) -> list[tuple[int, int]]:
        page = ask(server, body)
        return [(item["turn"], item["seq"]) for item in page["records"]]

    # `turn: 3` reads turn 3 and no other, whatever the continuation says.
    for turn in [1, 2, 4, 5]:
        assert message(server, made_up(turn, 0, turn=3)) == OTHER
    assert seqs(made_up(3, 8, turn=3)) == [(3, 8), (3, 9)]
    # A range keeps its ends.
    for turn in [1, 2, 4, 5]:
        assert message(server, made_up(turn, 0, after=2, through=3)) == OTHER
    assert seqs(made_up(3, 9, after=2, through=3)) == [(3, 9)]
    assert message(server, made_up(1, 0, after=1)) == OTHER
    # A start inside a turn is not passed backwards.
    for seq in [0, 3]:
        assert message(server, made_up(3, seq, turn=3, seq=4)) == OTHER
    assert seqs(made_up(3, 4, turn=3, seq=4))[0] == (3, 4)
    # A piece fetch stays in its piece: records 2 to 5 of the wide turn,
    # between the piece of records 0 and 1 and the one-record piece at 6.
    for asked in [2, 3, 5]:
        fetch = {"turn": 3, "seq": asked, "piece": True}
        for seq in [0, 1, 6, 7, 9]:
            assert message(server, made_up(3, seq, **fetch)) == OTHER, (asked, seq)
        for seq in [3, 4, 5]:
            assert seqs(made_up(3, seq, **fetch)) == [(3, n) for n in range(seq, 6)]
        assert message(server, made_up(2, 0, **fetch)) == OTHER


def test_a_made_up_continuation_reads_nothing_beyond_its_request(
    records: Server,
) -> None:
    conversation(records)
    forgeries(records, SESSION)


def test_the_headers_spool_spills_and_the_answer_is_the_same_bytes(
    records: Server,
) -> None:
    """Twenty turns under an actor of 60,000 bytes: 1.2 MB of headers. With
    `kinds: []` the first of the answer's two spools goes to a file; with
    every kind the headers and the records pass 1 MiB together."""
    actor = "a" * 60_000
    said = [{"role": "user", "content": "x" * 3_000, **TEXT}]
    for _ in range(20):
        records.post(
            "conversation/append_turn",
            conversation_id="s-headers",
            messages=said,
            actor=actor,
        )
    stamps = [
        turn["created_at"]
        for turn in records.post(
            "conversation/read_turns", conversation_id="s-headers", page_bytes=1 << 23
        )["turns"]
    ]
    headers = [
        {
            "turn": number,
            "created_at": stamp,
            "actor": "client:contract/" + actor,
            "redacted": False,
            "records": 1,
            "bytes": 3_000,
        }
        for number, stamp in enumerate(stamps, 1)
    ]
    assert len(headers) == 20
    body = {"conversation_id": "s-headers", "page_bytes": 1 << 23}
    only = compact({"turns": headers, "records": [], "continuation": None})
    assert len(only) > 1_200_000
    assert raw(records, {**body, "kinds": []}) == (200, only)
    items = [
        {"turn": number, **record(0, 0, "user", "prompt", "x" * 3_000)}
        | {"tail": None, "truncated": False}
        for number in range(1, 21)
    ]
    both = compact({"turns": headers, "records": items, "continuation": None})
    assert raw(records, {**body}) == (200, both)
    # At the default each page stays whole in memory, and they join the same.
    paged = pages(records, "s-headers", None)
    assert len(paged) == 2 and joined(paged) == (headers, items)


def test_a_malformed_or_foreign_continuation_is_refused_by_name(
    records: Server,
) -> None:
    conversation(records)
    turns = records.post(
        "conversation/read_turns", conversation_id=SESSION, page_bytes=1
    )
    foreign = turns["continuation"]
    assert isinstance(foreign, str)
    for token in ["abc", "W10=", foreign, "é" * 5]:
        refused = message(records, {"conversation_id": SESSION, "continuation": token})
        assert refused == f"malformed records-v1/read cursor: {token!r}"
    own = read(records, SESSION, page_bytes=1)["continuation"]
    assert isinstance(own, str)
    back = records.request(
        "/v1/conversation/read_turns",
        {"conversation_id": SESSION, "page_bytes": 1, "continuation": own},
    )
    assert (back.status, back.body["error"]["code"]) == (400, "value_error")


def test_a_later_page_adds_the_turns_sealed_since(records: Server) -> None:
    """Turns are immutable, so a continuation issued before a seal reads
    on into the turn sealed after it."""
    conversation(records)
    before = read(records, SESSION)
    first = read(records, SESSION, page_bytes=1)
    late = [record(0, 0, "user", "prompt", "late")]
    sealed = write(records, SESSION, "late", late, seal=True)
    assert sealed["turn"]["turn"] == 6
    rest = pages(records, SESSION, 5_000, start=first["continuation"])
    turns, items = joined([first, *rest])
    assert turns == [*before["turns"], sealed["turn"]]
    assert items == [*before["records"], *all_answered(6, late)]


def test_page_bytes_is_served_at_most_the_servers_page(enabled: Binary) -> None:
    """A larger `page_bytes`, and the default, are served as
    `NEOSIAN_MAX_PAGE_BYTES`."""
    with serve(enabled, extra_env={"NEOSIAN_MAX_PAGE_BYTES": "700"}) as server:
        conversation(server)
        whole = (
            read(server, SESSION, kinds=[])["turns"],
            [item for page in pages(server, SESSION, 1) for item in page["records"]],
        )
        assert len(whole[1]) == 29
        for asked in [None, 701, 983_040, 1 << 40, 2**64 - 1]:
            capped = pages(server, SESSION, asked, limit=700)
            assert joined(capped) == whole
            assert len(capped) > 5
        smaller = pages(server, SESSION, 400)
        assert joined(smaller) == whole and len(smaller) > len(capped)


def test_an_answer_is_within_its_published_bound(enabled: Binary) -> None:
    """Item 14 with ruling D2: an answer is at most
    `max(min(page_bytes, NEOSIAN_MAX_PAGE_BYTES), 81,920 + h) + 4,160`
    bytes, `h` being the page's first header, itself at most
    `192 + 6 * u(actor)`: no length bounds an actor, and a control
    character is answered as six bytes. Measured on raw answers with the
    longest record the bounds allow: a text of 65,536 encoded bytes, a call
    id and a name of 1,024 control characters each."""
    session = "x" * 128
    control = "\x01" * 1_024
    inner = "\x02" * 9_360 + "a" * 2
    call = '{"a":"' + inner.replace("\x02", "\\u0002") + '"}'
    widest = record(
        1, 1, "assistant", "tool_call", call, content="none", call=0, name=control
    )
    widest["call_id"] = control
    items = [
        record(0, 0, "user", "prompt", "\x01" * 10_922),
        widest,
        *[record(2 + n, 2 + n, "user", "prompt", "z" * 900) for n in range(30)],
    ]
    actor = "\x01" * 200 + "é" * 100
    stamped = "client:contract/" + actor
    with serve(enabled, extra_env={"NEOSIAN_MAX_PAGE_BYTES": "200000"}) as server:
        sealed = write(server, session, "d", items, seal=True, actor=actor)
        header = sealed["turn"]
        assert header["actor"] == stamped
        h = len(compact(header))
        assert h <= 192 + 6 * len(stamped.encode())
        assert h > 6 * 200, "the header's actor is answered escaped"
        body: dict[str, Any] = {"conversation_id": session}
        seen = 0
        lengths: dict[int, list[int]] = {}
        for asked in [1, 4_000, 90_000, 150_000, DEFAULT_PAGE, 1 << 40]:
            served = min(asked, 200_000)
            continuation: str | None = None
            while True:
                more = {} if continuation is None else {"continuation": continuation}
                status, answer = raw(server, {**body, "page_bytes": asked, **more})
                assert status == 200
                bound = max(served, RECORD_BYTES + h) + FRAMING
                assert len(answer) <= bound, (asked, len(answer))
                lengths.setdefault(asked, []).append(len(answer))
                page = read(server, session, page_bytes=asked, **more)
                assert compact(page) == answer
                seen += len(page["records"])
                continuation = page["continuation"]
                if continuation is None:
                    break
                assert len(continuation.encode()) <= 256
        assert seen == 6 * 32
        # The bound is not idle. A page of one group passes `page_bytes` by a
        # whole record and header: the widest record is 78,0xx bytes.
        text = len(compact(widest["text"]))
        assert text == 65_536 and len(compact(control)) == 6_146
        assert max(lengths[1]) > 65_536 + 2 * 6_146 + h
        assert max(lengths[1]) > RECORD_BYTES - 4_096
        # A page of many records stays within `page_bytes` and its framing.
        assert all(length <= 4_000 + FRAMING for length in lengths[4_000][2:])
        assert max(lengths[150_000]) > 150_000 - 1_000
        assert max(lengths[1 << 40]) <= 200_000 + FRAMING
