"""Prompt loading, agent definitions, configuration and structured output (DESIGN
§5)."""

from __future__ import annotations

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions.base import NeosianError


class PromptLoadError(NeosianError):
    """Base exception for prompt loading errors."""

    code = "prompt_load_failed"


class PromptFileNotFoundError(PromptLoadError):
    """Raised when a prompt file is not found."""

    code = "prompt_file_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.PROMPT_FILE_NOT_FOUND.format(path=path),
            details={"path": path},
        )
        self.path = path


class PromptInvalidYAMLError(PromptLoadError):
    """Raised when a prompt file contains invalid YAML."""

    code = "prompt_invalid_yaml"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.PROMPT_INVALID_YAML.format(path=path), details={"path": path}
        )
        self.path = path


class PromptMissingKeyError(PromptLoadError):
    """Raised when a required key is missing from a prompt file."""

    code = "prompt_missing_key"

    def __init__(self, key: str, path: str) -> None:
        super().__init__(
            ErrorMessages.PROMPT_MISSING_KEY.format(key=key, path=path),
            details={"key": key, "path": path},
        )
        self.key = key
        self.path = path


class AgentLoadError(NeosianError):
    """Base exception for agent loading errors."""

    code = "agent_load_failed"


class AgentFileNotFoundError(AgentLoadError):
    """Raised when an agent file is not found."""

    code = "agent_file_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.AGENT_FILE_NOT_FOUND.format(path=path), details={"path": path}
        )
        self.path = path


class AgentMissingConfigurationError(AgentLoadError):
    """Raised when an agent file is missing configuration."""

    code = "agent_missing_configuration"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.AGENT_MISSING_CONFIGURATION.format(path=path),
            details={"path": path},
        )
        self.path = path


class AgentInvalidConfigurationError(AgentLoadError):
    """Raised when configuration is not an AgentConfig instance."""

    code = "agent_invalid_configuration"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.AGENT_INVALID_CONFIGURATION.format(path=path),
            details={"path": path},
        )
        self.path = path


class AgentInvalidDefinitionError(AgentLoadError):
    """Raised when an agent file has invalid definitions."""

    code = "agent_invalid_definition"

    def __init__(self, path: str, error: str) -> None:
        super().__init__(
            ErrorMessages.AGENT_LOAD_ERROR.format(path=path, error=error),
            details={"path": path, "error": error},
        )
        self.path = path
        self.error = error


class ConfigurationError(NeosianError):
    """Raised for configuration-related errors."""

    code = "agent_configuration_error"


class InvalidModelError(ConfigurationError):
    """Raised when an invalid model is provided to AgentConfig."""

    code = "agent_invalid_model"

    def __init__(self, message: str, model_value: object) -> None:
        """Initialize with error message and the invalid model value.

        Args:
            message: Pre-formatted error message.
            model_value: The invalid value that was provided instead of a Model enum.
        """
        super().__init__(message, details={"model_value": repr(model_value)})
        self.model_value = model_value


class MissingAPIKeyError(ConfigurationError):
    """Raised when a required API key is missing."""

    code = "agent_missing_api_key"


class StructuredOutputError(NeosianError):
    """Base exception for structured output errors."""

    code = "agent_structured_output_error"


class StructuredOutputStreamingError(StructuredOutputError):
    """Raised when streaming is requested with structured outputs."""

    code = "agent_structured_output_requires_blocking"

    def __init__(self) -> None:
        super().__init__(ErrorMessages.STRUCTURED_OUTPUT_REQUIRES_BLOCKING)


class StructuredOutputToolsError(StructuredOutputError):
    """Raised when structured outputs are used with tool-enabled agents."""

    code = "agent_structured_output_incompatible_with_tools"

    def __init__(self) -> None:
        super().__init__(ErrorMessages.STRUCTURED_OUTPUT_INCOMPATIBLE_WITH_TOOLS)
