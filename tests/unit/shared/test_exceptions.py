"""Tests for exceptions."""

import pytest

from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
from neosian._foundation.shared.exceptions import (
    GuardrailClassifierParseError,
    GuardrailError,
    GuardrailPolicyParseError,
    GuardrailStreamingError,
    LLMError,
    MessageSerializationError,
    NeosianError,
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
class TestGuardrailClassifierParseError:
    """Test classifier parse error."""

    def test_inherits_from_guardrail_error(self) -> None:
        """Should inherit from GuardrailError."""
        assert issubclass(GuardrailClassifierParseError, GuardrailError)

    def test_stores_response(self) -> None:
        """Should store the original response."""
        error = GuardrailClassifierParseError(response="bad response")
        assert error.response == "bad response"

    def test_message_includes_response(self) -> None:
        """Error message should include the response."""
        error = GuardrailClassifierParseError(response="unexpected")
        assert "unexpected" in str(error)


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
