"""YAML prompt loading utility.

Provides a simple way to load system prompts from YAML files.
"""

from pathlib import Path

import yaml

from neosian._foundation.shared.constants import PromptLoader
from neosian._foundation.shared.exceptions import (
    PromptFileNotFoundError,
    PromptInvalidYAMLError,
    PromptMissingKeyError,
)
from neosian._foundation.shared.types import SystemPrompt


def load_prompt(path: str | Path) -> SystemPrompt:
    """Load a system prompt from a YAML file.

    The YAML file must contain a 'system_prompt' key with a string value.

    Example YAML file:
        system_prompt: |
          You are a helpful assistant.
          Be concise and accurate.

    Args:
        path: Path to the YAML file.

    Returns:
        The system prompt as a SystemPrompt type.

    Raises:
        PromptFileNotFoundError: If the file does not exist.
        PromptInvalidYAMLError: If the file contains invalid YAML.
        PromptMissingKeyError: If 'system_prompt' key is missing.
    """
    path = Path(path)
    path_str = str(path)

    if not path.exists():
        raise PromptFileNotFoundError(path_str)

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise PromptInvalidYAMLError(path_str) from e

    if not isinstance(data, dict):
        raise PromptInvalidYAMLError(path_str)

    if PromptLoader.SYSTEM_PROMPT_KEY not in data:
        raise PromptMissingKeyError(PromptLoader.SYSTEM_PROMPT_KEY, path_str)

    prompt = data[PromptLoader.SYSTEM_PROMPT_KEY]

    if not isinstance(prompt, str):
        raise PromptInvalidYAMLError(path_str)

    return SystemPrompt(prompt)
