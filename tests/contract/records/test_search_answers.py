"""`conversation/search_records`: what never matches, the design's example
and the answer's published bound (DESIGN §40.54 items 11 and 14, rulings
R16 and R17). Expected scores are literal values from an independent
Python reference of the pinned formula."""

from __future__ import annotations

from typing import Any

from tests.contract.records.example import (
    CUT_WORD,
    DRAFT,
    FAILED,
    FAILED_CHUNKS,
    FAILED_EXAMPLE,
    SESSION,
)
from tests.contract.records.reading import all_answered, read
from tests.contract.records.searching import (
    SEARCH,
    SWEEP,
    answer_bytes,
    hit,
    places,
    ranked,
    raw,
    refusal,
    search,
)
from tests.contract.records.support import compact, record, write
from tests.contract.support import ADMIN, Binary, Server, serve


def one(seq_text: str) -> list[dict[str, Any]]:
    return [record(0, 0, "user", "prompt", seq_text)]


def hidden(records: Server) -> None:
    """An open draft is invisible to both orders and to the statistics of
    relevance until it is sealed; a redacted turn leaves both again."""
    session = "s-hide"
    ask = {"conversations": [session]}
    assert write(records, session, "one", one("needle one"))["state"] == "open"
    assert search(records, "needle", **ask) == {"hits": []}
    assert search(records, "needle", order="relevance") == {
        "hits": [],
        "order": "relevance",
    }
    write(records, session, "one", [], seal=True)
    assert ranked(search(records, "needle", order="relevance", **ask)) == [
        (1, 0, 287682)
    ]
    write(records, session, "two", one("needle two"), seal=True)
    two = [(2, 0, 182322), (1, 0, 182322)]
    assert ranked(search(records, "needle", order="relevance", **ask)) == two
    # A second draft, open: neither a hit nor a document of the statistics.
    assert write(records, session, "three", one("needle three"))["state"] == "open"
    assert ranked(search(records, "needle", order="relevance", **ask)) == two
    assert places(search(records, "needle", **ask)) == [
        (session, 2, 0),
        (session, 1, 0),
    ]
    assert search(records, "three", **ask) == {"hits": []}
    write(records, session, "three", [], seal=True)
    assert ranked(search(records, "needle", order="relevance", **ask)) == [
        (2, 0, 137035),
        (1, 0, 137035),
        (3, 0, 127035),
    ]
    assert places(search(records, "three", **ask)) == [(session, 3, 0)]
    erased = records.post(
        "conversation/redact_turns", conversation_id=session, turns=[2]
    )
    assert erased == {"count": 1}
    assert ranked(search(records, "needle", order="relevance", **ask)) == [
        (1, 0, 189364),
        (3, 0, 175784),
    ]
    assert places(search(records, "needle", **ask)) == [
        (session, 3, 0),
        (session, 1, 0),
    ]
    assert search(records, "two", **ask) == {"hits": []}
    # A discarded draft was never there.
    write(records, session, "four", one("needle four"))
    write(records, session, "four", [], discard=True)
    assert search(records, "four", **ask) == {"hits": []}
    # A turn stored whole and then redacted leaves the same way.
    records.post(
        "conversation/append_turn",
        conversation_id=session,
        messages=[{"role": "user", "content": "needle five"}],
    )
    assert places(search(records, "five", **ask)) == [(session, 4, 0)]
    records.post("conversation/redact_turns", conversation_id=session, turns=[4])
    assert search(records, "five", **ask) == {"hits": []}


def test_a_draft_and_a_redacted_turn_never_match(records: Server) -> None:
    hidden(records)


