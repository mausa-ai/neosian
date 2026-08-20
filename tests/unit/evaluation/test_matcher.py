"""Matching semantics — typed equality, first-call tool rule, accumulation."""

import re
from typing import Any

import pytest

from neosian._foundation.evaluation.matcher import _equal, match_turn
from neosian._foundation.evaluation.results import ToolCallCapture
from neosian._foundation.evaluation.types import (
    Expectation,
    MatchMode,
    SequenceStep,
    ValueMatcher,
)
from neosian._foundation.shared.types import ToolName

NO_IGNORE: frozenset[ToolName] = frozenset()


def cap(name: str, **arguments: Any) -> ToolCallCapture:
    return ToolCallCapture(
        name=ToolName(name),
        arguments=arguments,
        executed=False,
        ok=True,
        duration_ms=0,
    )


def equals(value: Any) -> ValueMatcher:
    return ValueMatcher(mode=MatchMode.EQUALS, value=value)


@pytest.mark.unit
class TestTypedEquality:
    @pytest.mark.parametrize(
        ("expected", "actual", "outcome"),
        [
            (1, 1, True),
            (1, 1.0, True),  # numeric int↔float stays
            ("1", 1, False),  # the str() coercion is dead
            (1, "1", False),
            (True, 1, False),  # bool is not 1
            (True, True, True),
            ("None", None, False),  # "None" no longer matches a missing value
            ([1, 2], [1, 2], True),
            ([1, 2], [1, 2, 3], False),
            ([1, [2, "x"]], [1, [2, "x"]], True),
            ({"a": 1}, {"a": 1}, True),
            ({"a": 1}, {"a": 1, "b": 2}, False),
            ("x", "x", True),
            (None, None, True),
        ],
    )
    def test_matrix(self, expected: Any, actual: Any, outcome: bool) -> None:
        assert _equal(expected, actual) is outcome

    def test_failure_message_names_types(self) -> None:
        expectation = Expectation(tool=ToolName("t"), params={"n": equals(3)})
        passed, failures = match_turn(
            expectation, [cap("t", n="3")], None, ignore=NO_IGNORE
        )
        assert passed is False
        assert failures == ("param 'n': expected 3 (int), got '3' (str)",)


@pytest.mark.unit
class TestToolRule:
    def test_first_call_must_match(self) -> None:
        expectation = Expectation(tool=ToolName("right"))
        passed, failures = match_turn(
            expectation, [cap("wrong"), cap("right")], None, ignore=NO_IGNORE
        )
        assert passed is False
        assert "expected first tool 'right', got 'wrong'" in failures[0]

    def test_later_calls_are_unconstrained(self) -> None:
        expectation = Expectation(tool=ToolName("a"), params={"q": equals("x")})
        passed, _ = match_turn(
            expectation, [cap("a", q="x"), cap("b"), cap("c")], None, ignore=NO_IGNORE
        )
        assert passed is True

    def test_no_call_fails(self) -> None:
        passed, failures = match_turn(
            Expectation(tool=ToolName("a")), [], None, ignore=NO_IGNORE
        )
        assert passed is False
        assert failures == ("expected tool 'a', no tool called",)

    def test_extra_actual_arguments_are_ignored(self) -> None:
        expectation = Expectation(tool=ToolName("a"), params={"q": equals("x")})
        passed, _ = match_turn(
            expectation, [cap("a", q="x", extra=1)], None, ignore=NO_IGNORE
        )
        assert passed is True

    def test_exists_and_matcher_modes(self) -> None:
        expectation = Expectation(
            tool=ToolName("a"),
            params={
                "p": ValueMatcher(mode=MatchMode.EXISTS),
                "c": ValueMatcher(mode=MatchMode.CONTAINS, value="un"),
                "r": ValueMatcher(mode=MatchMode.REGEX, value=re.compile(r"^\d+$")),
            },
        )
        passed, _ = match_turn(
            expectation, [cap("a", p=0, c="sunset", r="123")], None, ignore=NO_IGNORE
        )
        assert passed is True
        passed, failures = match_turn(
            expectation, [cap("a", p=None, c=["sun"], r="12x")], None, ignore=NO_IGNORE
        )
        assert passed is False
        assert len(failures) == 3  # failures accumulate, never first-only

    def test_contains_on_a_list_matches_elements(self) -> None:
        expectation = Expectation(
            tool=ToolName("a"),
            params={"tags": ValueMatcher(mode=MatchMode.CONTAINS, value="sky")},
        )
        passed, _ = match_turn(
            expectation, [cap("a", tags=["sea", "sky"])], None, ignore=NO_IGNORE
        )
        assert passed is True


