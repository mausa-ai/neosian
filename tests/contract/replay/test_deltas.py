"""The delta list stays small, reviewed and exact (owner ruling on #448).

Each entry names a frozen case, a path that exists in its expectation, a
literal that differs from the frozen value there, and decision #448. The
comparison changes exactly the listed values: an expectation with every
delta applied differs from the frozen one at those paths and nowhere else,
so the replay test still fails on any difference the list does not name.
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
MOST = 12


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
    assert 0 < len(DELTAS) <= MOST
    names = {case.name for case in CASES}
    seen = set()
    for delta in DELTAS:
        assert sorted(delta) == ["at", "case", "decision", "literal"]
        assert delta["decision"] in DECISIONS
        assert delta["case"] in names and delta["case"] in EXPECTED
        key = (delta["case"], json.dumps(delta["at"]))
        assert key not in seen
        seen.add(key)
        container, last = located(EXPECTED[delta["case"]], delta["at"])
        frozen = container[last]
        assert frozen != delta["literal"]
        assert type(frozen) is type(delta["literal"])


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


def test_an_unlisted_difference_still_fails_the_comparison() -> None:
    """The replay test's own comparison, given answers made up here: the
    frozen answer with exactly the listed deltas passes; the released answer
    without them fails; one more difference anywhere fails."""
    for name in sorted({delta["case"] for delta in DELTAS}):
        expected = expected_for(name, EXPECTED[name])
        compare(name, expected)
        with pytest.raises(AssertionError):
            compare(name, EXPECTED[name])
        listed = next(d for d in DELTAS if d["case"] == name)
        observed = json.loads(json.dumps(expected))
        container, last = located(observed, listed["at"])
        if isinstance(container[last], str):
            container[last] += "\n[an answer no decision names]"
        else:
            container[last]["description"] += " An unlisted sentence."
        with pytest.raises(AssertionError):
            compare(name, observed)
        beside = json.loads(json.dumps(expected))
        beside["removed"] = [*beside["removed"], "an unlisted removal"]
        with pytest.raises(AssertionError):
            compare(name, beside)
