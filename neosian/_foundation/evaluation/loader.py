"""Eval suite loader — YAML schema v2 (DESIGN §13).

Strict keys at every level: an unknown key is an error, never silently
carried. Retired v1 keys (`prompts:`, `mock_response:`) fail with a
targeted migration hint. Models are validated here, so a typo fails the
suite instantly instead of surfacing inside every case result.
"""

from pathlib import Path
from typing import Any

import yaml

from neosian._foundation.evaluation.cases import parse_cases
from neosian._foundation.evaluation.memory_loader import parse_memory_suite
from neosian._foundation.evaluation.schema import (
    check_keys,
    parse_models,
    parse_names,
    parse_stop_on_failure,
    parse_throttle_ms,
)
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
)

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

    # The kind reads first — every other key is kind-shaped (§13.2).
    kind = data.get("kind", EvalKind.AGENT.value)
    if kind == EvalKind.MEMORY.value:
        return parse_memory_suite(data, path_str)
    if kind != EvalKind.AGENT.value:
        known = ", ".join(k.value for k in EvalKind)
        raise EvalConfigInvalidYAMLError(
            path_str, f"unknown kind '{kind}' — known kinds: {known}"
        )

    check_keys(
        data,
        allowed=_SUITE_KEYS,
        required=_REQUIRED_KEYS,
        path_str=path_str,
        hints=_V1_SUITE_HINTS,
    )
    name = data["name"]
    agent = data["agent"]
    if not isinstance(name, str) or not isinstance(agent, str):
        raise EvalConfigInvalidYAMLError(path_str, "'name' and 'agent' must be strings")

    variants = _parse_variants(data.get("variants"), path_str)
    return AgentEvalConfig(
        name=name,
        agent=agent,
        models=parse_models(data["models"], path_str),
        cases=parse_cases(data["cases"], path_str),
        variants=variants if variants is not None else (BASE_VARIANT,),
        execute_tools=parse_names(data.get("execute_tools"), "execute_tools", path_str),
        ignore_tools=parse_names(data.get("ignore_tools"), "ignore_tools", path_str),
        stop_on_failure=parse_stop_on_failure(data, path_str),
        throttle_ms=parse_throttle_ms(data, path_str),
    )


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
