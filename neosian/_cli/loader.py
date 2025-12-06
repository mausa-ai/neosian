"""Agent loader for loading agent definitions from Python files.

Loads system_prompt and tools from a user-defined Python file.
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from neosian._foundation.shared.constants import AgentLoader
from neosian._foundation.shared.exceptions import (
    AgentFileNotFoundError,
    AgentInvalidDefinitionError,
    AgentMissingSystemPromptError,
    AgentMissingToolsError,
)
from neosian._foundation.shared.types import SystemPrompt
from neosian._foundation.tools.base import ToolFunction


@dataclass
class AgentDefinition:
    """Agent definition loaded from a Python file."""

    system_prompt: SystemPrompt
    tools: list[ToolFunction]
    name: str


def load_agent_definition(path: str | Path) -> AgentDefinition:
    """Load an agent definition from a Python file.

    The file must define:
        - system_prompt: str - The system prompt for the agent
        - tools: list - List of tool functions decorated with @Tool

    Args:
        path: Path to the Python file.

    Returns:
        AgentDefinition with the loaded configuration.

    Raises:
        AgentFileNotFoundError: If the file does not exist.
        AgentMissingSystemPromptError: If system_prompt is not defined.
        AgentMissingToolsError: If tools is not defined.
        AgentInvalidDefinitionError: If definitions are invalid types.
    """
    path = Path(path).resolve()
    path_str = str(path)

    if not path.exists():
        raise AgentFileNotFoundError(path_str)

    # Load the module
    module = _load_module_from_path(path)

    # Extract system_prompt
    if not hasattr(module, AgentLoader.SYSTEM_PROMPT_VAR):
        raise AgentMissingSystemPromptError(path_str)

    system_prompt = getattr(module, AgentLoader.SYSTEM_PROMPT_VAR)
    if not isinstance(system_prompt, str):
        raise AgentInvalidDefinitionError(path_str, "system_prompt must be a string")

    # Extract tools
    if not hasattr(module, AgentLoader.TOOLS_VAR):
        raise AgentMissingToolsError(path_str)

    tools = getattr(module, AgentLoader.TOOLS_VAR)
    if not isinstance(tools, list):
        raise AgentInvalidDefinitionError(path_str, "tools must be a list")

    # Derive agent name from filename
    name = path.stem

    return AgentDefinition(
        system_prompt=SystemPrompt(system_prompt),
        tools=tools,
        name=name,
    )


def _load_module_from_path(path: Path) -> ModuleType:
    """Load a Python module from a file path.

    Args:
        path: Path to the Python file.

    Returns:
        The loaded module.

    Raises:
        AgentInvalidDefinitionError: If the module fails to load.
    """
    path_str = str(path)

    try:
        spec = importlib.util.spec_from_file_location(AgentLoader.MODULE_NAME, path)
        if spec is None or spec.loader is None:
            raise AgentInvalidDefinitionError(path_str, "Failed to create module spec")

        module = importlib.util.module_from_spec(spec)

        # Add module to sys.modules so imports within it work
        sys.modules[AgentLoader.MODULE_NAME] = module

        spec.loader.exec_module(module)

        return module

    except SyntaxError as e:
        raise AgentInvalidDefinitionError(path_str, f"Syntax error: {e}") from e
    except Exception as e:
        if isinstance(e, AgentInvalidDefinitionError):
            raise
        raise AgentInvalidDefinitionError(path_str, str(e)) from e
    finally:
        # Clean up sys.modules
        sys.modules.pop(AgentLoader.MODULE_NAME, None)