@pytest.mark.unit
class TestNoToolAndIgnore:
    def test_no_tool_fails_on_any_visible_call(self) -> None:
        passed, failures = match_turn(
            Expectation(no_tool=True), [cap("t")], None, ignore=NO_IGNORE
        )
        assert passed is False
        assert failures == ("expected no tool call, got 't'",)

    def test_ignored_tools_are_invisible_everywhere(self) -> None:
        ignore = frozenset({ToolName("update_todo")})
        captures = [cap("update_todo"), cap("a", q="x")]
        assert match_turn(
            Expectation(no_tool=True), [cap("update_todo")], None, ignore=ignore
        )[0]
        assert match_turn(
            Expectation(tool=ToolName("a"), params={"q": equals("x")}),
            captures,
            None,
            ignore=ignore,
        )[0]
        sequence = Expectation(sequence=(SequenceStep(tool=ToolName("a")),))
        assert match_turn(sequence, captures, None, ignore=ignore)[0]


@pytest.mark.unit
class TestSequenceRule:
    EXPECT = Expectation(
        sequence=(
            SequenceStep(tool=ToolName("a"), params={"q": equals("x")}),
            SequenceStep(tool=ToolName("b")),
        )
    )

    def test_exact_order_and_length(self) -> None:
        assert match_turn(
            self.EXPECT, [cap("a", q="x"), cap("b")], None, ignore=NO_IGNORE
        )[0]
        passed, failures = match_turn(
            self.EXPECT, [cap("b"), cap("a", q="x")], None, ignore=NO_IGNORE
        )
        assert passed is False
        assert "sequence: expected ['a', 'b'], got ['b', 'a']" in failures[0]
        passed, failures = match_turn(
            self.EXPECT, [cap("a", q="x")], None, ignore=NO_IGNORE
        )
        assert passed is False

    def test_step_params_carry_their_position(self) -> None:
        passed, failures = match_turn(
            self.EXPECT, [cap("a", q="y"), cap("b")], None, ignore=NO_IGNORE
        )
        assert passed is False
        assert failures[0].startswith("sequence step 1 param 'q'")


@pytest.mark.unit
class TestResponseRule:
    def test_contains_is_case_insensitive(self) -> None:
        expectation = Expectation(
            response=(
                ValueMatcher(mode=MatchMode.CONTAINS, value="hello"),
                ValueMatcher(mode=MatchMode.CONTAINS, value="help"),
            )
        )
        passed, _ = match_turn(
            expectation, [], "Hello! How can I HELP?", ignore=NO_IGNORE
        )
        assert passed is True

    def test_equals_and_regex(self) -> None:
        equals_hi = Expectation(
            response=(ValueMatcher(mode=MatchMode.EQUALS, value="hi"),)
        )
        assert match_turn(equals_hi, [], "hi", ignore=NO_IGNORE)[0]
        assert not match_turn(equals_hi, [], "Hi", ignore=NO_IGNORE)[0]
        regex = Expectation(
            response=(ValueMatcher(mode=MatchMode.REGEX, value=re.compile(r"\bok\b")),)
        )
        assert match_turn(regex, [], "all ok here", ignore=NO_IGNORE)[0]

    def test_missing_text_fails(self) -> None:
        expectation = Expectation(
            response=(ValueMatcher(mode=MatchMode.CONTAINS, value="x"),)
        )
        passed, failures = match_turn(expectation, [], None, ignore=NO_IGNORE)
        assert passed is False
        assert failures == ("response: no text in the assistant turn",)
