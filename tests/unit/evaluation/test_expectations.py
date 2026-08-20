"""`expect:` parsing — matcher sugar, exclusivity, load-time strictness."""

from typing import Any

import pytest

from neosian._foundation.evaluation.expectations import parse_expectation
from neosian._foundation.evaluation.types import MatchMode
from neosian._foundation.shared.exceptions import EvalCaseInvalidError


def _parse(data: dict[str, Any]) -> Any:
    return parse_expectation(data, "case", "expect")


@pytest.mark.unit
class TestMatcherVocabulary:
    def test_exists_sugar_and_explicit_form(self) -> None:
        expect = _parse(
            {"tool": "t", "params": {"a": "_exists", "b": {"exists": True}}}
        )
        assert expect.params["a"].mode is MatchMode.EXISTS
        assert expect.params["b"].mode is MatchMode.EXISTS

    def test_plain_values_are_equals_literals(self) -> None:
        expect = _parse(
            {"tool": "t", "params": {"n": 3, "s": "x", "l": [1, 2], "flag": True}}
        )
        assert expect.params["n"].mode is MatchMode.EQUALS
        assert expect.params["n"].value == 3
        assert expect.params["l"].value == [1, 2]

    def test_one_key_matcher_mappings(self) -> None:
        expect = _parse(
            {
                "tool": "t",
                "params": {
                    "e": {"equals": "16:9"},
                    "c": {"contains": "sun"},
                    "r": {"regex": "^a+$"},
                },
            }
        )
        assert expect.params["e"].mode is MatchMode.EQUALS
        assert expect.params["c"].mode is MatchMode.CONTAINS
        assert expect.params["r"].mode is MatchMode.REGEX
        assert expect.params["r"].value.pattern == "^a+$"

    def test_other_mappings_stay_literals(self) -> None:
        expect = _parse({"tool": "t", "params": {"m": {"width": 1, "height": 2}}})
        assert expect.params["m"].mode is MatchMode.EQUALS
        assert expect.params["m"].value == {"width": 1, "height": 2}

    def test_literal_equals_mapping_needs_the_escape(self) -> None:
        expect = _parse({"tool": "t", "params": {"m": {"equals": {"equals": "x"}}}})
        assert expect.params["m"].mode is MatchMode.EQUALS
        assert expect.params["m"].value == {"equals": "x"}

    @pytest.mark.parametrize(
        "value",
        [
            {"exists": False},
            {"contains": 3},
            {"regex": ["a"]},
            {"regex": "[unclosed"},
        ],
    )
    def test_malformed_matchers_fail(self, value: Any) -> None:
        with pytest.raises(EvalCaseInvalidError):
            _parse({"tool": "t", "params": {"p": value}})


@pytest.mark.unit
class TestExpectShape:
    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ({}, "non-empty mapping"),
            (None, "non-empty mapping"),
            ({"tool": "a", "sequence": [{"tool": "b"}]}, "mutually exclusive"),
            ({"tool": "a", "no_tool": True}, "mutually exclusive"),
            ({"sequence": [{"tool": "b"}], "no_tool": True}, "mutually exclusive"),
            ({"params": {"a": 1}}, "only valid beside 'tool'"),
            ({"no_tool": False}, "'no_tool' must be true"),
            ({"tool": 3}, "'tool' must be a string"),
            ({"retries": 2}, "unknown key 'retries'"),
        ],
    )
    def test_invalid_shapes(self, data: Any, message: str) -> None:
        with pytest.raises(EvalCaseInvalidError, match=message):
            _parse(data)

    def test_no_tool_composes_with_response(self) -> None:
        expect = _parse({"no_tool": True, "response": {"contains": "hi"}})
        assert expect.no_tool is True
        assert expect.response[0].mode is MatchMode.CONTAINS


@pytest.mark.unit
class TestSequence:
    def test_steps_parse_with_params(self) -> None:
        expect = _parse(
            {
                "sequence": [
                    {"tool": "a", "params": {"q": "_exists"}},
                    {"tool": "b"},
                ]
            }
        )
        assert expect.sequence is not None
        assert [s.tool for s in expect.sequence] == ["a", "b"]
        assert expect.sequence[0].params["q"].mode is MatchMode.EXISTS

    @pytest.mark.parametrize(
        "sequence",
        [[], [{"params": {}}], [{"tool": 3}], [{"tool": "a", "when": "later"}]],
    )
    def test_malformed_sequences_fail(self, sequence: Any) -> None:
        with pytest.raises(EvalCaseInvalidError):
            _parse({"sequence": sequence})


@pytest.mark.unit
class TestResponse:
    def test_contains_list_is_all_of(self) -> None:
        expect = _parse({"response": {"contains": ["hello", "help"]}})
        assert [m.value for m in expect.response] == ["hello", "help"]
        assert all(m.mode is MatchMode.CONTAINS for m in expect.response)

    def test_equals_and_regex_forms(self) -> None:
        assert _parse({"response": {"equals": "hi"}}).response[0].value == "hi"
        regex = _parse({"response": {"regex": "h."}}).response[0]
        assert regex.value.pattern == "h."

    @pytest.mark.parametrize(
        "response",
        [
            "hello",  # bare strings are ambiguous — refused
            {"exists": True},
            {"equals": ["a", "b"]},
            {"contains": [1]},
            {"equals": "a", "contains": "b"},
        ],
    )
    def test_malformed_responses_fail(self, response: Any) -> None:
        with pytest.raises(EvalCaseInvalidError):
            _parse({"response": response})
