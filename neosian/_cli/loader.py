"""Agent loader for loading agent definitions from Python files.

Loads AgentConfig from a user-defined Python file.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from neosian._foundation.shared.constants import AgentLoader
from neosian._foundation.shared.exceptions import (
    AgentFileNotFoundError,
    AgentInvalidConfigurationError,
    AgentInvalidDefinitionError,
    AgentMissingConfigurationError,
)
from neosian._foundation.shared.types import AgentConfig

# Project root markers in priority order
_PROJECT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "setup.py",
    ".git",
)


def _find_project_root(start: Path) -> Path:
    """Find the project root by walking up from start path.

    Looks for project markers (pyproject.toml, setup.py, .git) in parent
    directories. Falls back to the start path's parent if no markers found.

    Args:
        start: Path to start searching from (typically the agent file).

    Returns:
        The project root directory path.
    """
    for parent in [start.parent] + list(start.parent.parents):
        for marker in _PROJECT_MARKERS:
            if (parent / marker).exists():
                return parent
    # Fallback to agent file's parent directory
    return start.parent


def load_agent_config(path: str | Path) -> tuple[AgentConfig, str]:
    """Load an AgentConfig from a Python file.

    The file must export a `configuration` variable of type AgentConfig.

    Args:
        path: Path to the Python file.

    Returns:
        Tuple of (AgentConfig, agent_name derived from filename).

    Raises:
        AgentFileNotFoundError: If the file does not exist.
        AgentMissingConfigurationError: If configuration is not defined.
        AgentInvalidConfigurationError: If configuration is not AgentConfig.
        AgentInvalidDefinitionError: If the module fails to load.
    """
    path = Path(path).resolve()
    path_str = str(path)

    if not path.exists():
        raise AgentFileNotFoundError(path_str)

    # Load the module
    module = _load_module_from_path(path)

    # Extract configuration
    if not hasattr(module, AgentLoader.CONFIGURATION_VAR):
        raise AgentMissingConfigurationError(path_str)

    configuration = getattr(module, AgentLoader.CONFIGURATION_VAR)
    if not isinstance(configuration, AgentConfig):
        raise AgentInvalidConfigurationError(path_str)

    # Derive agent name from filename
    name = path.stem

    return configuration, name


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

    # Find project root and add to sys.path so project imports work
    project_root = str(_find_project_root(path))
    root_added = False
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        root_added = True

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
        if isinstance(e, (AgentInvalidDefinitionError, AgentMissingConfigurationError)):
            raise
        raise AgentInvalidDefinitionError(path_str, str(e)) from e
    finally:
        # Clean up sys.modules
        sys.modules.pop(AgentLoader.MODULE_NAME, None)
        # Remove project root from sys.path if we added it
        if root_added and project_root in sys.path:
            sys.path.remove(project_root)
