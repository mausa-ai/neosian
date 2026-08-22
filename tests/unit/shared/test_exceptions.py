"""Tests for exceptions."""

import json
import re

import pytest

from neosian._foundation.llm.base import ModelUsage, Usage
from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
from neosian._foundation.shared.exceptions import (
    ERROR_CODES,
    ContextWindowExceededError,
    FallbackExhaustedError,
    GuardrailError,
    GuardrailPolicyParseError,
    GuardrailStreamingError,
    LLMError,
    MessageSerializationError,
    ModelFailedError,
    NeosianError,
    ProviderError,
    ToolCallGenerationError,
)


@pytest.mark.unit
class TestNeosianError:
    """Test base exception."""

    def test_can_raise_and_catch(self) -> None:
        """NeosianError should be raisable and catchable."""
        with pytest.raises(NeosianError):
            raise NeosianError("test error")

    def test_inherits_from_exception(self) -> None:
        """NeosianError should inherit from Exception."""
        assert issubclass(NeosianError, Exception)


@pytest.mark.unit
class TestLLMError:
    """Test LLM base exception."""

    def test_inherits_from_neosian_error(self) -> None:
        """LLMError should inherit from NeosianError."""
        assert issubclass(LLMError, NeosianError)

    def test_can_catch_as_neosian_error(self) -> None:
        """LLMError should be catchable as NeosianError."""
        with pytest.raises(NeosianError):
            raise LLMError("llm error")

    def test_usage_defaults(self) -> None:
        """Every LLMError answers usage (None) and usage_by_model (empty)."""
        error = LLMError("llm error")
        assert error.usage is None
        assert error.usage_by_model == ()

    def test_usage_round_trip(self) -> None:
        """usage and usage_by_model survive construction verbatim."""
        usage = Usage(input_tokens=10, output_tokens=5)
        split = (ModelUsage(model="m-1", usage=usage),)
        error = LLMError("llm error", usage=usage, usage_by_model=split)
        assert error.usage == usage
        assert error.usage_by_model == split

    def test_subclasses_default_usage(self) -> None:
        """Subclasses that never pass usage still answer the public fields."""
        error = ToolCallGenerationError(retries=3)
        assert error.usage is None
        assert error.usage_by_model == ()

    def test_terminal_errors_forward_usage(self) -> None:
        """The two terminal errors forward usage to the LLMError base."""
        usage = Usage(input_tokens=10, output_tokens=5)
        split = (ModelUsage(model="m-1", usage=usage),)
        failed = ModelFailedError(
            model="m-1",
            error="boom",
            has_fallback=False,
            usage=usage,
            usage_by_model=split,
        )
        assert failed.usage == usage
        assert failed.usage_by_model == split

        exhausted = FallbackExhaustedError(
            main_model="m-1",
            main_error="boom",
            fallback_model="m-2",
            fallback_error="also boom",
            usage=usage,
            usage_by_model=split,
        )
        assert exhausted.usage == usage
        assert exhausted.usage_by_model == split


@pytest.mark.unit
class TestToolCallGenerationError:
    """Test tool call generation error."""

    def test_inherits_from_llm_error(self) -> None:
        """ToolCallGenerationError should inherit from LLMError."""
        assert issubclass(ToolCallGenerationError, LLMError)

    def test_stores_retry_count(self) -> None:
        """Should store the retry count."""
        error = ToolCallGenerationError(retries=3)
        assert error.retries == 3

    def test_message_includes_retry_count(self) -> None:
        """Error message should include retry count."""
        error = ToolCallGenerationError(retries=2)
        assert "2" in str(error)
        assert "retries" in str(error).lower()

    def test_uses_constant_in_message(self) -> None:
        """Should use the centralized error message format."""
        error = ToolCallGenerationError(retries=LLMDefaults.MAX_TOOL_CALL_RETRIES)
        assert str(LLMDefaults.MAX_TOOL_CALL_RETRIES) in str(error)


