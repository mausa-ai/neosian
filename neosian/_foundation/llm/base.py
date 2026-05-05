"""Base protocol for LLM clients.

All LLM providers must implement this protocol.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ResponseFormat,
    ToolCallId,
    ToolName,
)


class Role(str, Enum):
    """Message role in conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A tool call requested by the model."""

    id: ToolCallId
    name: ToolName
    arguments: dict[str, Any]


@dataclass
class Message:
    """A message in the conversation."""

    role: Role
    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: ToolCallId | None = None


@dataclass
class ToolDefinition:
    """Definition of a tool that the model can call.

    `strict` opts into provider-enforced constrained decoding. Honored only by
    Anthropic today (counts against its per-request schema-complexity budget);
    other providers ignore the field. Default False = best-effort.
    """

    name: ToolName
    description: str
    parameters: dict[str, Any]
    strict: bool = False


@dataclass
class StreamChunk:
    """A chunk of streamed response."""

    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: "Usage | None" = None


@dataclass
class Usage:
    """Token usage information.

    Cache fields are populated by providers with prompt caching:
    - Anthropic: cache_creation_input_tokens + cache_read_input_tokens
    - OpenAI: cache_read_input_tokens only (automatic caching, no creation concept)
    - Cerebras: cache_read_input_tokens only (automatic caching, like OpenAI)
    - Groq: defaults to 0

    All providers normalize input_tokens to mean non-cached input tokens.
    total_tokens = input_tokens + output_tokens + cache_creation + cache_read.
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens used (includes cached tokens)."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass
class CompletionResponse:
    """Response from a completion request."""

    message: Message
    usage: Usage
    model: str  # Model string returned by API (may differ from input)


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients.

    All provider implementations must inherit from this class.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
    ) -> CompletionResponse:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-2.0). None uses provider default.
            response_format: Optional structured output configuration. When provided,
                the model will be constrained to generate valid JSON matching the
                schema defined in the ResponseFormat.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.

        Returns:
            CompletionResponse with the model's response.
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from the LLM.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-2.0). None uses provider default.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.

        Yields:
            StreamChunk objects as they arrive.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the underlying HTTP client and release resources.

        Should be called when the client is no longer needed to ensure
        proper cleanup of connection pools and other resources.
        """
        ...
