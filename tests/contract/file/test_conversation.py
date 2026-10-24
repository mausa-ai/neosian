"""Malformed physical rows cannot be hidden by a read window or repaired by erasure."""

import json
from typing import Any

import pytest

from tests.contract.file.support import CONVERSATION, SESSION, append_row, snapshot
from tests.contract.storage.support import message, refused
from tests.contract.support import Binary, fixture, serve


@pytest.mark.parametrize(
    "change",
    [
        "{broken",
        "",
        "[]",
        "{}",
        {"neosian_format": 2},
        {"neosian_format": True},
        {"turn": 0},
        {"turn": True},
        {"turn": 1.5},
        {"created_at": "2026-08-19T10:00:00"},
        {"created_at": "later"},
        {"actor": 7},
        {"messages": []},
        {"messages": [{"role": "invented", "content": "x"}]},
        {"redacted": True},
        {"redacted": None},
    ],
)
def test_bad_turn_blocks_reads_numbering_appends_and_erasure(
    planted: Binary, change: dict[str, Any] | str
) -> None:
    number = append_row(planted.home / CONVERSATION / "turns.jsonl", change)
    original = snapshot(planted.home)
    with serve(planted) as server:
        calls: list[tuple[str, dict[str, Any]]] = [
            ("read_turns", {}),
            ("read_turns", {"after": 99, "limit": 1}),
            ("last_turn_number", {}),
            ("append_turn", {"messages": [message("must not land")]}),
            ("redact_turns", {}),
        ]
        for route, args in calls:
            details = refused(
                server.request(
                    "/v1/conversation/" + route, {"conversation_id": SESSION, **args}
                ),
                "agent_conversation_format_unsupported",
            )
            assert f"turn-log line {number}" in details["reason"]
            assert snapshot(planted.home) == original


@pytest.mark.parametrize(
    "change",
    [
        "{broken",
        "",
        "[]",
        "{}",
        {"neosian_format": 2},
        {"neosian_format": True},
        {"turn": 0},
        {"span": 0},
        {"span": 99},
        {"span": True},
        {"kind": "invented"},
        {"text": None},
    ],
)
def test_bad_projection_is_refused_even_outside_the_read_window(
    planted: Binary, change: dict[str, Any] | str
) -> None:
    number = append_row(planted.home / CONVERSATION / "projections.jsonl", change)
    original = snapshot(planted.home)
    with serve(planted) as server:
        for after in [0, 99]:
            details = refused(
                server.request(
                    "/v1/conversation/read_projections",
                    {"conversation_id": SESSION, "after": after, "limit": 1},
                ),
                "agent_conversation_format_unsupported",
            )
            assert f"projection line {number}" in details["reason"]
            assert snapshot(planted.home) == original


@pytest.mark.parametrize(
    "change",
    [
        "{broken",
        "[]",
        {"neosian_format": 2},
        {"created_at": "later"},
        {"turns": []},
        {"turns": [0]},
        {"actor": 7},
    ],
)
def test_bad_turn_erasure_trail_is_not_skipped(
    planted: Binary, change: dict[str, Any] | str
) -> None:
    number = append_row(planted.home / CONVERSATION / "redactions.jsonl", change)
    original = snapshot(planted.home)
    with serve(planted) as server:
        details = refused(
            server.request(
                "/v1/conversation/turn_redactions",
                {"conversations": [SESSION], "limit": 1},
            ),
            "agent_conversation_format_unsupported",
        )
        assert f"redaction trail line {number}" in details["reason"]
        assert snapshot(planted.home) == original


def test_unknown_row_keys_are_ignored_and_last_row_controls_numbering(
    planted: Binary,
) -> None:
    append_row(
        planted.home / CONVERSATION / "turns.jsonl",
        {"turn": 7, "future_key": {"x": True}},
    )
    with serve(planted) as server:
        assert server.post(
            "conversation/last_turn_number", conversation_id=SESSION
        ) == {"turn": 7}
        turns = server.post("conversation/read_turns", conversation_id=SESSION)["turns"]
        assert turns == [
            *fixture("records.json")["turns"],
            {**fixture("records.json")["turns"][0], "turn": 7},
        ]
        appended = server.post(
            "conversation/append_turn",
            conversation_id=SESSION,
            messages=[message("next")],
        )["turn"]
        assert appended["turn"] == 8


def test_search_decodes_only_raw_candidates_but_reads_validate_every_row(
    planted: Binary,
) -> None:
    file = planted.home / CONVERSATION / "turns.jsonl"
    good = file.read_bytes()
    with serve(planted) as server:
        before = server.post(
            "conversation/search_turns", query="review", conversations=[SESSION]
        )
        assert before["turns"]
        file.write_bytes(good + b"{broken\n")
        assert (
            server.post(
                "conversation/search_turns", query="review", conversations=[SESSION]
            )
            == before
        )
        refused(
            server.request("/v1/conversation/read_turns", {"conversation_id": SESSION}),
            "agent_conversation_format_unsupported",
        )
        file.write_bytes(good + b"{broken review\n")
        refused(
            server.request(
                "/v1/conversation/search_turns",
                {"query": "review", "conversations": [SESSION]},
            ),
            "agent_conversation_format_unsupported",
        )


def test_escaped_search_terms_are_not_lost_to_the_raw_prefilter(
    planted: Binary,
) -> None:
    with serve(planted) as server:
        appended = server.post(
            "conversation/append_turn",
            conversation_id=SESSION,
            messages=[message('say "hi" to café \\x')],
        )["turn"]
        raw = (
            (planted.home / CONVERSATION / "turns.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )
        assert raw.isascii() and "caf\\u00e9" in raw
        assert json.loads(raw)["messages"][0]["content"] == 'say "hi" to café \\x'
        for term in ['"hi"', "café", "\\x", "CAFÉ"]:
            hits = server.post(
                "conversation/search_turns", query=term, conversations=[SESSION]
            )["turns"]
            assert any(hit["turn"] == appended["turn"] for hit in hits)
