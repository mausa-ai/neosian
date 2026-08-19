"""Exception hierarchy for neosian.

All exceptions are centralized here with their error messages.
Add exceptions as needed, not speculatively.

Every exception carries a stable machine `code` under a closed family
prefix and a `retryable` flag (DESIGN §5, ECOSYSTEM §6). Codes are
append-only: deprecate, never repurpose or rename. The full registry is
exported as ERROR_CODES and printed by `python -m neosian.schemas errors`.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from neosian._foundation.shared.constants import ErrorMessages

if TYPE_CHECKING:
    from collections.abc import Mapping

    from neosian._foundation.llm.base import ModelUsage, Usage


class NeosianError(Exception):
    """Base exception for all neosian errors.

    Args:
        message: Developer-facing English; hosts own all user-facing text.
        details: JSON-safe structured context for logs and wire envelopes.
        retryable: Per-instance override of the class default.
    """

    code: ClassVar[str] = "neosian_error"
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.retryable = self.default_retryable if retryable is None else retryable


class LLMError(NeosianError):
    """Base exception for LLM-related errors.

    Every LLM failure can carry the best-effort usage billed before it:
    `usage` is the sum, `usage_by_model` the per-API-reported-model split
    in first-appearance order (DESIGN §3).
    """

    code = "llm_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
        usage: Usage | None = None,
        usage_by_model: tuple[ModelUsage, ...] = (),
    ) -> None:
        super().__init__(message, details=details, retryable=retryable)
        self.usage = usage
        self.usage_by_model = usage_by_model


class ToolCallGenerationError(LLMError):
    """Raised when the LLM fails to generate a valid tool call after retries."""

    code = "llm_tool_call_generation_failed"
    default_retryable = True

    def __init__(self, retries: int) -> None:
        """Initialize with retry count.

        Args:
            retries: Number of retries attempted.
        """
        super().__init__(
            ErrorMessages.TOOL_CALL_GENERATION_FAILED.format(retries=retries)
        )
        self.retries = retries


class MessageSerializationError(LLMError):
    """Raised when message data cannot be serialized to JSON.

    This typically occurs when tool call arguments or tool results contain
    non-JSON-serializable types like Decimal, datetime, UUID, etc.

    Common causes:
    - Loading conversation history from DynamoDB (uses Decimal for numbers)
    - Tool results containing database models with non-serializable fields
    - Custom objects in tool call arguments

    Fix by converting data to JSON-compatible types before passing to neosian.
    """

    code = "llm_message_serialization_failed"

    def __init__(
        self,
        context: str,
        value_type: str,
        error: str,
        *,
        field: str | None = None,
    ) -> None:
        """Initialize with serialization context.

        Args:
            context: Where serialization failed (e.g., "tool_call.arguments").
            value_type: The type that couldn't be serialized (e.g., "Decimal").
            error: Original error message from json.dumps.
            field: Specific field path that contains the problematic value.
        """
        if field:
            message = ErrorMessages.MESSAGE_SERIALIZATION_FIELD.format(
                context=context, field=field, value_type=value_type
            )
        else:
            message = ErrorMessages.MESSAGE_SERIALIZATION_GENERIC.format(
                context=context, value_type=value_type
            )
        super().__init__(message)
        self.context = context
        self.field = field
        self.value_type = value_type
        self.error = error


class PromptLoadError(NeosianError):
    """Base exception for prompt loading errors."""

    code = "prompt_load_failed"


class PromptFileNotFoundError(PromptLoadError):
    """Raised when a prompt file is not found."""

    code = "prompt_file_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.PROMPT_FILE_NOT_FOUND.format(path=path))
        self.path = path


class PromptInvalidYAMLError(PromptLoadError):
    """Raised when a prompt file contains invalid YAML."""

    code = "prompt_invalid_yaml"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.PROMPT_INVALID_YAML.format(path=path))
        self.path = path


class PromptMissingKeyError(PromptLoadError):
    """Raised when a required key is missing from a prompt file."""

    code = "prompt_missing_key"

    def __init__(self, key: str, path: str) -> None:
        super().__init__(ErrorMessages.PROMPT_MISSING_KEY.format(key=key, path=path))
        self.key = key
        self.path = path


class AgentLoadError(NeosianError):
    """Base exception for agent loading errors."""

    code = "agent_load_failed"


class AgentFileNotFoundError(AgentLoadError):
    """Raised when an agent file is not found."""

    code = "agent_file_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.AGENT_FILE_NOT_FOUND.format(path=path))
        self.path = path


class AgentMissingConfigurationError(AgentLoadError):
    """Raised when an agent file is missing configuration."""

    code = "agent_missing_configuration"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.AGENT_MISSING_CONFIGURATION.format(path=path))
        self.path = path


class AgentInvalidConfigurationError(AgentLoadError):
    """Raised when configuration is not an AgentConfig instance."""

    code = "agent_invalid_configuration"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.AGENT_INVALID_CONFIGURATION.format(path=path))
        self.path = path


class AgentInvalidDefinitionError(AgentLoadError):
    """Raised when an agent file has invalid definitions."""

    code = "agent_invalid_definition"

    def __init__(self, path: str, error: str) -> None:
        super().__init__(ErrorMessages.AGENT_LOAD_ERROR.format(path=path, error=error))
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
        super().__init__(message)
        self.model_value = model_value


class MissingAPIKeyError(ConfigurationError):
    """Raised when a required API key is missing."""

    code = "agent_missing_api_key"


class UnsupportedParameterError(LLMError):
    """Raised when an unsupported parameter is passed to an LLM client."""

    code = "llm_unsupported_parameter"


class UnsupportedContentError(LLMError):
    """Raised when multimodal content blocks reach a provider or model
    that cannot handle them.

    Content is never silently dropped: providers without a content-block
    converter (OpenAI, Groq, Cerebras) raise this on any block-list message,
    and the Anthropic client raises it when the target model's ModelSpec
    lacks the required capability.
    """

    code = "llm_unsupported_content"


class GuardrailError(NeosianError):
    """Base exception for guardrail-related errors."""

    code = "guardrail_error"


class GuardrailPolicyParseError(GuardrailError):
    """Raised when policy response cannot be parsed."""

    code = "guardrail_policy_parse_failed"

    def __init__(self, response: str) -> None:
        super().__init__(
            ErrorMessages.GUARDRAIL_POLICY_PARSE_ERROR.format(response=response)
        )
        self.response = response


class GuardrailStreamingError(GuardrailError):
    """Raised when streaming is requested with output guardrails configured."""

    code = "guardrail_output_requires_blocking"

    def __init__(self) -> None:
        super().__init__(ErrorMessages.GUARDRAIL_OUTPUT_REQUIRES_BLOCKING)


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


class ProviderError(LLMError):
    """Raised when a provider fails (rate limit, auth, network, etc.).

    Produced by wrap_provider_error at every client boundary; provider
    identity and HTTP status are preserved structurally, never stringified
    away (ECOSYSTEM §6). This error triggers fallback to the next provider
    in the chain.
    """

    code = "llm_provider_error"

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        """Initialize with provider and error details.

        Args:
            provider: Provider identifier (e.g., "groq", "openai").
            message: Error description from the provider SDK.
            status: HTTP status code, when the failure carried one.
            retryable: Whether the failure class is worth retrying
                (429/408/5xx/connection/timeout).
            request_id: Provider request id, when the SDK exposed one.
        """
        super().__init__(
            ErrorMessages.PROVIDER_FAILED.format(provider=provider, error=message),
            retryable=retryable,
        )
        self.provider = provider
        self.status = status
        self.request_id = request_id


class ContextWindowExceededError(LLMError):
    """Raised when a request cannot fit the model's context window.

    Reactive producer: wrap_provider_error classifying a provider 400.
    Proactive producer: ContextPolicy (N0). An overflow does not trigger
    fallback unless the fallback model's window exceeds the primary's —
    falling back smaller is a guaranteed second failure and a doubled bill.
    """

    code = "llm_context_window_exceeded"

    def __init__(
        self,
        model: str,
        *,
        context_window: int | None = None,
        estimated_tokens: int | None = None,
        provider: str | None = None,
    ) -> None:
        message = f"Context window exceeded for model {model}"
        if context_window is not None:
            message += f" (window: {context_window} tokens)"
        super().__init__(message)
        self.model = model
        self.context_window = context_window
        self.estimated_tokens = estimated_tokens
        self.provider = provider


class FakeScriptExhaustedError(LLMError):
    """Raised when a FakeClient's script runs out of turns (neosian.fake).

    Lives here, not in llm/fake.py, so the ERROR_CODES subclass walk is
    import-order-deterministic.
    """

    code = "llm_fake_script_exhausted"

    def __init__(self, consumed: int) -> None:
        super().__init__(f"FakeScript exhausted after {consumed} turn(s)")
        self.consumed = consumed


class ModelFailedError(LLMError):
    """Raised when the configured model fails.

    When has_fallback is False, this indicates no fallback was configured
    and the caller should handle the failure appropriately.
    """

    code = "llm_model_failed"

    def __init__(
        self,
        model: str,
        error: str,
        *,
        has_fallback: bool,
        usage: Usage | None = None,
        usage_by_model: tuple[ModelUsage, ...] = (),
        cause_code: str | None = None,
        provider_status: int | None = None,
    ) -> None:
        """Initialize with model and error details.

        Args:
            model: Model identifier that failed.
            error: Error description.
            has_fallback: True if a fallback is configured (failure is recoverable),
                False if no fallback exists (this is the final error).
            usage: Best-effort token usage billed before the failure.
            usage_by_model: Per-API-reported-model split of `usage`.
            cause_code: Machine code of the underlying failure, so terminal
                frames carry structure, not formatted English.
            provider_status: HTTP status of the underlying failure, if any.
        """
        if has_fallback:
            message = ErrorMessages.MODEL_FAILED.format(model=model, error=error)
        else:
            message = ErrorMessages.MODEL_FAILED_NO_FALLBACK.format(
                model=model, error=error
            )
        super().__init__(message, usage=usage, usage_by_model=usage_by_model)
        self.model = model
        self.error = error
        self.has_fallback = has_fallback
        self.cause_code = cause_code
        self.provider_status = provider_status


class FallbackExhaustedError(LLMError):
    """Raised when both main and fallback models have failed."""

    code = "llm_fallback_exhausted"

    def __init__(
        self,
        main_model: str,
        main_error: str,
        fallback_model: str,
        fallback_error: str,
        *,
        usage: Usage | None = None,
        usage_by_model: tuple[ModelUsage, ...] = (),
        cause_code: str | None = None,
        provider_status: int | None = None,
    ) -> None:
        """Initialize with details from both failed models.

        Args:
            main_model: The primary model that failed.
            main_error: Error from the primary model.
            fallback_model: The fallback model that also failed.
            fallback_error: Error from the fallback model.
            usage: Best-effort combined token usage billed across both
                failed attempts.
            usage_by_model: Per-API-reported-model split of `usage`.
            cause_code: Machine code of the final underlying failure, so
                terminal frames carry structure, not formatted English.
            provider_status: HTTP status of the final failure, if any.
        """
        super().__init__(
            ErrorMessages.FALLBACK_EXHAUSTED.format(
                main_model=main_model,
                main_error=main_error,
                fallback_model=fallback_model,
                fallback_error=fallback_error,
            ),
            usage=usage,
            usage_by_model=usage_by_model,
        )
        self.main_model = main_model
        self.main_error = main_error
        self.fallback_model = fallback_model
        self.fallback_error = fallback_error
        self.cause_code = cause_code
        self.provider_status = provider_status


# Evaluation Errors
class EvalError(NeosianError):
    """Base exception for evaluation-related errors."""

    code = "eval_error"


class EvalConfigNotFoundError(EvalError):
    """Raised when an eval config file is not found."""

    code = "eval_config_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.EVAL_CONFIG_NOT_FOUND.format(path=path))
        self.path = path


class EvalConfigInvalidYAMLError(EvalError):
    """Raised when an eval config file contains invalid YAML."""

    code = "eval_config_invalid_yaml"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.EVAL_CONFIG_INVALID_YAML.format(path=path))
        self.path = path


class EvalConfigMissingKeyError(EvalError):
    """Raised when a required key is missing from eval config."""

    code = "eval_config_missing_key"

    def __init__(self, key: str, path: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_CONFIG_MISSING_KEY.format(key=key, path=path)
        )
        self.key = key
        self.path = path


class EvalPromptNotFoundError(EvalError):
    """Raised when a prompt file referenced in eval config is not found."""

    code = "eval_prompt_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.EVAL_PROMPT_NOT_FOUND.format(path=path))
        self.path = path


class EvalCaseInvalidError(EvalError):
    """Raised when an eval case definition is invalid."""

    code = "eval_case_invalid"

    def __init__(self, name: str, error: str) -> None:
        super().__init__(ErrorMessages.EVAL_CASE_INVALID.format(name=name, error=error))
        self.name = name
        self.error = error


class EvalRunError(EvalError):
    """Raised when an evaluation run fails."""

    code = "eval_run_failed"

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


# Playbook Errors
class PlaybookLoadError(NeosianError):
    """Base exception for playbook loading errors."""

    code = "playbook_load_failed"


class PlaybookFileNotFoundError(PlaybookLoadError):
    """Raised when a playbook file is not found."""

    code = "playbook_file_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.PLAYBOOK_FILE_NOT_FOUND.format(path=path))
        self.path = path


class PlaybookInvalidFrontmatterError(PlaybookLoadError):
    """Raised when a playbook file has invalid or missing frontmatter."""

    code = "playbook_invalid_frontmatter"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.PLAYBOOK_INVALID_FRONTMATTER.format(path=path))
        self.path = path


class PlaybookMissingKeyError(PlaybookLoadError):
    """Raised when a required key is missing from playbook frontmatter."""

    code = "playbook_missing_key"

    def __init__(self, key: str, path: str) -> None:
        super().__init__(ErrorMessages.PLAYBOOK_MISSING_KEY.format(key=key, path=path))
        self.key = key
        self.path = path


class PlaybookDuplicateNameError(PlaybookLoadError):
    """Raised when multiple playbooks share the same name."""

    code = "playbook_duplicate_name"

    def __init__(self, name: str) -> None:
        super().__init__(ErrorMessages.PLAYBOOK_DUPLICATE_NAME.format(name=name))
        self.name = name


class PlaybookDirectoryNotFoundError(PlaybookLoadError):
    """Raised when the playbook directory does not exist."""

    code = "playbook_directory_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(ErrorMessages.PLAYBOOK_DIRECTORY_NOT_FOUND.format(path=path))
        self.path = path


# Blackboard Errors
class BlackboardError(NeosianError):
    """Base exception for blackboard-related errors."""

    code = "blackboard_error"


class BlackboardEntryNotFoundError(BlackboardError):
    """Raised when a blackboard entry does not exist."""

    code = "blackboard_entry_not_found"

    def __init__(self, name: str) -> None:
        super().__init__(ErrorMessages.BLACKBOARD_ENTRY_NOT_FOUND.format(name=name))
        self.name = name


class BlackboardReadError(BlackboardError):
    """Raised when reading a blackboard entry fails."""

    code = "blackboard_read_failed"

    def __init__(self, name: str, error: str) -> None:
        super().__init__(
            ErrorMessages.BLACKBOARD_READ_ERROR.format(name=name, error=error)
        )
        self.name = name
        self.error = error


class BlackboardUpdateError(BlackboardError):
    """Raised when updating a blackboard entry fails."""

    code = "blackboard_update_failed"

    def __init__(self, name: str, error: str) -> None:
        super().__init__(
            ErrorMessages.BLACKBOARD_UPDATE_ERROR.format(name=name, error=error)
        )
        self.name = name
        self.error = error


class FileBlackboardDirectoryNotFoundError(BlackboardError):
    """Raised when the FileBlackboard directory does not exist."""

    code = "blackboard_directory_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.FILE_BLACKBOARD_DIRECTORY_NOT_FOUND.format(path=path)
        )
        self.path = path


# Memory Errors (DESIGN §5 table, §8). Base is MemoryStoreError — never
# MemoryError, which shadows a Python builtin in __all__. Messages are
# inline f-strings: constants.py is named debt and never grows.
class MemoryStoreError(NeosianError):
    """Base exception for memory-store errors."""

    code = "memory_error"


class MemoryDocumentNotFoundError(MemoryStoreError):
    """Raised when an operation requires a document that does not exist."""

    code = "memory_document_not_found"

    def __init__(self, scope: str, path: str) -> None:
        super().__init__(
            f"Memory document not found: {path!r} in scope {scope!r}",
            details={"scope": scope, "path": path},
        )
        self.scope = scope
        self.path = path


class MemoryScopeInvalidError(MemoryStoreError):
    """Raised when a scope string violates the grammar (ECOSYSTEM §2)."""

    code = "memory_scope_invalid"

    def __init__(self, scope: str, reason: str) -> None:
        super().__init__(
            f"Invalid memory scope {scope!r}: {reason}",
            details={"scope": scope, "reason": reason},
        )
        self.scope = scope
        self.reason = reason


class MemoryPathInvalidError(MemoryStoreError):
    """Raised when a document path violates the grammar (DESIGN §8)."""

    code = "memory_path_invalid"

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"Invalid memory document path {path!r}: {reason}",
            details={"path": path, "reason": reason},
        )
        self.path = path
        self.reason = reason


class MemoryConflictError(MemoryStoreError):
    """Raised when a write loses a version race or a rename target is taken.

    `reason` is machine-checkable: "version_mismatch", "document_absent"
    (an `expected_version` on a document that does not exist), or
    "destination_exists" (rename onto an occupied path, src == dst included).
    """

    code = "memory_conflict"

    def __init__(
        self,
        scope: str,
        path: str,
        reason: str,
        *,
        expected_version: int | None = None,
        actual_version: int | None = None,
    ) -> None:
        super().__init__(
            f"Memory conflict on {path!r} in scope {scope!r}: {reason}",
            details={
                "scope": scope,
                "path": path,
                "reason": reason,
                "expected_version": expected_version,
                "actual_version": actual_version,
            },
        )
        self.scope = scope
        self.path = path
        self.reason = reason
        self.expected_version = expected_version
        self.actual_version = actual_version


class MemoryFormatUnsupportedError(MemoryStoreError):
    """Raised when stored data declares a newer format than this library reads.

    Refusal, never coercion: a newer `neosian_format` (or a missing/broken
    storage envelope, or a naive timestamp) is rejected at the boundary.
    """

    code = "memory_format_unsupported"

    def __init__(self, scope: str, path: str, reason: str) -> None:
        super().__init__(
            f"Unsupported memory format for {path!r} in scope {scope!r}: {reason}",
            details={"scope": scope, "path": path, "reason": reason},
        )
        self.scope = scope
        self.path = path
        self.reason = reason


class MemoryReadOnlyMountError(MemoryStoreError):
    """Raised by the tool layer when a write targets a read-only mount.

    The store never raises this (C7: mounts are tool-layer policy); it lives
    here so the DESIGN §5 code table ships whole. Wired in N1 slice B.
    """

    code = "memory_read_only_mount"

    def __init__(self, mount_path: str) -> None:
        super().__init__(
            f"Memory mount {mount_path!r} is read-only",
            details={"mount_path": mount_path},
        )
        self.mount_path = mount_path


# Conversation Errors (DESIGN §5 table, §9). Codes live under agent_ — the
# family-prefix set is closed and frozen (ECOSYSTEM §6; ledger #24).
class ConversationStoreError(NeosianError):
    """Base exception for conversation-store errors."""

    code = "agent_conversation_error"


class ConversationIdInvalidError(ConversationStoreError):
    """Raised when a conversation id violates the grammar (DESIGN §9.4)."""

    code = "agent_conversation_id_invalid"

    def __init__(self, conversation_id: str, reason: str) -> None:
        super().__init__(
            f"Invalid conversation id {conversation_id!r}: {reason}",
            details={"conversation_id": conversation_id, "reason": reason},
        )
        self.conversation_id = conversation_id
        self.reason = reason


class ConversationFormatUnsupportedError(ConversationStoreError):
    """Raised when a stored turn declares a newer format than this library reads.

    Refusal, never coercion: a newer `neosian_format`, a malformed row, or a
    naive timestamp is rejected at the boundary (DESIGN §9.2 CS6, §9.8).
    """

    code = "agent_conversation_format_unsupported"

    def __init__(self, conversation_id: str, reason: str) -> None:
        super().__init__(
            f"Unsupported turn format in conversation {conversation_id!r}: {reason}",
            details={"conversation_id": conversation_id, "reason": reason},
        )
        self.conversation_id = conversation_id
        self.reason = reason


def _collect_error_codes() -> dict[str, type[NeosianError]]:
    registry: dict[str, type[NeosianError]] = {NeosianError.code: NeosianError}
    stack: list[type[NeosianError]] = [NeosianError]
    while stack:
        for subclass in stack.pop().__subclasses__():
            registry[subclass.code] = subclass
            stack.append(subclass)
    return registry


# The append-only machine-code registry (ECOSYSTEM §6). Hosts key i18n and
# alerting on these; `python -m neosian.schemas errors` prints it as JSON.
ERROR_CODES: Mapping[str, type[NeosianError]] = MappingProxyType(_collect_error_codes())
