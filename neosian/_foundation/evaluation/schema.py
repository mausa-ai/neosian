"""Kind-neutral suite parsing primitives (DESIGN §13).

Every kind's loader shares the strict-key discipline, the `models:`
axis, tool-name lists, the paths a suite names (resolved beside the
suite, never against the working directory) and the values it carries
into the artifact (JSON's, or refused at load); the per-kind shapes stay
in their own loaders.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from neosian._foundation.shared.exceptions import (
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigUnknownKeyError,
    EvalModelUnknownError,
)
from neosian._foundation.shared.registry import lookup_model
from neosian._foundation.shared.types import AnyModel, ToolName


def check_keys(
    data: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: tuple[str, ...],
    path_str: str,
    hints: Mapping[str, str] | None = None,
) -> None:
    """Enforce strict keys: unknown keys raise (with a migration hint
    when one exists), then required keys must be present."""
    for key in data:
        if key not in allowed:
            raise EvalConfigUnknownKeyError(
                str(key), path_str, (hints or {}).get(str(key))
            )
    for key in required:
        if key not in data:
            raise EvalConfigMissingKeyError(key, path_str)


def parse_stop_on_failure(data: Mapping[str, Any], path_str: str) -> bool:
    value = data.get("stop_on_failure", True)
    if not isinstance(value, bool):
        raise EvalConfigInvalidYAMLError(path_str, "'stop_on_failure' must be a bool")
    return value


def parse_throttle_ms(data: Mapping[str, Any], path_str: str) -> int:
    value = data.get("throttle_ms", 500)
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvalConfigInvalidYAMLError(path_str, "'throttle_ms' must be an integer")
    if value < 0:
        raise EvalConfigInvalidYAMLError(path_str, "'throttle_ms' must be >= 0")
    return value


def parse_names(data: Any, key: str, path_str: str) -> frozenset[ToolName]:
    if data is None:
        return frozenset()
    if not isinstance(data, list) or not all(isinstance(n, str) for n in data):
        raise EvalConfigInvalidYAMLError(
            path_str, f"'{key}' must be a list of tool names"
        )
    return frozenset(ToolName(n) for n in data)


def parse_models(data: Any, path_str: str) -> tuple[AnyModel, ...]:
    if not isinstance(data, list) or not data:
        raise EvalConfigInvalidYAMLError(path_str, "'models' must be a non-empty list")
    models: list[AnyModel] = []
    for entry in data:
        if not isinstance(entry, str):
            raise EvalConfigInvalidYAMLError(
                path_str, "'models' entries must be strings"
            )
        # Accept both "provider:model" and the bare model value
        value = entry.split(":", 1)[1] if ":" in entry else entry
        model = lookup_model(value)
        if model is None:
            raise EvalModelUnknownError(entry, path_str)
        if model in models:  # a second column would mirror the first (EC-22)
            raise EvalConfigInvalidYAMLError(
                path_str, f"duplicate model '{entry}' on the 'models' axis"
            )
        models.append(model)
    return tuple(models)


def beside(suite: str, path: str) -> str:
    """A path the suite names, resolved against the suite file's directory
    (EC-9): the suite runs from any working directory."""
    return str(Path(suite).parent / path)


def json_value(value: Any) -> bool:
    """True when JSON holds `value` as it is. YAML also yields dates, sets
    and non-string keys, which the artifact could only store rewritten
    (EC-21), so the loaders refuse them."""
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and json_value(v) for k, v in value.items())
    return False
