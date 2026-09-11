"""LLM-layer errors: the base every provider-side failure carries usage on
(DESIGN §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions.base import NeosianError

if TYPE_CHECKING:
    from neosian._foundation.llm.base import ModelUsage, Usage


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
            ErrorMessages.TOOL_CALL_GENERATION_FAILED.format(retries=retries),
            details={"retries": retries},
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
        super().__init__(
            message,
            details={"context": context, "value_type": value_type, "field": field},
        )
        self.context = context
        self.field = field
        self.value_type = value_type
        self.error = error


class UnsupportedParameterError(LLMError):
    """Raised when an unsupported parameter is passed to an LLM client."""

    code = "llm_unsupported_parameter"


class UnsupportedContentError(LLMError):
    """Raised when multimodal content blocks reach a provider or model
    that cannot handle them.

    Content is never silently dropped: providers without a content-block
    converter (OpenAI, Cerebras) raise this on any block-list message,
    and the Anthropic client raises it when the target model's ModelSpec
    lacks the required capability.
    """

    code = "llm_unsupported_content"
