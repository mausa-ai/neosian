"""Exception hierarchy for neosian.

All exceptions are centralized here with their error messages.
Add exceptions as needed, not speculatively.
"""

from neosian._foundation.shared.constants import ErrorMessages


class NeosianError(Exception):
    """Base exception for all neosian errors."""

    pass


class LLMError(NeosianError):
    """Base exception for LLM-related errors."""

    pass


class ToolCallGenerationError(LLMError):
    """Raised when the LLM fails to generate a valid tool call after retries."""

    def __init__(self, retries: int) -> None:
        """Initialize with retry count.

        Args:
            retries: Number of retries attempted.
        """
        super().__init__(
            ErrorMessages.TOOL_CALL_GENERATION_FAILED.format(retries=retries)
        )
        self.retries = retries


class PromptLoadError(NeosianError):
    """Base exception for prompt loading errors."""

    pass


class PromptFileNotFoundError(PromptLoadError):
    """Raised when a prompt file is not found."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.PROMPT_FILE_NOT_FOUND.format(path=path))
        self.path = path


class PromptInvalidYAMLError(PromptLoadError):
    """Raised when a prompt file contains invalid YAML."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.PROMPT_INVALID_YAML.format(path=path))
        self.path = path


class PromptMissingKeyError(PromptLoadError):
    """Raised when a required key is missing from a prompt file."""

    def __init__(self, key: str, path: str) -> None:
        super().__init__(ErrorMessages.PROMPT_MISSING_KEY.format(key=key, path=path))
        self.key = key
        self.path = path


class AgentLoadError(NeosianError):
    """Base exception for agent loading errors."""

    pass


class AgentFileNotFoundError(AgentLoadError):
    """Raised when an agent file is not found."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.AGENT_FILE_NOT_FOUND.format(path=path))
        self.path = path


class AgentMissingConfigurationError(AgentLoadError):
    """Raised when an agent file is missing configuration."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.AGENT_MISSING_CONFIGURATION.format(path=path))
        self.path = path


