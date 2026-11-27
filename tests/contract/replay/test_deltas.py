"""The delta list stays small, reviewed and exact (owner ruling on #448).

Each entry names a frozen case and a path that `APPROVED` names, a literal
that differs from the frozen value there, and decision #448. A tool entry
keeps its tool's name and changes only its description and schema. The
comparison changes exactly the listed values: an expectation with every
delta applied differs from the frozen one at those paths and nowhere else,
so the replay test still fails on any difference the list does not name,
and on any change to a listed value, each entry exercised.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.contract.replay.corpus import CASES
from tests.contract.replay.deltas import DECISIONS, DELTAS, expected_for, located
from tests.contract.replay.test_reference import compare

HERE = Path(__file__).parent
EXPECTED: dict[str, Any] = json.loads((HERE / "expected.json").read_text())

# Every #448 entry approved: its case, its path, and the tool it describes
# (None for an answer's text). No other case or path may carry an entry.
_TEXT = ("result", "content", 0, "text")
_TOOLS = ("result", "tools")
APPROVED: dict[tuple[str, tuple[Any, ...]], str | None] = {
    ("mcp recalls", ("output", 1, 1, *_TEXT)): None,
    ("serve mcp session", ("output", 10, 1, 0, *_TEXT)): None,
    **{
        (case, (*at, *_TOOLS, index)): tool
        for case, at in (
            ("mcp reads", ("output", 1, 1)),
            ("mcp before initialize", ("output", 1, 2)),
            ("mcp policies", ("output", 1, 1)),
            ("serve mcp session", ("output", 2, 1, 0)),
            ("serve mcp without a session", ("output", 7, 1, 0)),
        )
        for index, tool in (
            (4, "recall_turn"),
            (5, "search_history"),
            (6, "continue_session"),
        )
        if (case, index) != ("mcp policies", 6)
    },
}


def differences(
    left: Any, right: Any, path: tuple[Any, ...] = ()
) -> set[tuple[Any, ...]]:
    """The paths where two JSON values differ, each at its deepest common node."""
    if (
        isinstance(left, dict)
        and isinstance(right, dict)
        and left.keys() == right.keys()
    ):
        found: set[tuple[Any, ...]] = set()
        for key in left:
            found |= differences(left[key], right[key], (*path, key))
        return found
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        found = set()
        for index, (one, other) in enumerate(zip(left, right, strict=True)):
            found |= differences(one, other, (*path, index))
        return found
    return set() if left == right else {path}


def test_each_delta_is_a_reviewed_literal_of_a_frozen_case() -> None:
    names = {case.name for case in CASES}
    seen = set()
    for delta in DELTAS:
        assert sorted(delta) == ["at", "case", "decision", "literal"]
        assert delta["decision"] in DECISIONS
        assert delta["case"] in names and delta["case"] in EXPECTED
        key = (delta["case"], tuple(delta["at"]))
        assert key in APPROVED, key
        assert key not in seen
        seen.add(key)
        container, last = located(EXPECTED[delta["case"]], delta["at"])
        frozen, literal = container[last], delta["literal"]
        assert frozen != literal
        assert type(frozen) is type(literal)
        tool = APPROVED[key]
        if tool is None:
            assert isinstance(literal, str)
        else:
            assert frozen["name"] == literal["name"] == tool, key
            changed = {
                k
                for k in frozen.keys() | literal.keys()
                if frozen.get(k) != literal.get(k)
            }
            assert changed <= {"description", "inputSchema"}, (key, changed)
    assert seen == set(APPROVED)


def test_only_the_listed_values_change() -> None:
    for name in sorted({delta["case"] for delta in DELTAS}):
        frozen = EXPECTED[name]
        listed = {tuple(d["at"]) for d in DELTAS if d["case"] == name}
        changed = differences(frozen, expected_for(name, frozen))
        for path in changed:
            assert any(path[: len(at)] == at for at in listed), path
        for at in listed:
            assert any(path[: len(at)] == at for path in changed), at
    untouched = next(case.name for case in CASES if case.name in EXPECTED)
    assert untouched not in {delta["case"] for delta in DELTAS}
    assert expected_for(untouched, EXPECTED[untouched]) == EXPECTED[untouched]


def changes(value: Any) -> list[Any]:
    """Each way one listed value is changed: its text, or a tool's
    description and each of its schema's fields."""
    if isinstance(value, str):
        return [value + "\n[an answer no decision names]"]
    described = json.loads(json.dumps(value))
    described["description"] += " An unlisted sentence."
    made = [described]
    for field in value["inputSchema"]["properties"]:
        changed = json.loads(json.dumps(value))
        changed["inputSchema"]["properties"][field]["unlisted"] = True
        made.append(changed)
    return made


def test_an_unlisted_difference_still_fails_the_comparison() -> None:
    """The replay test's own comparison, given answers made up here: the
    frozen answer with exactly the listed deltas passes; the released answer
    without them fails; a change to any listed value, every entry and every
    field of a listed schema each in turn, fails, though the comparison
    maps `search_history`'s description and ranking fields back; one more
    difference anywhere fails."""
    for name in sorted({delta["case"] for delta in DELTAS}):
        expected = expected_for(name, EXPECTED[name])
        compare(name, expected)
        with pytest.raises(AssertionError):
            compare(name, EXPECTED[name])
        for listed in (d for d in DELTAS if d["case"] == name):
            container, last = located(expected, listed["at"])
            for changed in changes(container[last]):
                observed = json.loads(json.dumps(expected))
                inside, key = located(observed, listed["at"])
                inside[key] = changed
                with pytest.raises(AssertionError):
                    compare(name, observed)
        beside = json.loads(json.dumps(expected))
        beside["removed"] = [*beside["removed"], "an unlisted removal"]
        with pytest.raises(AssertionError):
            compare(name, beside)
