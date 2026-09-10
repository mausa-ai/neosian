"""Expectation matching (DESIGN §13).

Typed equality throughout — the v1 blanket `str()` coercion is gone:
bool compares only to bool (`True != 1`), str only to str, int↔float
compare numerically, containers compare elementwise/key-exact. Failure
messages name types, because type confusion is where every coercion bug
hid. Failures accumulate — a turn reports every unmet expectation.

One deliberate asymmetry: `response` CONTAINS is case-insensitive
(prose casing is model noise), while param CONTAINS stays exact
(arguments are structured data). Regex covers anything finer.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from neosian._foundation.evaluation.results import ToolCallCapture
from neosian._foundation.evaluation.types import (
    Expectation,
    MatchMode,
    SequenceStep,
    ValueMatcher,
)
from neosian._foundation.shared.types import ToolName

_CLIP = 120


def match_turn(
    expectation: Expectation,
    captures: Sequence[ToolCallCapture],
    response_text: str | None,
    *,
    ignore: frozenset[ToolName],
) -> tuple[bool, tuple[str, ...]]:
    """Score one turn's captures and response text against its expectation.

    Captures named in `ignore` are invisible to every check. Returns
    (passed, failures) with one message per unmet expectation.
    """
    visible = [c for c in captures if c.name not in ignore]
    failures: list[str] = []

    if expectation.no_tool and visible:
        failures.append(f"expected no tool call, got '{visible[0].name}'")

    if expectation.tool is not None:
        if not visible:
            failures.append(f"expected tool '{expectation.tool}', no tool called")
        elif visible[0].name != expectation.tool:
            failures.append(
                f"expected first tool '{expectation.tool}', " f"got '{visible[0].name}'"
            )
        else:
            failures.extend(
                _match_params(expectation.params, visible[0].arguments, where="")
            )

    if expectation.sequence is not None:
        failures.extend(_match_sequence(expectation.sequence, visible))

    for matcher in expectation.response:
        reason = (
            "response: no text in the assistant turn"
            if not response_text
            else match_text(matcher, response_text, label="response")
        )
        if reason is not None:
            failures.append(reason)

    return (not failures, tuple(failures))


def _match_params(
    params: Mapping[str, ValueMatcher],
    arguments: Mapping[str, Any],
    *,
    where: str,
) -> list[str]:
    failures: list[str] = []
    for name, matcher in params.items():
        reason = _match_value(matcher, name in arguments, arguments.get(name))
        if reason is not None:
            failures.append(f"{where}param '{name}': {reason}")
    return failures


def _match_value(matcher: ValueMatcher, present: bool, actual: Any) -> str | None:
    if matcher.mode is MatchMode.EXISTS:
        if not present:
            return "expected to exist, missing"
        if actual is None:
            return "expected to exist, got None"
        return None
    if not present:
        return f"expected {matcher.describe()}, missing"
    if matcher.mode is MatchMode.EQUALS:
        if _equal(matcher.value, actual):
            return None
        return (
            f"expected {matcher.value!r} ({_type_name(matcher.value)}), "
            f"got {actual!r} ({_type_name(actual)})"
        )
    if matcher.mode is MatchMode.CONTAINS:
        if isinstance(actual, str):
            return (
                None
                if matcher.value in actual
                else f"expected to contain {matcher.value!r}, got {clip(actual)!r}"
            )
        if isinstance(actual, list):
            return (
                None
                if any(_equal(matcher.value, element) for element in actual)
                else f"expected to contain {matcher.value!r}, got {actual!r}"
            )
        return (
            f"expected to contain {matcher.value!r}, "
            f"got {_type_name(actual)} {actual!r}"
        )
    # REGEX
    if not isinstance(actual, str):
        return f"expected {matcher.describe()}, got {_type_name(actual)} {actual!r}"
    if matcher.value.search(actual) is None:
        return f"expected {matcher.describe()} to match, got {clip(actual)!r}"
    return None


def _match_sequence(
    steps: tuple[SequenceStep, ...], visible: list[ToolCallCapture]
) -> list[str]:
    expected_names = [str(s.tool) for s in steps]
    actual_names = [str(c.name) for c in visible]
    if expected_names != actual_names:
        return [f"sequence: expected {expected_names}, got {actual_names}"]
    failures: list[str] = []
    for idx, (step, capture) in enumerate(zip(steps, visible, strict=True), start=1):
        failures.extend(
            _match_params(step.params, capture.arguments, where=f"sequence step {idx} ")
        )
    return failures


def match_text(matcher: ValueMatcher, text: str, *, label: str) -> str | None:
    """Match model-authored prose: `contains` is case-insensitive (§13.4).

    `label` locates the text in failure messages ("response", a document
    path) — the memory kind scores document content with the same rules.
    """
    if matcher.mode is MatchMode.EQUALS:
        if text == matcher.value:
            return None
        return f"{label}: expected equals {matcher.value!r}, got {clip(text)!r}"
    if matcher.mode is MatchMode.CONTAINS:
        if matcher.value.lower() in text.lower():
            return None
        return f"{label}: expected to contain {matcher.value!r}, got {clip(text)!r}"
    # REGEX
    if matcher.value.search(text) is None:
        return f"{label}: expected {matcher.describe()} to match, got {clip(text)!r}"
    return None


def _equal(expected: Any, actual: Any) -> bool:
    """Typed equality: bool only to bool, str only to str, int↔float
    numeric, containers structural, otherwise same-type `==`."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return (
            isinstance(expected, bool)
            and isinstance(actual, bool)
            and (expected is actual)
        )
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    if isinstance(expected, str) or isinstance(actual, str):
        return (
            isinstance(expected, str)
            and isinstance(actual, str)
            and (expected == actual)
        )
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        return len(expected) == len(actual) and all(
            _equal(e, a) for e, a in zip(expected, actual, strict=True)
        )
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _equal(value, actual[key]) for key, value in expected.items()
        )
    return type(expected) is type(actual) and bool(expected == actual)


def _type_name(value: Any) -> str:
    return type(value).__name__


def clip(text: str, limit: int = _CLIP) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
