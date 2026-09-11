"""Shipped prompt data (ECOSYSTEM §8, DESIGN §7).

Every prompt neosian ships lives in assets/ YAML, loaded and validated
here at import — fail-fast, never prose in Python. Overriding is done at
the consumer seam: custom PolicyCategory lists replace the shipped policy
pack, and user-defined @Tool descriptions replace the builtin ones.
"""

from collections.abc import Mapping
from importlib import resources
from typing import Any, Final

import yaml

from neosian._foundation.shared.exceptions import (
    PromptInvalidYAMLError,
    PromptMissingKeyError,
)

_PACKAGE: Final = "neosian.assets"
_GUARDRAILS_FILE: Final = "prompts/guardrails.yaml"
_TOOLS_FILE: Final = "prompts/tools.yaml"
_MEMORY_FILE: Final = "prompts/memory.yaml"
_COMPACTION_FILE: Final = "prompts/compaction.yaml"
_REFLECTION_FILE: Final = "prompts/reflection.yaml"
_MAINTENANCE_FILE: Final = "prompts/maintenance.yaml"
_CONTEXT_FILE: Final = "prompts/context.yaml"
_CHAT_FILE: Final = "prompts/chat.yaml"
_POLICY_KEYS: Final = ("name", "code", "description", "violates", "safe")
_MEMORY_KEYS: Final = ("tool", "system_section")
_COMPACTION_KEYS: Final = ("distill", "epoch", "log_header", "log_footer")
_REFLECTION_KEYS: Final = ("system",)
_MAINTENANCE_KEYS: Final = ("system",)
_CHAT_KEYS: Final = ("system",)
_CONTEXT_KEYS: Final = (
    "board",
    "view_header",
    "view_footer",
    "view_fold",
    "view_empty",
    "start_index",
    "start_header",
    "start_session",
    "start_footer",
    "start_empty",
)
_TOOL_KEYS: Final = (
    "todo",
    "skill_list",
    "skill_load",
    "skill_guide",
    "recall_turn",
    "recall_turn_any",
    "docs",
    "json_object",
)
# Parameter prose by parameter name (NF §27.9): the builtins' `params=`.
_TOOL_PARAM_KEYS: Final = (
    "todo_params",
    "skill_load_params",
    "recall_turn_params",
    "recall_turn_any_params",
    "docs_params",
)
_MEMORY_PARAM_KEYS: Final = ("params",)


def render(template: str, **variables: str) -> str:
    """Interpolate {{var}} placeholders (the ECOSYSTEM §8 template syntax)."""
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def _load_yaml(filename: str) -> dict[str, Any]:
    text = resources.files(_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PromptInvalidYAMLError(filename) from exc
    if not isinstance(data, dict):
        raise PromptInvalidYAMLError(filename)
    return data


def _require(data: dict[str, Any], key: str, filename: str) -> Any:
    if key not in data:
        raise PromptMissingKeyError(key, filename)
    return data[key]


def _require_params(data: dict[str, Any], key: str, filename: str) -> dict[str, str]:
    value = _require(data, key, filename)
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise PromptInvalidYAMLError(filename)
    return dict(value)


def _load() -> (
    tuple[dict[str, str], dict[str, dict[str, str]], tuple[dict[str, Any], ...]]
):
    guardrails = _load_yaml(_GUARDRAILS_FILE)
    tools = _load_yaml(_TOOLS_FILE)
    params: dict[str, dict[str, str]] = {}
    for key in _TOOL_PARAM_KEYS:
        params[f"tools.{key}"] = _require_params(tools, key, _TOOLS_FILE)
    prompts = {
        "guardrails.classifier": str(
            _require(guardrails, "classifier", _GUARDRAILS_FILE)
        ),
        "guardrails.category": str(_require(guardrails, "category", _GUARDRAILS_FILE)),
    }
    for key in _TOOL_KEYS:
        prompts[f"tools.{key}"] = str(_require(tools, key, _TOOLS_FILE))
    memory = _load_yaml(_MEMORY_FILE)
    for key in _MEMORY_KEYS:
        prompts[f"memory.{key}"] = str(_require(memory, key, _MEMORY_FILE))
    for key in _MEMORY_PARAM_KEYS:
        params[f"memory.{key}"] = _require_params(memory, key, _MEMORY_FILE)
    compaction = _load_yaml(_COMPACTION_FILE)
    for key in _COMPACTION_KEYS:
        prompts[f"compaction.{key}"] = str(_require(compaction, key, _COMPACTION_FILE))
    reflection = _load_yaml(_REFLECTION_FILE)
    for key in _REFLECTION_KEYS:
        prompts[f"reflection.{key}"] = str(_require(reflection, key, _REFLECTION_FILE))
    maintenance = _load_yaml(_MAINTENANCE_FILE)
    for key in _MAINTENANCE_KEYS:
        prompts[f"maintenance.{key}"] = str(
            _require(maintenance, key, _MAINTENANCE_FILE)
        )
    context = _load_yaml(_CONTEXT_FILE)
    for key in _CONTEXT_KEYS:
        prompts[f"context.{key}"] = str(_require(context, key, _CONTEXT_FILE))
    chat = _load_yaml(_CHAT_FILE)
    for key in _CHAT_KEYS:
        prompts[f"chat.{key}"] = str(_require(chat, key, _CHAT_FILE))
    policies = tuple(_require(guardrails, "policies", _GUARDRAILS_FILE))
    for entry in policies:
        if not isinstance(entry, dict):
            raise PromptInvalidYAMLError(_GUARDRAILS_FILE)
        for key in _POLICY_KEYS:
            _require(entry, key, _GUARDRAILS_FILE)
    return prompts, params, policies


_PROMPTS, _PARAMS, POLICY_DATA = _load()


def get_prompt(key: str) -> str:
    """A shipped prompt by registry key (e.g. "guardrails.classifier")."""
    if key not in _PROMPTS:
        raise PromptMissingKeyError(key, "prompt registry")
    return _PROMPTS[key]


def get_prompt_params(key: str) -> Mapping[str, str]:
    """A builtin tool's parameter prose by registry key (e.g.
    "tools.todo_params"): `{parameter: description}` for `Tool(params=)`."""
    if key not in _PARAMS:
        raise PromptMissingKeyError(key, "prompt registry")
    return _PARAMS[key]
