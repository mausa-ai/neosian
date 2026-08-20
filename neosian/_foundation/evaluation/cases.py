"""Case parsing for eval suites (DESIGN §13).

Split from loader.py so each stays under the size gate; the strict-key
discipline is identical. `parse_names` is shared with the suite level.
"""

from typing import Any

from neosian._foundation.evaluation.expectations import parse_expectation
from neosian._foundation.evaluation.types import EvalCase, EvalTurn
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeTurn
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalConfigInvalidYAMLError,
)
from neosian._foundation.shared.types import ToolCallId, ToolName

_CASE_KEYS = frozenset(
    {"name", "input", "expect", "conversation", "script", "execute_tools"}
)
_TURN_KEYS = frozenset({"user", "expect", "tool_results"})
_V1_TURN_HINTS = {
    "mock_response": "schema v2 replaced it with 'tool_results'",
}


def parse_names(data: Any, key: str, path_str: str) -> frozenset[ToolName]:
    if data is None:
        return frozenset()
    if not isinstance(data, list) or not all(isinstance(n, str) for n in data):
        raise EvalConfigInvalidYAMLError(
            path_str, f"'{key}' must be a list of tool names"
        )
    return frozenset(ToolName(n) for n in data)


def parse_cases(data: Any, path_str: str) -> tuple[EvalCase, ...]:
    if not isinstance(data, list) or not data:
        raise EvalConfigInvalidYAMLError(path_str, "'cases' must be a non-empty list")
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for case_data in data:
        case = parse_case(case_data)
        if case.name in seen:
            raise EvalConfigInvalidYAMLError(
                path_str, f"duplicate case name '{case.name}'"
            )
        seen.add(case.name)
        cases.append(case)
    return tuple(cases)


def parse_case(data: Any) -> EvalCase:
    if not isinstance(data, dict):
        raise EvalCaseInvalidError("unknown", "case must be a mapping")
    if "name" not in data or not isinstance(data["name"], str):
        raise EvalCaseInvalidError("unknown", "missing 'name'")
    name = data["name"]
    for key in data:
        if key not in _CASE_KEYS:
            raise EvalCaseInvalidError(name, f"unknown key '{key}'")

    if "input" in data and "conversation" in data:
        raise EvalCaseInvalidError(name, "'input' and 'conversation' are exclusive")
    if "input" in data:
        if not isinstance(data["input"], str):
            raise EvalCaseInvalidError(name, "'input' must be a string")
        if "expect" not in data:
            raise EvalCaseInvalidError(name, "a one-shot case requires 'expect'")
        turns: tuple[EvalTurn, ...] = (
            EvalTurn(
                user=data["input"],
                expect=parse_expectation(data["expect"], name, "expect"),
            ),
        )
    elif "conversation" in data:
        if "expect" in data:
            raise EvalCaseInvalidError(
                name,
                "top-level 'expect' is invalid on a conversational case — "
                "put it on the turn",
            )
        turns = _parse_turns(data["conversation"], name)
    else:
        raise EvalCaseInvalidError(name, "must have 'input' or 'conversation'")

    return EvalCase(
        name=name,
        turns=turns,
        script=_parse_script(data.get("script"), name),
        execute_tools=parse_names(data.get("execute_tools"), "execute_tools", name),
    )


def _parse_turns(data: Any, case_name: str) -> tuple[EvalTurn, ...]:
    if not isinstance(data, list) or not data:
        raise EvalCaseInvalidError(case_name, "'conversation' must be a non-empty list")
    turns: list[EvalTurn] = []
    for idx, turn_data in enumerate(data, start=1):
        if not isinstance(turn_data, dict):
            raise EvalCaseInvalidError(case_name, f"turn {idx} must be a mapping")
        for key in turn_data:
            if key not in _TURN_KEYS:
                raise EvalCaseInvalidError(
                    case_name,
                    f"turn {idx}: unknown key '{key}'"
                    + (f" — {_V1_TURN_HINTS[key]}" if key in _V1_TURN_HINTS else ""),
                )
        if "user" not in turn_data or not isinstance(turn_data["user"], str):
            raise EvalCaseInvalidError(case_name, f"turn {idx} missing 'user'")
        if "expect" not in turn_data:
            raise EvalCaseInvalidError(case_name, f"turn {idx} missing 'expect'")
        turns.append(
            EvalTurn(
                user=turn_data["user"],
                expect=parse_expectation(
                    turn_data["expect"], case_name, f"turn {idx} expect"
                ),
                tool_results=_parse_tool_results(
                    turn_data.get("tool_results"), case_name, idx
                ),
            )
        )
    return tuple(turns)


def _parse_tool_results(
    data: Any, case_name: str, turn_idx: int
) -> dict[ToolName, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise EvalCaseInvalidError(
            case_name, f"turn {turn_idx}: 'tool_results' must be a mapping"
        )
    return {ToolName(str(tool)): payload for tool, payload in data.items()}


def _parse_script(data: Any, case_name: str) -> tuple[FakeTurn, ...] | None:
    """Parse a case's scripted model turns into FakeTurns."""
    if data is None:
        return None
    if not isinstance(data, list) or not data:
        raise EvalCaseInvalidError(case_name, "'script' must be a non-empty list")
    turns: list[FakeTurn] = []
    for turn_idx, turn_data in enumerate(data):
        if not isinstance(turn_data, dict):
            raise EvalCaseInvalidError(
                case_name, f"script turn {turn_idx + 1} must be a mapping"
            )
        tool_calls: list[ToolCall] = []
        for call_idx, call_data in enumerate(turn_data.get("tool_calls") or []):
            if not isinstance(call_data, dict) or "name" not in call_data:
                raise EvalCaseInvalidError(
                    case_name,
                    f"script turn {turn_idx + 1} tool_call {call_idx + 1} "
                    "must be a mapping with a 'name'",
                )
            tool_calls.append(
                ToolCall(
                    id=ToolCallId(f"script_{turn_idx}_{call_idx}"),
                    name=ToolName(call_data["name"]),
                    arguments=call_data.get("arguments") or {},
                )
            )
        turns.append(
            FakeTurn(
                content=turn_data.get("content"),
                reasoning=turn_data.get("reasoning"),
                tool_calls=tuple(tool_calls),
            )
        )
    return tuple(turns)