def example(server: Server, *, files: bool = True) -> None:
    """Item 11's example on the turn of item 9, one failing assertion laid
    in its result. Literal as the design shows it: the request, the hit's
    place (turn 42, record 9, message 2), its call id and its call's name,
    the actor, the piece's size and the excerpt's offset. The design
    shortens the excerpt's text with `...`; here it is the 240 scalars from
    31,507. The design's score, 5,342,871, is not one ranking version 1 can
    give this answer: a lone candidate holding two terms scores at most
    1,265,801 whatever its counts (under `2 * ln(4/3) * 2.2`), and holding
    each once it scores `2 * ln(4/3)`, 575,364 (ruling R17). The server's default client is
    `client:default`."""
    for number in range(1, 42):
        server.post(
            "conversation/append_turn",
            conversation_id=SESSION,
            messages=[{"role": "user", "content": f"turn {number}"}],
        )
    write(server, SESSION, DRAFT, FAILED_EXAMPLE[:9], actor="claude-code:7f3a")
    sealed = write(
        server,
        SESSION,
        DRAFT,
        FAILED_EXAMPLE[9:],
        actor="claude-code:7f3a",
        seal=True,
    )
    created_at = sealed["turn"]["created_at"]
    chunk = FAILED_CHUNKS[7]
    assert chunk.find("failed assertion") == 31_547
    assert FAILED.count("failed") == 1 and FAILED.count("assertion") == 1
    text = chunk[31_507:31_747]
    assert text[10:].startswith("thread 'cache::ttl' panicked: failed assertion:")
    expected = {
        "hits": [
            {
                "conversation_id": "cc-7f3a",
                "turn": 42,
                "seq": 9,
                "message": 2,
                "kind": "tool_result",
                "call_id": "toolu_01",
                "name": "Bash",
                "created_at": created_at,
                "actor": "client:default/claude-code:7f3a",
                "size": 812_345,
                "excerpt": {"offset": 31_507, "text": text},
                "score": 575_364,
            }
        ],
        "order": "relevance",
    }
    body: dict[str, Any] = {
        "query": "failed assertion",
        "conversations": ["cc-7f3a"],
        "kinds": ["tool_result"],
        "order": "relevance",
        "limit": 5,
    }
    assert search(server, **body) == expected
    assert raw(server, SEARCH, body) == (200, compact(expected))
    # A hit names the record: read it, its piece, or around it.
    found = read(server, SESSION, turn=42, seq=9, piece=True, page_bytes=1)
    assert found["records"] == all_answered(42, FAILED_EXAMPLE[2:3])
    exact = read(server, SESSION, turn=42, seq=9, kinds=["tool_result"])
    assert exact["records"][0]["text"][31_507:31_747] == text
    # An occurrence spanning a cut between two records is not found
    # here; turn-level matching stays on `search_turns`.
    assert FAILED_CHUNKS[0].endswith(CUT_WORD[:3])
    assert FAILED_CHUNKS[1].startswith(CUT_WORD[3:])
    assert search(server, CUT_WORD) == {"hits": []}
    assert search(server, CUT_WORD, order="relevance")["hits"] == []
    # A FileStore's history search matches the turn's whole text; Postgres
    # matches a term within one record there too (`neosian docs stores`).
    turns = server.post("conversation/search_turns", query=CUT_WORD)
    assert [turn["turn"] for turn in turns["turns"]] == ([42] if files else [])
    # Each side of the cut is found in its own record.
    assert places(search(server, CUT_WORD[:3])) == [(SESSION, 42, 2)]
    assert places(search(server, CUT_WORD[3:])) == [(SESSION, 42, 3)]


def test_the_design_example_finds_the_failing_assertion(enabled: Binary) -> None:
    token = f"client:default={ADMIN}"
    with serve(enabled, extra_env={"NEOSIAN_SERVE_TOKEN": token}) as server:
        example(server)


def test_an_answer_is_within_its_published_bound(records: Server) -> None:
    """Item 14 with ruling R16: an answer is at most
    `limit * (16,384 + 6 * u(actor)) + 64` bytes. Measured on raw answers
    whose hits are as wide as the bounds allow: a conversation id of 128
    bytes, a call id and a name of 1,024 control characters each, an
    excerpt of 239 control characters and an actor of control characters,
    each answered as six bytes."""
    session = "x" * 128
    control = "\x01" * 1_024
    actor = "\x01" * 200 + "é" * 100
    stamped = "client:contract/" + actor
    text = "\x02" * 40 + "x" + "\x02" * 400
    items: list[dict[str, Any]] = []
    for index in range(4):
        call = record(
            index, 0, "assistant", "tool_call", "{}", content=None, call=index
        )
        call["call_id"] = chr(3 + index) * 1_024
        call["name"] = control
        items.append(call)
    items[0]["content"] = "none"
    for index in range(4):
        result = record(4 + index, 1 + index, "tool", "tool_result", text)
        result["call_id"] = chr(3 + index) * 1_024
        items.append(result)
    sealed = write(records, session, "d", items, seal=True, actor=actor)
    assert sealed["turn"]["actor"] == stamped
    widest = 0
    for order in [None, "newest", "relevance"]:
        for limit in [1, 3, 4, 100]:
            body: dict[str, Any] = {"query": "x", "kinds": ["tool_result"]}
            body |= {"limit": limit} | ({} if order is None else {"order": order})
            status, answer = raw(records, SEARCH, body)
            assert status == 200
            hits = search(records, **body)["hits"]
            assert len(hits) == min(limit, 4)
            assert answer == answer_bytes(hits, order)
            bound = limit * (16_384 + 6 * len(stamped.encode())) + 64
            assert len(answer) <= bound, (order, limit, len(answer))
            for found in hits:
                assert found["name"] == control
                assert found["excerpt"] == {"offset": 0, "text": text[:240]}
                size = len(compact(found))
                widest = max(widest, size)
                assert size <= 16_384 + 6 * len(stamped.encode())
            # The bound is not idle: a hit is most of its 16 KiB.
            assert len(answer) > len(hits) * 13_800
    # 128 + 2 * 6,146 + 1,437 + the actor escaped + keys and numbers.
    assert len(compact(control)) == 6_146
    assert len(compact(text[:240])) == 239 * 6 + 1 + 2
    assert 15_400 < widest < 15_600
    assert len(compact(stamped)) == 16 + 200 * 6 + 200 + 2