class AgentInvalidConfigurationError(AgentLoadError):
    """Raised when configuration is not an AgentConfig instance."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.AGENT_INVALID_CONFIGURATION.format(path=path))
        self.path = path


class AgentInvalidDefinitionError(AgentLoadError):
    """Raised when an agent file has invalid definitions."""

    def __init__(self, path: str, error: str) -> None:
        super().__init__(ErrorMessages.AGENT_LOAD_ERROR.format(path=path, error=error))
        self.path = path
        self.error = error


class ConfigurationError(NeosianError):
    """Raised for configuration-related errors."""

    pass


class InvalidModelError(ConfigurationError):
    """Raised when an invalid model is provided to AgentConfig."""

    def __init__(self, message: str, model_value: object) -> None:
        """Initialize with error message and the invalid model value.

        Args:
            message: Pre-formatted error message.
            model_value: The invalid value that was provided instead of a Model enum.
        """
        super().__init__(message)
        self.model_value = model_value


class MissingAPIKeyError(ConfigurationError):
    """Raised when a required API key is missing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedParameterError(LLMError):
    """Raised when an unsupported parameter is passed to an LLM client."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GuardrailError(NeosianError):
    """Base exception for guardrail-related errors."""

    pass


class GuardrailClassifierParseError(GuardrailError):
    """Raised when classifier response cannot be parsed."""

    def __init__(self, response: str) -> None:
        super().__init__(
            ErrorMessages.GUARDRAIL_CLASSIFIER_PARSE_ERROR.format(response=response)
        )
        self.response = response


class GuardrailPolicyParseError(GuardrailError):
    """Raised when policy response cannot be parsed."""

    def __init__(self, response: str) -> None:
        super().__init__(
            ErrorMessages.GUARDRAIL_POLICY_PARSE_ERROR.format(response=response)
        )
        self.response = response


class GuardrailStreamingError(GuardrailError):
    """Raised when streaming is requested with output guardrails configured."""

    def __init__(self) -> None:
        super().__init__(ErrorMessages.GUARDRAIL_OUTPUT_REQUIRES_BLOCKING)


class StructuredOutputError(NeosianError):
    """Base exception for structured output errors."""

    pass


class StructuredOutputStreamingError(StructuredOutputError):
    """Raised when streaming is requested with structured outputs."""

    def __init__(self) -> None:
        super().__init__(ErrorMessages.STRUCTURED_OUTPUT_REQUIRES_BLOCKING)


class StructuredOutputToolsError(StructuredOutputError):
    """Raised when structured outputs are used with tool-enabled agents."""

    def __init__(self) -> None:
        super().__init__(ErrorMessages.STRUCTURED_OUTPUT_INCOMPATIBLE_WITH_TOOLS)


class ProviderError(LLMError):
    """Raised when a provider fails (rate limit, auth, network, etc.).

    This error triggers fallback to the next provider in the chain.
    """

    def __init__(self, provider: str, error: str) -> None:
        """Initialize with provider and error details.

        Args:
            provider: Provider identifier (e.g., "groq", "openai").
            error: Error description.
        """
        super().__init__(
            ErrorMessages.PROVIDER_FAILED.format(provider=provider, error=error)
        )
        self.provider = provider
        self.error = error


class ModelFailedError(LLMError):
    """Raised when the configured model fails.

    When has_fallback is False, this indicates no fallback was configured
    and the caller should handle the failure appropriately.
    """

    def __init__(self, model: str, error: str, *, has_fallback: bool) -> None:
        """Initialize with model and error details.

        Args:
            model: Model identifier that failed.
            error: Error description.
            has_fallback: True if a fallback is configured (failure is recoverable),
                False if no fallback exists (this is the final error).
        """
        if has_fallback:
            message = ErrorMessages.MODEL_FAILED.format(model=model, error=error)
        else:
            message = ErrorMessages.MODEL_FAILED_NO_FALLBACK.format(
                model=model, error=error
            )
        super().__init__(message)
        self.model = model
        self.error = error
        self.has_fallback = has_fallback


class FallbackExhaustedError(LLMError):
    """Raised when both main and fallback models have failed."""

    def __init__(
        self,
        main_model: str,
        main_error: str,
        fallback_model: str,
        fallback_error: str,
    ) -> None:
        """Initialize with details from both failed models.

        Args:
            main_model: The primary model that failed.
            main_error: Error from the primary model.
            fallback_model: The fallback model that also failed.
            fallback_error: Error from the fallback model.
        """
        super().__init__(
            ErrorMessages.FALLBACK_EXHAUSTED.format(
                main_model=main_model,
                main_error=main_error,
                fallback_model=fallback_model,
                fallback_error=fallback_error,
            )
        )
        self.main_model = main_model
        self.main_error = main_error
        self.fallback_model = fallback_model
        self.fallback_error = fallback_error


# Evaluation Errors
class EvalError(NeosianError):
    """Base exception for evaluation-related errors."""

    pass


class EvalConfigNotFoundError(EvalError):
    """Raised when an eval config file is not found."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.EVAL_CONFIG_NOT_FOUND.format(path=path))
        self.path = path


class EvalConfigInvalidYAMLError(EvalError):
    """Raised when an eval config file contains invalid YAML."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.EVAL_CONFIG_INVALID_YAML.format(path=path))
        self.path = path


class EvalConfigMissingKeyError(EvalError):
    """Raised when a required key is missing from eval config."""

    def __init__(self, key: str, path: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_CONFIG_MISSING_KEY.format(key=key, path=path)
        )
        self.key = key
        self.path = path


class EvalPromptNotFoundError(EvalError):
    """Raised when a prompt file referenced in eval config is not found."""

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.EVAL_PROMPT_NOT_FOUND.format(path=path))
        self.path = path


class EvalCaseInvalidError(EvalError):
    """Raised when an eval case definition is invalid."""

    def __init__(self, name: str, error: str) -> None:
        super().__init__(ErrorMessages.EVAL_CASE_INVALID.format(name=name, error=error))
        self.name = name
        self.error = error


class EvalRunError(EvalError):
    """Raised when an evaluation run fails."""

    def __init__(self, prompt: str, model: str, case: str, error: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_RUN_ERROR.format(
                prompt=prompt, model=model, case=case, error=error
            )
        )
        self.prompt = prompt
        self.model = model
        self.case = case
        self.error = error
