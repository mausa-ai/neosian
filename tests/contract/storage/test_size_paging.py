"""Optional size paging v1: byte-bounded pages of history search and turn reads.

A size-paged page holds the whole turns that fit under `page_bytes`, counting
each turn as the UTF-8 length of its compact JSON, and always at least one;
`continuation` carries the rest (DESIGN §40.46). These cases check that the
pages of every order reassemble to the answer the service gives without the
new fields, that pages are as full as the ceiling allows, that a continuation
binds its request, and that requests without the fields keep their answer.
Fixtures are ASCII, so every encoder counts alike. Absent support is an
explicit skip under both profiles.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.storage.support import Storage, message, refused

SEARCH = "conversation/search_turns"
READ = "conversation/read_turns"
SIZES = [700, 120, 950, 300, 40, 610, 880, 260, 75, 500, 330, 990]


@pytest.fixture(autouse=True)
def paging_capability(storage: Storage) -> None:
    capability = storage.capabilities.get("size_paging")
    if capability is None:
        pytest.skip("service does not declare optional size-paging version 1")
    assert capability == {"version": 1}


def plant(storage: Storage) -> list[dict[str, Any]]:
    turns = []
    for number, size in enumerate(SIZES, 1):
        text = f"paging probe {number:02d} " + "lorem " * (size // 6)
        reply = storage.turns("append_turn", messages=[message(text)])
        turns.append(reply["turn"])
    return turns


def encoded(turn: dict[str, Any]) -> int:
    return len(json.dumps(turn, separators=(",", ":"), ensure_ascii=False).encode())


def pages(
    storage: Storage, route: str, body: dict[str, Any], ceiling: int
) -> list[dict[str, Any]]:
    """Every page of one size-paged answer, each checked for its fill."""
    answers: list[dict[str, Any]] = []
    continuation = None
    while True:
        request = dict(body, page_bytes=ceiling)
        if continuation is not None:
            request["continuation"] = continuation
        answer = storage.server.post(route, **request)
        turns = answer["turns"]
        assert turns, "every page holds at least one turn"
        sizes = [encoded(turn) for turn in turns]
        assert len(turns) == 1 or sum(sizes) <= ceiling
        if answers:
            held = sum(encoded(turn) for turn in answers[-1]["turns"])
            assert held + sizes[0] > ceiling, "a page holds every turn that fits"
        answers.append(answer)
        continuation = answer["continuation"]
        if continuation is None:
            return answers
        assert isinstance(continuation, str)
        assert 1 <= len(continuation.encode()) <= 4096


def joined(answers: list[dict[str, Any]], key: str = "turns") -> list[Any]:
    return [item for answer in answers for item in answer.get(key, [])]


def test_version_1_pages_reassemble_the_whole_answer(storage: Storage) -> None:
    plant(storage)
    body = {"query": "paging probe", "conversations": [storage.conversation]}
    whole = storage.server.post(SEARCH, **body)
    assert set(whole) == {"turns"} and len(whole["turns"]) == len(SIZES)
    answers = pages(storage, SEARCH, body, 2000)
    assert len(answers) > 2
    assert all(set(answer) == {"turns", "continuation"} for answer in answers)
    assert joined(answers) == whole["turns"]
    limited = pages(storage, SEARCH, {**body, "limit": 5}, 2000)
    assert joined(limited) == whole["turns"][:5]


def test_ranked_orders_page_like_their_whole_answers(storage: Storage) -> None:
    if storage.capabilities.get("history_ranking") != {"version": 1}:
        pytest.skip("service does not declare history-ranking version 1")
    turns = plant(storage)
    since = turns[3]["created_at"]
    cases: list[tuple[str, dict[str, Any]]] = [
        ("probe lorem 07", {"order": "relevance"}),
        ("probe lorem 07", {"order": "relevance", "limit": 7}),
        ("paging probe", {"order": "newest", "since": since}),
        ("paging probe", {"since": since}),
    ]
    for query, extra in cases:
        body = {"query": query, "conversations": [storage.conversation], **extra}
        whole = storage.server.post(SEARCH, **body)
        answers = pages(storage, SEARCH, body, 1800)
        assert joined(answers) == whole["turns"], extra
        assert joined(answers, "scores") == whole.get("scores", [])
        for answer in answers:
            assert answer.get("order") == whole.get("order")
            assert ("scores" in answer) == ("scores" in whole)


def test_turn_reads_page_by_size(storage: Storage) -> None:
    plant(storage)
    whole = storage.server.post(READ, conversation_id=storage.conversation)
    answers = pages(storage, READ, {"conversation_id": storage.conversation}, 2500)
    assert all(answer["next_after"] is None for answer in answers)
    assert joined(answers) == whole["turns"]
    window = {"conversation_id": storage.conversation, "after": 2, "limit": 6}
    bounded = pages(storage, READ, window, 1500)
    assert [turn["turn"] for turn in joined(bounded)] == [3, 4, 5, 6, 7, 8]
    nothing = storage.server.post(READ, **{**window, "limit": 0}, page_bytes=100)
    assert (nothing["turns"], nothing["continuation"]) == ([], None)


def test_one_oversized_turn_makes_its_own_page(storage: Storage) -> None:
    plant(storage)
    body = {"query": "paging probe", "conversations": [storage.conversation]}
    answers = pages(storage, SEARCH, body, 1)
    assert [len(answer["turns"]) for answer in answers] == [1] * len(SIZES)
    reads = pages(storage, READ, {"conversation_id": storage.conversation}, 1)
    assert len(reads) == len(SIZES)


def test_a_continuation_binds_its_request(storage: Storage) -> None:
    plant(storage)
    body = {"query": "paging probe", "conversations": [storage.conversation]}
    first = storage.server.post(SEARCH, **body, page_bytes=1500)
    token = first["continuation"]
    assert token is not None
    changes: list[dict[str, Any]] = [
        {"query": "probe paging lorem"},
        {"conversations": [storage.conversation, storage.conversation + "-other"]},
        {"limit": 9},
    ]
    for changed in changes:
        reply = storage.server.request(
            "/v1/" + SEARCH,
            {**body, **changed, "page_bytes": 1500, "continuation": token},
        )
        refused(reply, "value_error")
    read = {"conversation_id": storage.conversation, "page_bytes": 1500}
    for foreign in [token, "not-a-continuation"]:
        refused(
            storage.server.request("/v1/" + READ, {**read, "continuation": foreign}),
            "value_error",
        )
    # The ceiling may change between pages.
    resumed = storage.server.post(SEARCH, **body, page_bytes=99999, continuation=token)
    whole = storage.server.post(SEARCH, **body)["turns"]
    assert first["turns"] + resumed["turns"] == whole


@pytest.mark.parametrize(
    "bad",
    [
        {"continuation": "x"},
        {"page_bytes": 0},
        {"page_bytes": -5},
        {"page_bytes": "1000"},
        {"page_bytes": True},
        {"page_bytes": 1.5},
        {"page_bytes": 1000, "continuation": ""},
        {"page_bytes": 1000, "continuation": 7},
    ],
)
def test_invalid_paging_requests_refuse(storage: Storage, bad: dict[str, Any]) -> None:
    requests: list[tuple[str, dict[str, Any]]] = [
        (SEARCH, {"query": "probe", "conversations": [storage.conversation]}),
        (READ, {"conversation_id": storage.conversation}),
    ]
    for route, body in requests:
        refused(storage.server.request("/v1/" + route, {**body, **bad}), "value_error")


def test_requests_without_the_fields_are_unchanged(storage: Storage) -> None:
    plant(storage)
    search = storage.server.post(
        SEARCH, query="paging probe", conversations=[storage.conversation]
    )
    assert set(search) == {"turns"}
    nulls = storage.server.post(
        SEARCH,
        query="paging probe",
        conversations=[storage.conversation],
        page_bytes=None,
        continuation=None,
    )
    assert nulls == search
    read = storage.server.post(READ, conversation_id=storage.conversation, limit=3)
    assert set(read) == {"turns", "next_after"} and read["next_after"] is None


def test_a_relevance_continuation_refuses_once_its_candidates_changed(
    storage: Storage,
) -> None:
    if storage.capabilities.get("history_ranking") != {"version": 1}:
        pytest.skip("service does not declare history-ranking version 1")
    storage.require("erasable")
    plant(storage)
    body = {
        "query": "paging probe",
        "conversations": [storage.conversation],
        "order": "relevance",
    }
    first = storage.server.post(SEARCH, **body, page_bytes=1500)
    assert first["continuation"] is not None
    storage.turns("redact_turns", actor="erase", turns=[12])
    reply = storage.server.request(
        "/v1/" + SEARCH,
        {**body, "page_bytes": 1500, "continuation": first["continuation"]},
    )
    refused(reply, "value_error")
