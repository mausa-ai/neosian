"""Base protocol for LLM clients.

All LLM providers must implement this protocol.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from neosian._foundation.shared.types import ModelId, ToolCallId, ToolName


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
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: ToolCallId | None = None


@dataclass
class ToolDefinition:
    """Definition of a tool that the model can call."""

    name: ToolName
    description: str
    parameters: dict[str, Any]


@dataclass
class StreamChunk:
    """A chunk of streamed response."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass
class Usage:
    """Token usage information."""

    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens


@dataclass
class CompletionResponse:
    """Response from a completion request."""

    message: Message
    usage: Usage
    model: ModelId


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients.

    All provider implementations must inherit from this class.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model: ModelId,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
    ) -> CompletionResponse:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-2.0). None uses provider default.

        Returns:
            CompletionResponse with the model's response.
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        model: ModelId,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from the LLM.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-2.0). None uses provider default.

        Yields:
            StreamChunk objects as they arrive.
        """
        ...