def test_an_empty_call_id_and_name_are_answered_as_they_were_written(
    records: Server,
) -> None:
    """Ruling R20: a record may carry an empty `call_id` and an empty
    `name`, and a hit answers them as empty strings, not null. The answer
    is checked against the published schema by `search`."""
    items = [
        record(
            0,
            0,
            "assistant",
            "tool_call",
            '{"q":"needle"}',
            content="none",
            call=0,
            call_id="",
            name="",
        ),
        record(1, 1, "tool", "tool_result", "needle out", call_id=""),
    ]
    sealed = write(records, "s-empty", "d", items, seal=True)
    found = search(records, "needle")
    assert found["hits"] == [
        hit("s-empty", sealed["turn"], items[0], ["needle"]),
        hit("s-empty", sealed["turn"], items[1], ["needle"], name=""),
    ]
    assert [(one["call_id"], one["name"]) for one in found["hits"]] == [("", "")] * 2
    assert raw(records, SEARCH, {"query": "needle"})[1].count(b'"call_id":""') == 2


def test_a_refusal_does_not_grow_with_the_request(records: Server) -> None:
    """Ruling R18: the published bounds are those of 200 answers, and a
    refusal repeats at most the first 128 scalars of an unknown field's
    name or an unknown kind."""
    long = "k" * 200_000
    cut = "k" * 128
    for route, body in [
        (SEARCH, {"query": "x", long: 1}),
        (SWEEP, {long: 1}),
    ]:
        status, answer = raw(records, route, body)
        assert status == 400 and len(answer) == 207
        error = refusal(records, route, body)
        assert error["message"] == f"unknown parameter: '{cut}'"
    wide = "é" * 127 + "\U0001f600" + "x" * 100_000
    status, answer = raw(records, SEARCH, {"query": "x", "kinds": [wide]})
    assert status == 400 and len(answer) < 512
    error = refusal(records, SEARCH, {"query": "x", "kinds": [wide]})
    assert error["message"] == 'unknown record kind "' + wide[:128] + '"'


def test_a_turn_that_cannot_be_records_refuses_a_search_that_reaches_it(
    records: Server,
) -> None:
    """A turn stored whole whose call id is longer than 1,024 bytes cannot
    be answered as records. A search whose term the turn holds is refused
    by name in both orders; one whose term it lacks passes it over."""
    session = "s-long"
    call = {"id": "i" * 1_025, "name": "Bash", "arguments": {"q": "needle"}}
    records.post(
        "conversation/append_turn",
        conversation_id=session,
        messages=[{"role": "assistant", "content": None, "tool_calls": [call]}],
    )
    reason = (
        "turn 1: message 0 has a call id longer than 1024 bytes,"
        " which records cannot carry"
    )
    narrower: list[dict[str, Any]] = [
        {},
        {"order": "relevance"},
        {"conversations": [session]},
    ]
    for more in narrower:
        error = refusal(
            records,
            SEARCH,
            {"query": "needle", **more},
            400,
            "agent_conversation_format_unsupported",
        )
        assert error == {
            "code": "agent_conversation_format_unsupported",
            "message": f"Unsupported turn format in conversation '{session}': {reason}",
            "details": {"conversation_id": session, "reason": reason},
        }
    assert search(records, "absentword") == {"hits": []}
    turns = records.post("conversation/search_turns", query="needle")
    assert [turn["turn"] for turn in turns["turns"]] == [1]
