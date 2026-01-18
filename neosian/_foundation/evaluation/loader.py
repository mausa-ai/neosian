"""YAML config loader for evaluation.

Parses eval config files into typed EvalConfig objects.
"""

from pathlib import Path
from typing import Any

import yaml

from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigNotFoundError,
)
from neosian._foundation.shared.types import (
    EvalCase,
    EvalConfig,
    EvalTurn,
    Expectation,
)


def load_eval_config(path: str | Path) -> EvalConfig:
    """Load evaluation config from YAML file.

    Args:
        path: Path to the eval config YAML file.

    Returns:
        Parsed EvalConfig object.

    Raises:
        EvalConfigNotFoundError: If file doesn't exist.
        EvalConfigInvalidYAMLError: If YAML is malformed.
        EvalConfigMissingKeyError: If required key is missing.
        EvalCaseInvalidError: If a case definition is invalid.
    """
    path = Path(path)
    path_str = str(path)

    if not path.exists():
        raise EvalConfigNotFoundError(path_str)

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise EvalConfigInvalidYAMLError(path_str) from e

    if not isinstance(data, dict):
        raise EvalConfigInvalidYAMLError(path_str)

    # Validate required keys
    for key in [
        Evaluation.NAME_KEY,
        Evaluation.PROMPTS_KEY,
        Evaluation.MODELS_KEY,
        Evaluation.CASES_KEY,
    ]:
        if key not in data:
            raise EvalConfigMissingKeyError(key, path_str)

    # Parse cases
    cases = _parse_cases(data[Evaluation.CASES_KEY])

    # Optional agent key for variant mode
    agent = data.get(Evaluation.AGENT_KEY)

    # Optional behavior configuration (defaults to True)
    stop_on_failure = data.get(Evaluation.STOP_ON_FAILURE_KEY, True)

    return EvalConfig(
        name=data[Evaluation.NAME_KEY],
        prompts=data[Evaluation.PROMPTS_KEY],
        models=data[Evaluation.MODELS_KEY],
        cases=cases,
        agent=agent,
        stop_on_failure=stop_on_failure,
    )


def _parse_cases(cases_data: list[dict[str, Any]]) -> list[EvalCase]:
    """Parse case definitions from YAML data.

    Args:
        cases_data: List of case dictionaries from YAML.

    Returns:
        List of EvalCase objects.

    Raises:
        EvalCaseInvalidError: If a case is malformed.
    """
    cases = []
    for case_data in cases_data:
        if "name" not in case_data:
            raise EvalCaseInvalidError("unknown", "missing 'name' field")

        name = case_data["name"]

        # Conversational case
        if Evaluation.CONVERSATION_KEY in case_data:
            conversation = _parse_conversation(
                case_data[Evaluation.CONVERSATION_KEY], name
            )
            cases.append(
                EvalCase(
                    name=name,
                    conversation=conversation,
                )
            )
        # One-shot case
        elif Evaluation.INPUT_KEY in case_data:
            expect = _parse_expectation(case_data.get(Evaluation.EXPECT_KEY, {}))
            cases.append(
                EvalCase(
                    name=name,
                    input=case_data[Evaluation.INPUT_KEY],
                    expect=expect,
                )
            )
        else:
            raise EvalCaseInvalidError(name, "must have 'input' or 'conversation'")

    return cases


def _parse_conversation(
    conv_data: list[dict[str, Any]], case_name: str
) -> list[EvalTurn]:
    """Parse conversation turns from YAML data.

    Args:
        conv_data: List of turn dictionaries.
        case_name: Case name for error messages.

    Returns:
        List of EvalTurn objects.

    Raises:
        EvalCaseInvalidError: If a turn is malformed.
    """
    turns = []
    for i, turn_data in enumerate(conv_data):
        if Evaluation.USER_KEY not in turn_data:
            raise EvalCaseInvalidError(case_name, f"turn {i + 1} missing 'user' field")

        expect = _parse_expectation(turn_data.get(Evaluation.EXPECT_KEY, {}))
        turns.append(
            EvalTurn(
                user=turn_data[Evaluation.USER_KEY],
                expect=expect,
            )
        )
    return turns


def _parse_expectation(expect_data: dict[str, Any]) -> Expectation:
    """Parse expectation from YAML data.

    Args:
        expect_data: Expectation dictionary.

    Returns:
        Expectation object.
    """
    return Expectation(
        tool=expect_data.get(Evaluation.TOOL_KEY),
        params=expect_data.get(Evaluation.PARAMS_KEY, {}),
        no_tool=expect_data.get(Evaluation.NO_TOOL_KEY, False),
        sequence=expect_data.get(Evaluation.SEQUENCE_KEY),
    )
