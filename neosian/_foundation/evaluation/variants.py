"""Variant prompt files (DESIGN §13).

A variant is one prompting strategy — a system prompt plus optional
tool-description overrides — applied to the agent under test. Files are
strict-keyed like the suite itself; the v1 `on_success` and
`compact_summarize` keys (parsed by nothing) are rejected with a hint.
"""

from pathlib import Path
from typing import Any

import yaml

from neosian._foundation.evaluation.types import Variant
from neosian._foundation.shared.exceptions import (
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigUnknownKeyError,
    EvalPromptNotFoundError,
)
from neosian._foundation.shared.types import SystemPrompt, ToolName

_VARIANT_KEYS = frozenset({"system_prompt", "tools"})
_TOOL_KEYS = frozenset({"description"})
_DROPPED_HINT = "schema v2 dropped it — the key was never read"


def load_variant(name: str, path: str | Path) -> Variant:
    """Load one variant prompt file.

    Raises:
        EvalPromptNotFoundError: If the file doesn't exist.
        EvalConfigInvalidYAMLError: If the YAML is unparseable or malformed.
        EvalConfigMissingKeyError: If `system_prompt` is absent.
        EvalConfigUnknownKeyError: If the file carries unknown keys.
    """
    path = Path(path)
    path_str = str(path)
    if not path.exists():
        raise EvalPromptNotFoundError(path_str)

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise EvalConfigInvalidYAMLError(path_str) from e
    if not isinstance(data, dict):
        raise EvalConfigInvalidYAMLError(path_str, "root must be a mapping")

    for key in data:
        if key not in _VARIANT_KEYS:
            hint = _DROPPED_HINT if key in ("on_success", "compact_summarize") else None
            raise EvalConfigUnknownKeyError(str(key), path_str, hint)

    if "system_prompt" not in data:
        raise EvalConfigMissingKeyError("system_prompt", path_str)
    system_prompt = data["system_prompt"]
    if not isinstance(system_prompt, str):
        raise EvalConfigInvalidYAMLError(path_str, "'system_prompt' must be a string")

    return Variant(
        name=name,
        system_prompt=SystemPrompt(system_prompt),
        tool_descriptions=_parse_tools(data.get("tools"), path_str),
        source=path_str,
    )


def _parse_tools(data: Any, path_str: str) -> dict[ToolName, str]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise EvalConfigInvalidYAMLError(path_str, "'tools' must be a mapping")
    descriptions: dict[ToolName, str] = {}
    for tool_name, tool_data in data.items():
        if not isinstance(tool_data, dict) or "description" not in tool_data:
            raise EvalConfigInvalidYAMLError(
                path_str, f"tool '{tool_name}' must be a mapping with a 'description'"
            )
        for key in tool_data:
            if key not in _TOOL_KEYS:
                hint = _DROPPED_HINT if key == "on_success" else None
                raise EvalConfigUnknownKeyError(
                    f"tools.{tool_name}.{key}", path_str, hint
                )
        description = tool_data["description"]
        if not isinstance(description, str):
            raise EvalConfigInvalidYAMLError(
                path_str, f"tool '{tool_name}' description must be a string"
            )
        descriptions[ToolName(str(tool_name))] = description
    return descriptions
