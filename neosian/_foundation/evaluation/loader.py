"""Eval suite loader — YAML schema v2 (DESIGN §13).

Strict keys at every level: an unknown key is an error, never silently
carried. Retired v1 keys (`prompts:`, `mock_response:`) fail with a
targeted migration hint. Models are validated here, so a typo fails the
suite instantly instead of surfacing inside every case result.
"""

from pathlib import Path
from typing import Any

import yaml

from neosian._foundation.evaluation.cases import parse_cases, parse_names
from neosian._foundation.evaluation.types import (
    BASE_VARIANT,
    AgentEvalConfig,
    EvalConfig,
    EvalKind,
    Variant,
)
from neosian._foundation.evaluation.variants import load_variant
from neosian._foundation.shared.exceptions import (
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigNotFoundError,
    EvalConfigUnknownKeyError,
    EvalModelUnknownError,
)
from neosian._foundation.shared.types import Model

_SUITE_KEYS = frozenset(
    {
        "kind",
        "name",
        "agent",
        "models",
        "cases",
        "variants",
        "stop_on_failure",
        "throttle_ms",
        "execute_tools",
        "ignore_tools",
    }
)
_REQUIRED_KEYS = ("name", "agent", "models", "cases")
_VARIANT_ENTRY_KEYS = frozenset({"name", "prompt"})
_V1_SUITE_HINTS = {
    "prompts": "schema v2 replaced it with 'agent:' + 'variants:'",
}


def load_eval_config(path: str | Path) -> EvalConfig:
    """Load an eval suite from a YAML file.

    Raises:
        EvalConfigNotFoundError: If the file doesn't exist.
        EvalConfigInvalidYAMLError: If unparseable or structurally invalid.
        EvalConfigMissingKeyError: If a required key is absent.
        EvalConfigUnknownKeyError: If a key isn't in the schema.
        EvalModelUnknownError: If the models axis names an unknown model.
        EvalCaseInvalidError: If a case definition is invalid.
        EvalPromptNotFoundError: If a variant's prompt file is missing.
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
        raise EvalConfigInvalidYAMLError(path_str, "root must be a mapping")

    for key in data:
        if key not in _SUITE_KEYS:
            raise EvalConfigUnknownKeyError(
                str(key), path_str, _V1_SUITE_HINTS.get(str(key))
            )
    for key in _REQUIRED_KEYS:
        if key not in data:
            raise EvalConfigMissingKeyError(key, path_str)

    kind = data.get("kind", EvalKind.AGENT.value)
    if kind != EvalKind.AGENT.value:
        raise EvalConfigInvalidYAMLError(
            path_str, f"unknown kind '{kind}' — known kinds: agent"
        )
    name = data["name"]
    agent = data["agent"]
    if not isinstance(name, str) or not isinstance(agent, str):
        raise EvalConfigInvalidYAMLError(path_str, "'name' and 'agent' must be strings")

    cases = parse_cases(data["cases"], path_str)
    stop_on_failure = data.get("stop_on_failure", True)
    if not isinstance(stop_on_failure, bool):
        raise EvalConfigInvalidYAMLError(path_str, "'stop_on_failure' must be a bool")
    throttle_ms = data.get("throttle_ms", 500)
    if not isinstance(throttle_ms, int) or isinstance(throttle_ms, bool):
        raise EvalConfigInvalidYAMLError(path_str, "'throttle_ms' must be an integer")
    if throttle_ms < 0:
        raise EvalConfigInvalidYAMLError(path_str, "'throttle_ms' must be >= 0")

    variants = _parse_variants(data.get("variants"), path_str)
    return AgentEvalConfig(
        name=name,
        agent=agent,
        models=_parse_models(data["models"], path_str),
        cases=cases,
        variants=variants if variants is not None else (BASE_VARIANT,),
        execute_tools=parse_names(data.get("execute_tools"), "execute_tools", path_str),
        ignore_tools=parse_names(data.get("ignore_tools"), "ignore_tools", path_str),
        stop_on_failure=stop_on_failure,
        throttle_ms=throttle_ms,
    )


def _parse_models(data: Any, path_str: str) -> tuple[Model, ...]:
    if not isinstance(data, list) or not data:
        raise EvalConfigInvalidYAMLError(path_str, "'models' must be a non-empty list")
    models: list[Model] = []
    for entry in data:
        if not isinstance(entry, str):
            raise EvalConfigInvalidYAMLError(
                path_str, "'models' entries must be strings"
            )
        # Accept both "provider:model" and the bare model value
        value = entry.split(":", 1)[1] if ":" in entry else entry
        for m in Model:
            if m.value == value:
                models.append(m)
                break
        else:
            raise EvalModelUnknownError(entry, path_str)
    return tuple(models)


def _parse_variants(data: Any, path_str: str) -> tuple[Variant, ...] | None:
    if data is None:
        return None
    if not isinstance(data, list) or not data:
        raise EvalConfigInvalidYAMLError(
            path_str, "'variants' must be a non-empty list"
        )
    variants: list[Variant] = []
    seen: set[str] = set()
    for idx, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            raise EvalConfigInvalidYAMLError(
                path_str, f"variant {idx} must be a mapping"
            )
        for key in entry:
            if key not in _VARIANT_ENTRY_KEYS:
                raise EvalConfigUnknownKeyError(f"variants[{idx}].{key}", path_str)
        for key in ("name", "prompt"):
            if key not in entry:
                raise EvalConfigMissingKeyError(f"variants[{idx}].{key}", path_str)
        variant_name = entry["name"]
        if not isinstance(variant_name, str) or not variant_name:
            raise EvalConfigInvalidYAMLError(
                path_str, f"variant {idx} name must be a non-empty string"
            )
        if variant_name in seen:
            raise EvalConfigInvalidYAMLError(
                path_str, f"duplicate variant name '{variant_name}'"
            )
        seen.add(variant_name)
        variants.append(load_variant(variant_name, entry["prompt"]))
    return tuple(variants)
