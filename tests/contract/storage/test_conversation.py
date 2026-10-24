"""Turns, projections and search remain data operations on every storage service."""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.contract.storage.support import Storage, message, refused
from tests.contract.support import fixture


@pytest.mark.parametrize(
    "content", ["plain text", "", "café ✓\r\nline", "---\nkey: value\n---\nbody"]
)
def test_turns_are_verbatim_monotonic_and_isolated(
    storage: Storage, content: str
) -> None:
    assert storage.turns("read_turns") == {"turns": [], "next_after": None}
    assert storage.turns("last_turn_number") == {"turn": 0}
    first = storage.turns("append_turn", messages=[message(content)], actor="writer")[
        "turn"
    ]
    assert first == {
        "conversation_id": storage.conversation,
        "turn": 1,
        "messages": [message(content)],
        "created_at": first["created_at"],
        "actor": storage.client + "/writer",
        "redacted": False,
    }
    second = storage.turns("append_turn", messages=[message("next")])["turn"]
    assert second["turn"] == 2 and second["actor"] == storage.client
    assert datetime.fromisoformat(second["created_at"]) >= datetime.fromisoformat(
        first["created_at"]
    )
    assert storage.turns("read_turns")["turns"] == [first, second]
    assert storage.turns("read_turns", after=1, limit=1) == {
        "turns": [second],
        "next_after": None,
    }
    assert storage.turns("read_turns", after=2)["turns"] == []
    assert storage.turns("read_turns", limit=0)["turns"] == []
    assert storage.turns("last_turn_number") == {"turn": 2}
    assert (
        storage.server.post(
            "conversation/read_turns", conversation_id=storage.conversation + "-other"
        )["turns"]
        == []
    )


def test_historical_media_reasoning_and_tool_calls_need_no_provider(
    storage: Storage,
) -> None:
    messages = fixture("records.json")["turns"][0]["messages"]
    appended = storage.turns("append_turn", messages=messages)["turn"]
    assert appended["messages"] == messages
    assert storage.turns("read_turns")["turns"][0]["messages"] == messages


def test_projection_order_spans_and_unwritten_turns(storage: Storage) -> None:
    assert storage.turns("read_projections")["entries"] == []
    storage.turns("append_projections", entries=[])
    entries = [
        {"turn": 3, "kind": "epoch", "span": 3, "text": "wide"},
        {"turn": 3, "kind": "log", "span": 1, "text": "first"},
        {"turn": 1, "kind": "digest", "span": 1, "text": "early"},
    ]
    storage.turns("append_projections", entries=entries)
    last = {"turn": 3, "kind": "log", "span": 1, "text": "second"}
    storage.turns("append_projections", entries=[last])
    expected = [entries[2], entries[1], last, entries[0]]
    assert storage.turns("read_projections")["entries"] == expected
    assert (
        storage.turns("read_projections", after=1, limit=2)["entries"] == expected[1:3]
    )
    assert storage.turns("read_projections", limit=0)["entries"] == []
    assert storage.turns("last_turn_number") == {"turn": 0}
    assert (
        storage.server.post(
            "conversation/read_projections",
            conversation_id=storage.conversation + "-other",
        )["entries"]
        == []
    )


@pytest.mark.parametrize(
    "route,key,value",
    [
        ("read_turns", "after", -1),
        ("read_turns", "limit", -1),
        ("read_projections", "after", -1),
        ("read_projections", "limit", -1),
        ("append_turn", "messages", []),
    ],
)
def test_invalid_windows_and_empty_turns_are_refused(
    storage: Storage, route: str, key: str, value: object
) -> None:
    refused(
        storage.server.request(
            "/v1/conversation/" + route,
            {"conversation_id": storage.conversation, key: value},
        ),
        "value_error",
    )
    assert storage.turns("last_turn_number") == {"turn": 0}


def test_search_terms_are_literal_case_insensitive_and_all_required(
    storage: Storage,
) -> None:
    for text in ["Kestrel", "Kestrel yarrow", "fjords 100% done_now"]:
        storage.turns("append_turn", messages=[message(text)])
    for query in ["KESTREL yarrow", "yarrow kestrel", "KeStReL\tYaRrOw"]:
        assert [r["turn"] for r in storage.search(query)] == [2]
    assert [r["turn"] for r in storage.search("kestrel")] == [2, 1]
    for query in ["jord", "100%", "done_now"]:
        assert [r["turn"] for r in storage.search(query)] == [3]
    for query in ["100_", "user", "assistant", "tool", "kestrel absent"]:
        assert storage.search(query) == []
    assert [r["turn"] for r in storage.search("kestrel", limit=1)] == [2]
    assert (
        storage.server.post(
            "conversation/search_turns", query="kestrel", conversations=[]
        )["turns"]
        == []
    )


def test_search_includes_tool_names_arguments_and_results(storage: Storage) -> None:
    tool = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call-1", "name": "lookup", "arguments": {"topic": "albatross"}}
        ],
    }
    result = {"role": "tool", "content": "mackerel", "tool_call_id": "call-1"}
    storage.turns("append_turn", messages=[tool, result])
    for query in ["lookup", "albatross", "mackerel", "lookup mackerel"]:
        assert [r["turn"] for r in storage.search(query)] == [1]


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": " \t"},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": -1},
        {"query": "x", "limit": 501},
    ],
)
def test_search_refuses_invalid_bounds(
    storage: Storage, body: dict[str, object]
) -> None:
    refused(
        storage.server.request(
            "/v1/conversation/search_turns",
            {"conversations": [storage.conversation], **body},
        ),
        "value_error",
    )