@pytest.mark.unit
class TestMessageSerializationError:
    """Test message serialization error."""

    def test_inherits_from_llm_error(self) -> None:
        """MessageSerializationError should inherit from LLMError."""
        assert issubclass(MessageSerializationError, LLMError)

    def test_stores_context(self) -> None:
        """Should store the serialization context."""
        error = MessageSerializationError(
            context="tool_call.arguments",
            value_type="Decimal",
            error="Object of type Decimal is not JSON serializable",
        )
        assert error.context == "tool_call.arguments"

    def test_stores_field(self) -> None:
        """Should store the field path."""
        error = MessageSerializationError(
            context="tool_call.arguments",
            value_type="Decimal",
            error="test",
            field="order.amount",
        )
        assert error.field == "order.amount"

    def test_stores_value_type(self) -> None:
        """Should store the value type."""
        error = MessageSerializationError(
            context="test",
            value_type="datetime",
            error="test",
        )
        assert error.value_type == "datetime"

    def test_stores_original_error(self) -> None:
        """Should store the original error message."""
        error = MessageSerializationError(
            context="test",
            value_type="Decimal",
            error="Object of type Decimal is not JSON serializable",
        )
        assert error.error == "Object of type Decimal is not JSON serializable"

    def test_message_with_field_includes_field_path(self) -> None:
        """Error message should include field path when provided."""
        error = MessageSerializationError(
            context="tool_call.arguments",
            value_type="Decimal",
            error="test",
            field="amount",
        )
        message = str(error)
        assert "tool_call.arguments" in message
        assert "amount" in message
        assert "Decimal" in message

    def test_message_without_field_is_generic(self) -> None:
        """Error message should be generic when field not provided."""
        error = MessageSerializationError(
            context="sse_event.data",
            value_type="UUID",
            error="test",
        )
        message = str(error)
        assert "sse_event.data" in message
        assert "UUID" in message


@pytest.mark.unit
class TestGuardrailError:
    """Test guardrail base exception."""

    def test_inherits_from_neosian_error(self) -> None:
        """GuardrailError should inherit from NeosianError."""
        assert issubclass(GuardrailError, NeosianError)

    def test_can_catch_as_neosian_error(self) -> None:
        """GuardrailError should be catchable as NeosianError."""
        with pytest.raises(NeosianError):
            raise GuardrailError("guardrail error")


@pytest.mark.unit
class TestGuardrailPolicyParseError:
    """Test policy parse error."""

    def test_inherits_from_guardrail_error(self) -> None:
        """Should inherit from GuardrailError."""
        assert issubclass(GuardrailPolicyParseError, GuardrailError)

    def test_stores_response(self) -> None:
        """Should store the original response."""
        error = GuardrailPolicyParseError(response="invalid json")
        assert error.response == "invalid json"

    def test_message_includes_response(self) -> None:
        """Error message should include the response."""
        error = GuardrailPolicyParseError(response="not json")
        assert "not json" in str(error)


@pytest.mark.unit
class TestGuardrailStreamingError:
    """Test streaming with output guardrails error."""

    def test_inherits_from_guardrail_error(self) -> None:
        """Should inherit from GuardrailError."""
        assert issubclass(GuardrailStreamingError, GuardrailError)

    def test_message_is_correct(self) -> None:
        """Should use the centralized error message."""
        error = GuardrailStreamingError()
        assert str(error) == ErrorMessages.GUARDRAIL_OUTPUT_REQUIRES_BLOCKING

    def test_no_args_required(self) -> None:
        """Should not require any arguments."""
        error = GuardrailStreamingError()
        assert error is not None


# The full code table (DESIGN §5) — append-only: adding a row is the only
# permitted change; renaming or repurposing a code fails this pin.
_CODE_TABLE = {
    "NeosianError": "neosian_error",
    "LLMError": "llm_error",
    "ProviderError": "llm_provider_error",
    "ContextWindowExceededError": "llm_context_window_exceeded",
    "ModelFailedError": "llm_model_failed",
    "FallbackExhaustedError": "llm_fallback_exhausted",
    "ToolCallGenerationError": "llm_tool_call_generation_failed",
    "MessageSerializationError": "llm_message_serialization_failed",
    "UnsupportedParameterError": "llm_unsupported_parameter",
    "UnsupportedContentError": "llm_unsupported_content",
    "FakeScriptExhaustedError": "llm_fake_script_exhausted",
    "ConfigurationError": "agent_configuration_error",
    "InvalidModelError": "agent_invalid_model",
    "MissingAPIKeyError": "agent_missing_api_key",
    "AgentLoadError": "agent_load_failed",
    "AgentFileNotFoundError": "agent_file_not_found",
    "AgentMissingConfigurationError": "agent_missing_configuration",
    "AgentInvalidConfigurationError": "agent_invalid_configuration",
    "AgentInvalidDefinitionError": "agent_invalid_definition",
    "StructuredOutputError": "agent_structured_output_error",
    "StructuredOutputStreamingError": "agent_structured_output_requires_blocking",
    "StructuredOutputToolsError": "agent_structured_output_incompatible_with_tools",
    "GuardrailError": "guardrail_error",
    "GuardrailPolicyParseError": "guardrail_policy_parse_failed",
    "GuardrailStreamingError": "guardrail_output_requires_blocking",
    "PromptLoadError": "prompt_load_failed",
    "PromptFileNotFoundError": "prompt_file_not_found",
    "PromptInvalidYAMLError": "prompt_invalid_yaml",
    "PromptMissingKeyError": "prompt_missing_key",
    "PlaybookLoadError": "playbook_load_failed",
    "PlaybookFileNotFoundError": "playbook_file_not_found",
    "PlaybookInvalidFrontmatterError": "playbook_invalid_frontmatter",
    "PlaybookMissingKeyError": "playbook_missing_key",
    "PlaybookDuplicateNameError": "playbook_duplicate_name",
    "PlaybookDirectoryNotFoundError": "playbook_directory_not_found",
    "BlackboardError": "blackboard_error",
    "BlackboardEntryNotFoundError": "blackboard_entry_not_found",
    "BlackboardReadError": "blackboard_read_failed",
    "BlackboardUpdateError": "blackboard_update_failed",
    "FileBlackboardDirectoryNotFoundError": "blackboard_directory_not_found",
    "EvalError": "eval_error",
    "EvalConfigNotFoundError": "eval_config_not_found",
    "EvalConfigInvalidYAMLError": "eval_config_invalid_yaml",
    "EvalConfigMissingKeyError": "eval_config_missing_key",
    "EvalPromptNotFoundError": "eval_prompt_not_found",
    "EvalCaseInvalidError": "eval_case_invalid",
    "EvalRunError": "eval_run_failed",
    "EvalConfigUnknownKeyError": "eval_config_unknown_key",
    "EvalModelUnknownError": "eval_model_unknown",
    "MemoryStoreError": "memory_error",
    "MemoryDocumentNotFoundError": "memory_document_not_found",
    "MemoryScopeInvalidError": "memory_scope_invalid",
    "MemoryPathInvalidError": "memory_path_invalid",
    "MemoryConflictError": "memory_conflict",
    "MemoryFormatUnsupportedError": "memory_format_unsupported",
    "MemoryReadOnlyMountError": "memory_read_only_mount",
    "MemoryEditOnlyMountError": "memory_edit_only_mount",
    "ConversationStoreError": "agent_conversation_error",
    "ConversationIdInvalidError": "agent_conversation_id_invalid",
    "ConversationFormatUnsupportedError": "agent_conversation_format_unsupported",
}

