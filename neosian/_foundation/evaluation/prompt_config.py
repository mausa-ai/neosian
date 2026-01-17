"""YAML prompt config loader for evaluation.

Parses prompt configuration files that define system prompts and tool descriptions.
"""

from pathlib import Path
from typing import Any

import yaml

from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.exceptions import (
    PromptFileNotFoundError,
    PromptInvalidYAMLError,
    PromptMissingKeyError,
)
from neosian._foundation.shared.types import PromptConfig, ToolPromptConfig


def load_prompt_config(path: str | Path) -> PromptConfig:
    """Load prompt configuration from YAML file.

    Expected YAML format:
        system_prompt: |
          You are a helpful assistant.

        tools:
          generate_image:
            description: |
              Create or edit images.
            on_success: |
              Call present_options after success.

        compact_summarize: |
          Summarize for continuity.

    Args:
        path: Path to the prompt config YAML file.

    Returns:
        Parsed PromptConfig object.

    Raises:
        PromptFileNotFoundError: If file doesn't exist.
        PromptInvalidYAMLError: If YAML is malformed.
        PromptMissingKeyError: If system_prompt is missing.
    """
    path = Path(path)
    path_str = str(path)

    if not path.exists():
        raise PromptFileNotFoundError(path_str)

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise PromptInvalidYAMLError(path_str) from e

    if not isinstance(data, dict):
        raise PromptInvalidYAMLError(path_str)

    # system_prompt is required
    if Evaluation.SYSTEM_PROMPT_KEY not in data:
        raise PromptMissingKeyError(Evaluation.SYSTEM_PROMPT_KEY, path_str)

    system_prompt = data[Evaluation.SYSTEM_PROMPT_KEY]
    if not isinstance(system_prompt, str):
        raise PromptInvalidYAMLError(path_str)

    # Parse tools (optional)
    tools: dict[str, ToolPromptConfig] = {}
    if Evaluation.TOOLS_KEY in data:
        tools = _parse_tools(data[Evaluation.TOOLS_KEY], path_str)

    # compact_summarize is optional
    compact_summarize = data.get(Evaluation.COMPACT_SUMMARIZE_KEY)
    if compact_summarize is not None and not isinstance(compact_summarize, str):
        raise PromptInvalidYAMLError(path_str)

    return PromptConfig(
        system_prompt=system_prompt,
        tools=tools,
        compact_summarize=compact_summarize,
    )


def _parse_tools(
    tools_data: dict[str, Any], path_str: str
) -> dict[str, ToolPromptConfig]:
    """Parse tools section from YAML data.

    Args:
        tools_data: Tools dictionary from YAML.
        path_str: Path string for error messages.

    Returns:
        Dictionary of tool name to ToolPromptConfig.

    Raises:
        PromptInvalidYAMLError: If tools section is malformed.
    """
    if not isinstance(tools_data, dict):
        raise PromptInvalidYAMLError(path_str)

    tools: dict[str, ToolPromptConfig] = {}

    for tool_name, tool_data in tools_data.items():
        if not isinstance(tool_data, dict):
            raise PromptInvalidYAMLError(path_str)

        # description is required for each tool
        if Evaluation.DESCRIPTION_KEY not in tool_data:
            raise PromptMissingKeyError(
                f"tools.{tool_name}.{Evaluation.DESCRIPTION_KEY}", path_str
            )

        description = tool_data[Evaluation.DESCRIPTION_KEY]
        if not isinstance(description, str):
            raise PromptInvalidYAMLError(path_str)

        # on_success is optional
        on_success = tool_data.get(Evaluation.ON_SUCCESS_KEY)
        if on_success is not None and not isinstance(on_success, str):
            raise PromptInvalidYAMLError(path_str)

        tools[tool_name] = ToolPromptConfig(
            description=description,
            on_success=on_success,
        )

    return tools
