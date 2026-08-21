"""`expect:` block parsing (DESIGN §13).

Strict on purpose: every unknown key, empty block, or misplaced
combination is an `eval_case_invalid` at load time — nothing about an
expectation is ever silently dropped. Regexes compile here, so a bad
pattern fails the config, never a run.
"""

import re
from typing import Any

from neosian._foundation.evaluation.types import (
    Expectation,
    MatchMode,
    SequenceStep,
    ValueMatcher,
)
from neosian._foundation.shared.exceptions import EvalCaseInvalidError
from neosian._foundation.shared.types import ToolName

_EXISTS_SUGAR = "_exists"
_MATCHER_KEYS = frozenset(m.value for m in MatchMode)
_EXPECT_KEYS = frozenset({"tool", "params", "no_tool", "sequence", "response"})
_STEP_KEYS = frozenset({"tool", "params"})


def parse_expectation(data: Any, case_name: str, where: str) -> Expectation:
    """Parse one `expect:` block. `where` locates it in error messages
    (e.g. "expect" or "turn 2 expect")."""
    if not isinstance(data, dict) or not data:
        raise EvalCaseInvalidError(case_name, f"{where} must be a non-empty mapping")
    for key in data:
        if key not in _EXPECT_KEYS:
            raise EvalCaseInvalidError(case_name, f"{where}: unknown key '{key}'")

    tool = data.get("tool")
    exclusive = [k for k in ("tool", "sequence") if k in data]
    if "no_tool" in data:
        if data["no_tool"] is not True:
            raise EvalCaseInvalidError(
                case_name, f"{where}: 'no_tool' must be true — remove it otherwise"
            )
        exclusive.append("no_tool")
    if len(exclusive) > 1:
        raise EvalCaseInvalidError(
            case_name, f"{where}: {' and '.join(exclusive)} are mutually exclusive"
        )
    if "params" in data and tool is None:
        raise EvalCaseInvalidError(
            case_name, f"{where}: 'params' is only valid beside 'tool'"
        )
    if tool is not None and not isinstance(tool, str):
        raise EvalCaseInvalidError(case_name, f"{where}: 'tool' must be a string")
    if not exclusive and "response" not in data:
        raise EvalCaseInvalidError(
            case_name,
            f"{where} must state one of 'tool', 'sequence', 'no_tool', 'response'",
        )

    return Expectation(
        tool=ToolName(tool) if tool is not None else None,
        params=_parse_params(data.get("params"), case_name, where),
        sequence=_parse_sequence(data.get("sequence"), case_name, where),
        no_tool="no_tool" in data,
        response=parse_response(data.get("response"), case_name, where),
    )


def _parse_params(data: Any, case_name: str, where: str) -> dict[str, ValueMatcher]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise EvalCaseInvalidError(case_name, f"{where}: 'params' must be a mapping")
    return {
        str(name): _parse_matcher(value, case_name, f"{where} param '{name}'")
        for name, value in data.items()
    }


def _parse_matcher(value: Any, case_name: str, where: str) -> ValueMatcher:
    """A one-key mapping over the matcher vocabulary is a matcher; the
    `_exists` string is sugar; everything else is an EQUALS literal. A
    literal one-key `{"equals": …}` value is written `{equals: {equals: …}}`.
    """
    if value == _EXISTS_SUGAR:
        return ValueMatcher(mode=MatchMode.EXISTS)
    if not (isinstance(value, dict) and len(value) == 1):
        return ValueMatcher(mode=MatchMode.EQUALS, value=value)
    key, inner = next(iter(value.items()))
    if key not in _MATCHER_KEYS:
        return ValueMatcher(mode=MatchMode.EQUALS, value=value)
    mode = MatchMode(key)
    if mode is MatchMode.EXISTS:
        if inner is not True:
            raise EvalCaseInvalidError(
                case_name, f"{where}: 'exists' must be true — use equals for absence"
            )
        return ValueMatcher(mode=MatchMode.EXISTS)
    if mode is MatchMode.EQUALS:
        return ValueMatcher(mode=MatchMode.EQUALS, value=inner)
    if not isinstance(inner, str):
        raise EvalCaseInvalidError(
            case_name, f"{where}: '{key}' takes a string, got {type(inner).__name__}"
        )
    if mode is MatchMode.REGEX:
        return ValueMatcher(
            mode=MatchMode.REGEX, value=_compile(inner, case_name, where)
        )
    return ValueMatcher(mode=MatchMode.CONTAINS, value=inner)


def _parse_sequence(
    data: Any, case_name: str, where: str
) -> tuple[SequenceStep, ...] | None:
    if data is None:
        return None
    if not isinstance(data, list) or not data:
        raise EvalCaseInvalidError(
            case_name, f"{where}: 'sequence' must be a non-empty list"
        )
    steps: list[SequenceStep] = []
    for idx, step in enumerate(data, start=1):
        step_where = f"{where} sequence step {idx}"
        if not isinstance(step, dict) or "tool" not in step:
            raise EvalCaseInvalidError(
                case_name, f"{step_where} must be a mapping with a 'tool'"
            )
        for key in step:
            if key not in _STEP_KEYS:
                raise EvalCaseInvalidError(
                    case_name, f"{step_where}: unknown key '{key}'"
                )
        if not isinstance(step["tool"], str):
            raise EvalCaseInvalidError(
                case_name, f"{step_where}: 'tool' must be a string"
            )
        steps.append(
            SequenceStep(
                tool=ToolName(step["tool"]),
                params=_parse_params(step.get("params"), case_name, step_where),
            )
        )
    return tuple(steps)


def parse_response(
    data: Any, case_name: str, where: str, *, key_name: str = "response"
) -> tuple[ValueMatcher, ...]:
    """A text-matcher block is an explicit one-key matcher mapping —
    never a bare string, so equals-vs-contains is always the author's
    stated intent. `contains`/`regex` accept a list for all-of matching.
    The memory kind parses document `content:` with `key_name`.
    """
    if data is None:
        return ()
    err = (
        f"{where}: '{key_name}' must be a one-key mapping of "
        f'equals/contains/regex (e.g. {key_name}: {{contains: "hello"}})'
    )
    if not (isinstance(data, dict) and len(data) == 1):
        raise EvalCaseInvalidError(case_name, err)
    key, inner = next(iter(data.items()))
    if key not in _MATCHER_KEYS or key == MatchMode.EXISTS.value:
        raise EvalCaseInvalidError(case_name, err)
    mode = MatchMode(key)
    values = inner if isinstance(inner, list) else [inner]
    if mode is MatchMode.EQUALS and len(values) != 1:
        raise EvalCaseInvalidError(
            case_name, f"{where}: {key_name} 'equals' takes a single string"
        )
    matchers: list[ValueMatcher] = []
    for value in values:
        if not isinstance(value, str):
            raise EvalCaseInvalidError(
                case_name,
                f"{where}: {key_name} '{key}' takes strings, "
                f"got {type(value).__name__}",
            )
        if mode is MatchMode.REGEX:
            matchers.append(
                ValueMatcher(mode=mode, value=_compile(value, case_name, where))
            )
        else:
            matchers.append(ValueMatcher(mode=mode, value=value))
    return tuple(matchers)


def _compile(pattern: str, case_name: str, where: str) -> "re.Pattern[str]":
    try:
        return re.compile(pattern)
    except re.error as e:
        raise EvalCaseInvalidError(
            case_name, f"{where}: invalid regex {pattern!r} ({e})"
        ) from e
