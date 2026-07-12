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
class TextBlock:
    """A text segment in multimodal message content."""

    text: str


@dataclass
class ImageBlock:
    """An image input for vision-capable models.

    Exactly one of `data` (base64) or `url` must be set. `media_type`
    (e.g. "image/png", "image/jpeg", "image/webp", "image/gif") is required
    with base64 data and unused for url sources.

    Anthropic recommends placing media blocks before text blocks in a
    message for best results; neosian preserves caller order.
    """

    media_type: str | None = None
    data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        _validate_media_source(
            type(self).__name__, self.media_type, self.data, self.url
        )
        if self.data is not None:
            # Anthropic rejects base64 containing newlines/whitespace.
            self.data = "".join(self.data.split())


@dataclass
class DocumentBlock:
    """A document input (e.g. PDF) for document-capable models.

    Exactly one of `data` (base64) or `url` must be set. `media_type`
    (e.g. "application/pdf") is required with base64 data and unused for
    url sources.

    Note: base64 inflates bytes by ~33% against Anthropic's 32 MB request
    cap (roughly a 24 MB raw-PDF ceiling; 100-page limit on 200K-context
    models).
    """

    media_type: str | None = None
    data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        _validate_media_source(
            type(self).__name__, self.media_type, self.data, self.url
        )
        if self.data is not None:
            self.data = "".join(self.data.split())


def _validate_media_source(
    block_name: str, media_type: str | None, data: str | None, url: str | None
) -> None:
    """Validate the data/url/media_type invariants shared by media blocks."""
    if (data is None) == (url is None):
        raise ValueError(f"{block_name} requires exactly one of 'data' or 'url'")
    if data is not None and media_type is None:
        raise ValueError(f"{block_name} requires media_type with base64 data")


ContentBlock = TextBlock | ImageBlock | DocumentBlock


@dataclass
class Message:
    """A message in the conversation.

    content is either a plain string (the fast path — unchanged behavior),
    or a list of content blocks for multimodal input (USER messages only;
    currently supported by the Anthropic provider).
    """

    role: Role
    content: str | list[ContentBlock] | None = None
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
    """Response from a completion request.

    stop_reason is the provider-native stop/finish reason passed through
    verbatim: truncation is "max_tokens" on Anthropic and "length" on
    OpenAI-compatible providers.
    """

    message: Message
    usage: Usage
    model: str  # Model string returned by API (may differ from input)
    stop_reason: str | None = None


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
        cache_conversation: bool = True,
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
            cache_conversation: When False, providers with explicit prompt-cache
                breakpoints (Anthropic) skip the last-message breakpoint — for
                one-shot calls whose conversation is never re-sent. System prompt
                and tool caching are unaffected. Providers with automatic caching
                ignore this.

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
        cache_conversation: bool = True,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from the LLM.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-2.0). None uses provider default.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.
            cache_conversation: When False, providers with explicit prompt-cache
                breakpoints (Anthropic) skip the last-message breakpoint. See
                `complete`.

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


def text_of(message: Message) -> str:
    """Text content of a message, regardless of content shape.

    Plain-str content is returned as-is; block-list content returns the
    TextBlock texts joined with newlines (media blocks contribute nothing);
    None returns "".
    """
    if message.content is None:
        return ""
    if isinstance(message.content, str):
        return message.content
    return "\n".join(
        block.text for block in message.content if isinstance(block, TextBlock)
    )


def content_to_json(
    content: str | list[ContentBlock] | None,
) -> str | list[dict[str, object]] | None:
    """JSON-safe encoding of message content for persistence.

    Plain strings and None pass through; block lists encode as typed dicts
    ({"type": "text" | "image" | "document", ...}) that survive json.dumps.
    """
    if content is None or isinstance(content, str):
        return content
    encoded: list[dict[str, object]] = []
    for block in content:
        if isinstance(block, TextBlock):
            encoded.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            encoded.append(
                {
                    "type": "image",
                    "media_type": block.media_type,
                    "data": block.data,
                    "url": block.url,
                }
            )
        else:
            encoded.append(
                {
                    "type": "document",
                    "media_type": block.media_type,
                    "data": block.data,
                    "url": block.url,
                }
            )
    return encoded


def required_content_types(messages: list[Message]) -> tuple[bool, bool]:
    """Content capabilities required by a conversation.

    Returns:
        (needs_images, needs_documents) — True when any message carries an
        ImageBlock / DocumentBlock. Used for model capability gating and
        capability-aware fallback.
    """
    needs_images = False
    needs_documents = False
    for message in messages:
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ImageBlock):
                    needs_images = True
                elif isinstance(block, DocumentBlock):
                    needs_documents = True
    return needs_images, needs_documents
