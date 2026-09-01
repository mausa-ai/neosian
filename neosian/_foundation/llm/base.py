"""Base protocol for LLM clients.

All LLM providers must implement this protocol.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

# Re-exports: the block types and helpers moved to llm/blocks.py at N4
# slice B; every existing `from ...llm.base import TextBlock` keeps working.
from neosian._foundation.llm.blocks import (
    CompactionBlock as CompactionBlock,
    ContentBlock as ContentBlock,
    DocumentBlock as DocumentBlock,
    ImageBlock as ImageBlock,
    TextBlock as TextBlock,
    assemble_streamed_content as assemble_streamed_content,
    content_to_json as content_to_json,
    required_content_types as required_content_types,
    requires_compaction_support as requires_compaction_support,
    text_of as text_of,
)
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.types import (
    AnyModel,
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
    """A tool call requested by the model.

    `extra` holds the provider's own fields on the call beyond
    id/name/arguments — the OpenAI wire echoes them back verbatim (Gemini
    3's `extra_content.google.thought_signature`, DESIGN §19.7); every
    other wire ignores them, and `None` is the common case.
    """

    id: ToolCallId
    name: ToolName
    arguments: dict[str, Any]
    extra: dict[str, Any] | None = None


@dataclass
class Message:
    """A message in the conversation.

    content is either a plain string (the fast path — unchanged behavior)
    or a list of content blocks: multimodal input on USER messages, and —
    since N4's server-compaction opt-in — TextBlock/CompactionBlock lists
    on ASSISTANT messages. Both shapes are Anthropic-only; the other
    converters reject block content rather than silently dropping it.
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

    `native_type` names a provider-native tool type (Anthropic's
    `memory_20250818`). A client that recognizes it sends the schema-less
    native declaration — the model's trained behavior replaces the
    description; every other client ignores the marker and sends the
    ordinary function schema, so capability-aware fallback needs no new
    gate. Set only by the library (ledger #41).
    """

    name: ToolName
    description: str
    parameters: dict[str, Any]
    strict: bool = False
    native_type: str | None = None


@dataclass
class StreamChunk:
    """A chunk of streamed response.

    `model` is the API-reported model string (may differ from the requested
    enum value), populated by every client on every chunk it can — the
    streaming counterpart of CompletionResponse.model.

    `compaction` carries server-side compaction blocks on the terminal
    chunk (Anthropic's compact beta, opt-in); consumers weave them into
    the assistant message they build so the echo-back contract holds.
    """

    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: "Usage | None" = None
    model: str | None = None
    compaction: tuple[CompactionBlock, ...] = ()


_MTOK: Final = 1_000_000  # tokens per MTok — the pricing-rate divisor


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage in the four ecosystem token classes (ECOSYSTEM §3).

    input_tokens always means non-cached input. Cache fields are populated
    by providers with prompt caching:
    - Anthropic: cache_write_tokens + cache_read_tokens
    - OpenAI: cache_read_tokens only (automatic caching, no write concept)
    - Cerebras: cache_read_tokens only (automatic caching, like OpenAI)

    total_tokens = input + output + cache_read + cache_write.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens used (includes cached tokens)."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: "Usage") -> "Usage":
        """Field-wise sum, for accumulating usage across LLM calls."""
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    def cost_micro_usd(self, model: AnyModel) -> int | None:
        """Cost of this usage in integer micro-USD at the model's list prices.

        Ceiling division — never undercount (ECOSYSTEM §4). Approximate, for
        observability, not a billing source (see PRICES_AS_OF in
        shared.types for the verification date). Cache token classes fall
        back to the input rate when the provider publishes no separate
        cache rate.

        Args:
            model: The model whose pricing to apply. For multi-model runs
                (fallback), price each model's usage separately.

        Returns:
            Cost in micro-USD, or None if the model has no verified pricing.
        """
        pricing = model.pricing
        if pricing is None:
            return None
        total = (
            self.input_tokens * pricing.input_per_mtok
            + self.output_tokens * pricing.output_per_mtok
            + self.cache_read_tokens * pricing.effective_cache_read_per_mtok
            + self.cache_write_tokens * pricing.effective_cache_write_per_mtok
        )
        return -(-total // _MTOK)  # ceiling — never undercount


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Usage attributed to one API-reported model string.

    One entry per distinct model, first-appearance order; aggregated onto
    AgentResponse.usage_by_model and terminal events (DESIGN §4).
    """

    model: str
    usage: Usage


class StopReason(str, Enum):
    """Provider-agnostic stop reason.

    Providers report the same condition under different names (truncation is
    "max_tokens" on Anthropic, "length" on OpenAI-compatible providers).
    This enum is the normalized view; the raw provider value is always kept
    alongside it for debugging.
    """

    STOP = "stop"
    MAX_TOKENS = "max_tokens"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    OTHER = "other"


_STOP_REASON_MAP: dict[str, StopReason] = {
    # Natural completion
    "end_turn": StopReason.STOP,
    "stop": StopReason.STOP,
    "stop_sequence": StopReason.STOP,
    # Truncation at the output-token cap
    "max_tokens": StopReason.MAX_TOKENS,
    "length": StopReason.MAX_TOKENS,
    # Model requested tool execution
    "tool_use": StopReason.TOOL_CALLS,
    "tool_calls": StopReason.TOOL_CALLS,
    "function_call": StopReason.TOOL_CALLS,
    # Provider-side content moderation
    "content_filter": StopReason.CONTENT_FILTER,
    "refusal": StopReason.CONTENT_FILTER,
}


def normalize_stop_reason(raw: str | None) -> StopReason | None:
    """Normalize a provider-native stop/finish reason.

    Args:
        raw: Provider value (e.g. "end_turn", "length"), or None.

    Returns:
        The normalized StopReason, StopReason.OTHER for unrecognized values
        (e.g. "pause_turn", "model_context_window_exceeded"), or None when
        the provider reported none.
    """
    if raw is None:
        return None
    return _STOP_REASON_MAP.get(raw, StopReason.OTHER)


@dataclass
class CompletionResponse:
    """Response from a completion request.

    stop_reason is the provider-native stop/finish reason passed through
    verbatim: truncation is "max_tokens" on Anthropic and "length" on
    OpenAI-compatible providers. Use normalize_stop_reason() for the
    provider-agnostic view.
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
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
        server_compaction: bool = False,
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
            server_compaction: Opt into provider-side history compaction
                (Anthropic's compact beta today; other providers ignore it,
                like cache_conversation). Responses may then carry a
                CompactionBlock the caller must echo back verbatim.

        Returns:
            CompletionResponse with the model's response.
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
        server_compaction: bool = False,
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
            server_compaction: Opt into provider-side history compaction;
                see `complete`. Compaction blocks arrive on the terminal
                chunk's `compaction` field.

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
