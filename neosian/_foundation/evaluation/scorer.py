"""Scoring logic for evaluation.

Simple matching: exact values or _exists check.
"""

from typing import Any

from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.types import (
    Expectation,
    ToolCallCapture,
    TurnResult,
)


def match_value(expected: Any, actual: Any) -> tuple[bool, str | None]:
    """Match expected value against actual value.

    Rules:
    - "_exists" → param is present (not None)
    - Everything else → exact match (with type coercion for numbers)

    Args:
        expected: Expected value or "_exists" marker.
        actual: Actual value from tool call.

    Returns:
        Tuple of (matched, failure_reason).
    """
    # Exists check
    if expected == Evaluation.EXISTS_MARKER:
        if actual is not None:
            return (True, None)
        return (False, Evaluation.Scorer.PARAM_EXISTS_FAIL)

    # Exact match
    if expected == actual:
        return (True, None)

    # Type-coerced comparison for numbers
    try:
        is_num = isinstance(expected, (int, float)) or isinstance(actual, (int, float))
        if is_num and float(expected) == float(actual):
            return (True, None)
    except (ValueError, TypeError):
        pass

    # String comparison (case-sensitive)
    if str(expected) == str(actual):
        return (True, None)

    return (
        False,
        Evaluation.Scorer.PARAM_MISMATCH.format(expected=expected, actual=actual),
    )


def score_turn(
    expectation: Expectation,
    tool_calls: list[ToolCallCapture],
    response_content: str | None,
    turn_index: int = 0,
) -> TurnResult:
    """Score a single turn against expectations.

    Args:
        expectation: What was expected.
        tool_calls: Tool calls that were made.
        response_content: Assistant response content.
        turn_index: Index of this turn in conversation.

    Returns:
        TurnResult with pass/fail and details.
    """
    # Case 1: Expected no tool call
    if expectation.no_tool:
        if tool_calls:
            return TurnResult(
                turn_index=turn_index,
                passed=False,
                expected_no_tool=True,
                actual_tool=tool_calls[0].name,
                actual_params=tool_calls[0].arguments,
                actual_response=response_content,
                error=Evaluation.Scorer.EXPECTED_NO_TOOL.format(
                    tool=tool_calls[0].name
                ),
            )
        return TurnResult(
            turn_index=turn_index,
            passed=True,
            expected_no_tool=True,
            actual_response=response_content,
        )

    # Case 2: Expected a specific tool
    if expectation.tool:
        # Find the expected tool in calls
        actual_tool: str | None = None
        actual_params: dict[str, Any] = {}

        for tc in tool_calls:
            if tc.name == expectation.tool:
                actual_tool = tc.name
                actual_params = tc.arguments
                break

        if actual_tool is None:
            if tool_calls:
                return TurnResult(
                    turn_index=turn_index,
                    passed=False,
                    expected_tool=expectation.tool,
                    actual_tool=tool_calls[0].name,
                    actual_params=tool_calls[0].arguments,
                    expected_params=expectation.params,
                    actual_response=response_content,
                    error=Evaluation.Scorer.WRONG_TOOL.format(
                        expected=expectation.tool, actual=tool_calls[0].name
                    ),
                )
            return TurnResult(
                turn_index=turn_index,
                passed=False,
                expected_tool=expectation.tool,
                expected_params=expectation.params,
                actual_response=response_content,
                error=Evaluation.Scorer.NO_TOOL_CALLED.format(
                    expected=expectation.tool
                ),
            )

        # Check parameters
        param_failures: list[str] = []
        for param_name, expected_value in expectation.params.items():
            actual_value = actual_params.get(param_name)
            matched, reason = match_value(expected_value, actual_value)
            if not matched:
                param_failures.append(f"{param_name}: {reason}")

        passed = len(param_failures) == 0
        return TurnResult(
            turn_index=turn_index,
            passed=passed,
            expected_tool=expectation.tool,
            actual_tool=actual_tool,
            expected_params=expectation.params,
            actual_params=actual_params,
            param_failures=param_failures,
            actual_response=response_content,
            error="; ".join(param_failures) if param_failures else None,
        )

    # Case 3: Expected a sequence of tools
    if expectation.sequence:
        expected_names = [step.get("tool") for step in expectation.sequence]
        actual_names = [tc.name for tc in tool_calls]

        if expected_names != actual_names:
            return TurnResult(
                turn_index=turn_index,
                passed=False,
                actual_response=response_content,
                error=Evaluation.Scorer.SEQUENCE_MISMATCH.format(
                    expected=expected_names, actual=actual_names
                ),
            )

        # Check params for each step
        param_failures = []
        for i, step in enumerate(expectation.sequence):
            step_params = step.get("params", {})
            if i < len(tool_calls):
                for param_name, expected_value in step_params.items():
                    actual_value = tool_calls[i].arguments.get(param_name)
                    matched, reason = match_value(expected_value, actual_value)
                    if not matched:
                        param_failures.append(
                            Evaluation.Scorer.STEP_PARAM_FAIL.format(
                                step=i + 1, param=param_name, reason=reason
                            )
                        )

        passed = len(param_failures) == 0
        return TurnResult(
            turn_index=turn_index,
            passed=passed,
            actual_response=response_content,
            param_failures=param_failures,
            error="; ".join(param_failures) if param_failures else None,
        )

    # No expectations (pass by default)
    return TurnResult(
        turn_index=turn_index,
        passed=True,
        actual_response=response_content,
    )
