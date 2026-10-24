"""Erase content and covering projections while preserving numbers and provenance."""

from __future__ import annotations

import pytest

from tests.contract.storage.support import Storage, message, refused


@pytest.mark.parametrize(
    "selector,expected",
    [({}, [1, 2, 3, 4]), ({"through": 2}, [1, 2]), ({"turns": [3, 1, 9, 3]}, [1, 3])],
)
def test_turn_erasure_is_exact_and_keeps_skeletons(
    storage: Storage, selector: dict[str, object], expected: list[int]
) -> None:
    storage.require("erasable")
    before = [
        storage.turns(
            "append_turn", messages=[message(f"evidence-{n}")], actor=f"writer{n}"
        )["turn"]
        for n in range(1, 5)
    ]
    storage.turns(
        "append_projections",
        entries=[
            {"turn": n, "kind": "log", "text": f"evidence-{n}", "span": 1}
            for n in range(1, 5)
        ],
    )
    assert storage.turns("redact_turns", actor="erase", **selector) == {
        "count": len(expected)
    }
    after = storage.turns("read_turns")["turns"]
    assert after == [
        {**turn, "messages": [], "redacted": True} if turn["turn"] in expected else turn
        for turn in before
    ]
    assert storage.turns("last_turn_number") == {"turn": 4}
    assert [r["turn"] for r in storage.search("evidence")] == [
        n for n in [4, 3, 2, 1] if n not in expected
    ]
    assert storage.turns("redact_turns", actor="erase", **selector) == {
        "count": len(expected)
    }
    assert storage.turns("read_turns")["turns"] == after
    trail = storage.server.post(
        "conversation/turn_redactions", conversations=[storage.conversation]
    )["redactions"]
    assert len(trail) == 2
    assert all(
        r["turns"] == expected and r["actor"] == storage.client + "/erase"
        for r in trail
    )
    assert (
        storage.server.post(
            "conversation/turn_redactions",
            conversations=[storage.conversation],
            limit=1,
        )["redactions"]
        == trail[:1]
    )
    assert (
        storage.server.post(
            "conversation/turn_redactions",
            conversations=[storage.conversation],
            since=trail[0]["created_at"],
        )["redactions"][0]
        == trail[0]
    )
    assert (
        storage.server.post("conversation/turn_redactions", conversations=[])[
            "redactions"
        ]
        == []
    )


def test_covering_spans_clear_only_their_text(storage: Storage) -> None:
    storage.require("erasable")
    for _ in range(4):
        storage.turns("append_turn", messages=[message("secret")])
    entries = [
        {"turn": 1, "kind": "log", "text": "one", "span": 1},
        {"turn": 2, "kind": "log", "text": "two", "span": 1},
        {"turn": 3, "kind": "epoch", "text": "one to three", "span": 3},
        {"turn": 4, "kind": "digest", "text": "four", "span": 1},
    ]
    storage.turns("append_projections", entries=entries)
    storage.turns("redact_turns", turns=[2])
    assert storage.turns("read_projections")["entries"] == [
        entries[0],
        {**entries[1], "text": ""},
        {**entries[2], "text": ""},
        entries[3],
    ]


@pytest.mark.parametrize(
    "selector",
    [
        {"through": 1, "turns": [1]},
        {"through": 0},
        {"through": -1},
        {"turns": [0]},
        {"turns": [-1, 2]},
    ],
)
def test_invalid_selection_never_erases(
    storage: Storage, selector: dict[str, object]
) -> None:
    storage.require("erasable")
    before = storage.turns("append_turn", messages=[message("retained")])["turn"]
    refused(
        storage.server.request(
            "/v1/conversation/redact_turns",
            {"conversation_id": storage.conversation, **selector},
        ),
        "value_error",
    )
    assert storage.turns("read_turns")["turns"] == [before]
    assert (
        storage.server.post(
            "conversation/turn_redactions", conversations=[storage.conversation]
        )["redactions"]
        == []
    )


def test_absent_turns_and_empty_selection_are_noops(storage: Storage) -> None:
    storage.require("erasable")
    assert storage.turns("redact_turns") == {"count": 0}
    storage.turns("append_turn", messages=[message("kept")])
    assert storage.turns("redact_turns", turns=[]) == {"count": 0}
    assert storage.turns("redact_turns", turns=[99]) == {"count": 0}
    assert (
        storage.server.post(
            "conversation/turn_redactions", conversations=[storage.conversation]
        )["redactions"]
        == []
    )