_CODE_PATTERN = re.compile(
    r"^(neosian|agent|llm|tool|guardrail|memory|prompt|playbook|blackboard|eval)"
    r"_[a-z0-9_]+$"
)


@pytest.mark.unit
class TestErrorCodes:
    """The machine-code registry (DESIGN §5, ECOSYSTEM §6)."""

    def test_every_class_declares_its_own_code(self) -> None:
        """No exception may inherit its code silently — explicit or bust."""
        for cls in ERROR_CODES.values():
            assert "code" in cls.__dict__, cls.__name__

    def test_codes_match_family_prefix_pattern(self) -> None:
        for code in ERROR_CODES:
            assert _CODE_PATTERN.match(code), code

    def test_codes_are_unique(self) -> None:
        """One code per class: a collision would shrink the registry."""
        classes = set(ERROR_CODES.values())
        assert len(classes) == len(ERROR_CODES)

    def test_registry_matches_the_design_table(self) -> None:
        actual = {cls.__name__: code for code, cls in ERROR_CODES.items()}
        assert actual == _CODE_TABLE

    def test_only_tool_call_generation_is_retryable_by_default(self) -> None:
        retryable = {c for c, cls in ERROR_CODES.items() if cls.default_retryable}
        assert retryable == {"llm_tool_call_generation_failed"}

    def test_registry_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            ERROR_CODES["x"] = NeosianError  # type: ignore[index]


@pytest.mark.unit
class TestNeosianErrorContract:
    """The code/details/retryable surface on the base class."""

    def test_message_details_retryable(self) -> None:
        error = NeosianError("boom", details={"key": "value"}, retryable=True)
        assert error.message == "boom"
        assert error.details == {"key": "value"}
        assert error.retryable is True
        assert json.dumps(error.details)  # JSON-safe by contract

    def test_retryable_defaults_to_class_default(self) -> None:
        assert NeosianError("x").retryable is False
        assert ToolCallGenerationError(retries=2).retryable is True

    def test_instance_retryable_overrides_class_default(self) -> None:
        assert NeosianError("x", retryable=True).retryable is True

    def test_provider_error_carries_structure(self) -> None:
        error = ProviderError(
            "cerebras", "rate limited", status=429, retryable=True, request_id="req_9"
        )
        assert error.provider == "cerebras"
        assert error.status == 429
        assert error.retryable is True
        assert error.request_id == "req_9"
        assert "cerebras" in error.message

    def test_context_window_exceeded_fields(self) -> None:
        error = ContextWindowExceededError(
            "claude-haiku-4-5", context_window=200_000, provider="anthropic"
        )
        assert error.model == "claude-haiku-4-5"
        assert error.context_window == 200_000
        assert error.provider == "anthropic"
        assert error.retryable is False

    def test_model_failed_carries_cause_structure(self) -> None:
        error = ModelFailedError(
            "m",
            "boom",
            has_fallback=False,
            cause_code="llm_provider_error",
            provider_status=500,
        )
        assert error.cause_code == "llm_provider_error"
        assert error.provider_status == 500
