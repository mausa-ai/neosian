"""Anthropic Claude LLM client implementation."""

import copy
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic, BadRequestError

from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    Role,
    StreamChunk,
    TextBlock,
    ToolCall,
    ToolDefinition,
    Usage,
    required_content_types,
)
from neosian._foundation.shared.constants import (
    ErrorMessages,
    LLMDefaults,
)
from neosian._foundation.shared.exceptions import (
    ToolCallGenerationError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ResponseFormat,
    ToolCallId,
    ToolName,
)

logger = logging.getLogger(__name__)

# Anthropic rejects these on integer/number fields regardless of strict mode.
_ALWAYS_UNSUPPORTED_NUMERIC_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)
# Strict-mode-only rejections (per Anthropic structured-outputs docs).
_STRICT_UNSUPPORTED_NUMERIC_KEYS = frozenset({"multipleOf"})
_STRICT_UNSUPPORTED_STRING_KEYS = frozenset({"minLength", "maxLength"})
# minItems/maxItems are allowed only when 0 or 1 under strict mode.
_STRICT_BOUNDED_ARRAY_KEYS = frozenset({"minItems", "maxItems"})


def _strip_unsupported_recursive(schema: Any, *, strict: bool) -> None:
    """Recursively strip Anthropic-incompatible keywords in place."""
    if not isinstance(schema, dict):
        return

    schema_type = schema.get("type")
    if schema_type in ("integer", "number"):
        for key in _ALWAYS_UNSUPPORTED_NUMERIC_KEYS:
            schema.pop(key, None)
        if strict:
            for key in _STRICT_UNSUPPORTED_NUMERIC_KEYS:
                schema.pop(key, None)
    elif schema_type == "string" and strict:
        for key in _STRICT_UNSUPPORTED_STRING_KEYS:
            schema.pop(key, None)
    elif schema_type == "array" and strict:
        for key in _STRICT_BOUNDED_ARRAY_KEYS:
            value = schema.get(key)
            if isinstance(value, int) and value > 1:
                schema.pop(key, None)

    if "properties" in schema:
        for prop_schema in schema["properties"].values():
            _strip_unsupported_recursive(prop_schema, strict=strict)

    if "$defs" in schema:
        for def_schema in schema["$defs"].values():
            _strip_unsupported_recursive(def_schema, strict=strict)

    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            for item in schema[key]:
                _strip_unsupported_recursive(item, strict=strict)

    if "items" in schema:
        _strip_unsupported_recursive(schema["items"], strict=strict)


def _strip_unsupported_constraints(
    schema: dict[str, Any], *, strict: bool
) -> dict[str, Any]:
    """Remove JSON Schema keywords Anthropic rejects.

    Always strips: minimum/maximum/exclusiveMinimum/exclusiveMaximum on
    integer/number (Anthropic rejects these regardless of strict mode).

    When strict=True, additionally strips: multipleOf on numbers; minLength
    and maxLength on strings; minItems/maxItems > 1 on arrays. Per the
    Anthropic structured-outputs docs, these are rejected only in strict mode.

    `pattern` is preserved; only specific regex constructs (backreferences,
    lookaheads, word boundaries) are rejected, and detecting those is left as
    a follow-up if real bugs appear.

    Returns a sanitized deep copy; the input is not mutated.
    """
    result: dict[str, Any] = copy.deepcopy(schema)
    _strip_unsupported_recursive(result, strict=strict)
    return result


# Models on which the API rejects sampling parameters entirely (temperature,
# top_p, top_k all return a 400 — removed, not merely defaulted).
_SAMPLING_REJECTED_MODELS: frozenset[Model] = frozenset({Model.CLAUDE_OPUS_5})


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude LLM client.

    Uses the Anthropic SDK. Includes automatic retry when tool call
    generation fails.
    """

    def __init__(self, api_key: str) -> None:
        """Initialize the Anthropic client.

        Args:
            api_key: Anthropic API key. Required, no implicit env var reading.
        """
        self._client = AsyncAnthropic(api_key=api_key)

    def _validate_temperature_support(
        self, model: Model, temperature: float | None
    ) -> None:
        """Reject explicit temperature on models that removed sampling params.

        Args:
            model: Target model.
            temperature: Requested temperature, if any.

        Raises:
            UnsupportedParameterError: If temperature was explicitly provided
                for a model whose API rejects sampling parameters.
        """
        if temperature is not None and model in _SAMPLING_REJECTED_MODELS:
            raise UnsupportedParameterError(
                ErrorMessages.ANTHROPIC_TEMPERATURE_NOT_SUPPORTED.format(
                    model=model.value
                )
            )

    def _resolve_effort(
        self, model: Model, reasoning_effort: ReasoningEffort | None
    ) -> ReasoningEffort | None:
        """Downgrade MAX effort on models whose spec doesn't allow it.

        Args:
            model: Target model.
            reasoning_effort: Requested effort level, if any.

        Returns:
            The effort level to send to the API.
        """
        if reasoning_effort == ReasoningEffort.MAX and not model.supports_max_effort:
            logger.warning(
                ErrorMessages.REASONING_EFFORT_MAX_DOWNGRADED_ANTHROPIC.format(
                    model=model.value
                )
            )
            return ReasoningEffort.HIGH
        return reasoning_effort

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
        """Send a completion request to Anthropic.

        Automatically retries if tool call generation fails.
        After max retries, raises ToolCallGenerationError.

        The request is streamed internally (messages.stream +
        get_final_message) so large max_tokens values don't trip the SDK's
        non-streaming timeout guard on long-output workloads.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-1.0). None uses default.
            response_format: Optional structured output configuration.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.
            cache_conversation: When False, skip the last-message cache
                breakpoint (one-shot calls); system/tool caching unaffected.

        Returns:
            CompletionResponse with the model's response.

        Raises:
            ToolCallGenerationError: If tool call generation fails after retries.
            UnsupportedParameterError: If reasoning_effort used with unsupported model.
            UnsupportedContentError: If messages carry content blocks the
                model does not support.
        """
        # Validate reasoning_effort
        if reasoning_effort is not None and not model.supports_reasoning:
            raise UnsupportedParameterError(
                ErrorMessages.REASONING_EFFORT_NOT_SUPPORTED.format(model=model.value)
            )

        self._validate_temperature_support(model, temperature)
        self._validate_content_support(messages, model)

        effective_effort = self._resolve_effort(model, reasoning_effort)

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        # Apply prompt caching breakpoints
        cached_system, anthropic_messages = self._apply_cache_control(
            system_prompt, anthropic_messages, cache_last_message=cache_conversation
        )

        # Only send temperature when explicitly requested: newer Claude
        # models (e.g. Sonnet 5) reject non-default sampling parameters
        # with a 400, so the API default must apply when unset.
        current_temp: float | None = temperature

        for attempt in range(LLMDefaults.MAX_TOOL_CALL_RETRIES + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": anthropic_messages,
                    "max_tokens": max_tokens,
                }

                # Thinking mode: add adaptive thinking + effort, omit temperature
                if effective_effort is not None:
                    kwargs["thinking"] = {"type": "adaptive"}
                    kwargs["output_config"] = {"effort": effective_effort.value}
                elif current_temp is not None:
                    kwargs["temperature"] = current_temp

                if cached_system:
                    kwargs["system"] = cached_system

                if anthropic_tools:
                    kwargs["tools"] = anthropic_tools

                if response_format:
                    # output_config may already exist from reasoning effort above;
                    # merge rather than overwrite.
                    output_config = kwargs.setdefault("output_config", {})
                    output_config["format"] = self._convert_response_format(
                        response_format
                    )
                # Stream internally: the SDK refuses non-streaming requests
                # it estimates may exceed ~10 minutes (large max_tokens).
                async with self._client.messages.stream(**kwargs) as stream:
                    response = await stream.get_final_message()

                return self._parse_response(response)

            except BadRequestError as e:
                if self._is_tool_call_error(e) and tools is not None:
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
                        # Lower the temperature on retry only when one was
                        # explicitly in play — injecting it on models that
                        # reject sampling params would turn the retry into
                        # a 400.
                        if current_temp is not None:
                            current_temp = LLMDefaults.RETRY_TEMPERATURE
                        continue
                    raise ToolCallGenerationError(
                        retries=LLMDefaults.MAX_TOOL_CALL_RETRIES
                    ) from e
                raise

        # Should not reach here, but satisfy type checker
        raise ToolCallGenerationError(retries=LLMDefaults.MAX_TOOL_CALL_RETRIES)

    def _is_tool_call_error(self, error: BadRequestError) -> bool:
        """Check if the error is a tool call generation failure.

        Args:
            error: The BadRequestError to check.

        Returns:
            True if it's a tool call related error.
        """
        error_message = str(error).lower()
        return "tool" in error_message or "function" in error_message

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse Anthropic response into CompletionResponse.

        Args:
            response: Raw response from Anthropic API.

        Returns:
            Parsed CompletionResponse.
        """
        # Build text content, reasoning, and tool calls from content blocks
        text_content = ""
        reasoning_content = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:  # type: ignore[attr-defined]
            if block.type == "thinking":
                reasoning_content += block.thinking
            elif block.type == "redacted_thinking":
                pass  # Encrypted thinking block - cannot read content
            elif block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=ToolCallId(block.id),
                        name=ToolName(block.name),
                        arguments=(
                            dict(block.input) if isinstance(block.input, dict) else {}
                        ),
                    )
                )

        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=text_content if text_content else None,
                reasoning=reasoning_content if reasoning_content else None,
                tool_calls=tool_calls,
            ),
            usage=Usage(
                input_tokens=response.usage.input_tokens,  # type: ignore[attr-defined]
                output_tokens=response.usage.output_tokens,  # type: ignore[attr-defined]
                cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,  # type: ignore[attr-defined]
                cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,  # type: ignore[attr-defined]
            ),
            model=response.model,  # type: ignore[attr-defined]
            stop_reason=getattr(response, "stop_reason", None),
        )

    async def stream(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from Anthropic.

        Supports streaming content, reasoning, and tool calls. Tool call
        blocks are accumulated and yielded as complete ToolCall objects
        in the final StreamChunk.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-1.0). None uses default.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.
            cache_conversation: When False, skip the last-message cache
                breakpoint (one-shot calls); system/tool caching unaffected.

        Yields:
            StreamChunk objects as they arrive.

        Raises:
            UnsupportedParameterError: If reasoning_effort used with unsupported model.
            UnsupportedContentError: If messages carry content blocks the
                model does not support.
        """
        # Validate reasoning_effort
        if reasoning_effort is not None and not model.supports_reasoning:
            raise UnsupportedParameterError(
                ErrorMessages.REASONING_EFFORT_NOT_SUPPORTED.format(model=model.value)
            )

        self._validate_temperature_support(model, temperature)
        self._validate_content_support(messages, model)

        effective_effort = self._resolve_effort(model, reasoning_effort)

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        # Apply prompt caching breakpoints
        cached_system, anthropic_messages = self._apply_cache_control(
            system_prompt, anthropic_messages, cache_last_message=cache_conversation
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }

        # Thinking mode: add adaptive thinking + effort, omit temperature.
        # Temperature is only sent when explicitly requested: newer Claude
        # models (e.g. Sonnet 5) reject non-default sampling parameters.
        if effective_effort is not None:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effective_effort.value}
        elif temperature is not None:
            kwargs["temperature"] = temperature

        if cached_system:
            kwargs["system"] = cached_system

        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        async with self._client.messages.stream(**kwargs) as stream:
            # Usage tracking: input/cache from message_start, output from message_delta
            input_tokens: int = 0
            output_tokens: int = 0
            cache_creation_tokens: int = 0
            cache_read_tokens: int = 0

            # Tool call accumulation state
            accumulated_tool_calls: list[ToolCall] = []
            current_tool_id: str | None = None
            current_tool_name: str | None = None
            current_tool_input: str = ""

            # Real stop reason from the API (reported in message_delta)
            stop_reason: str | None = None

            async for event in stream:
                if event.type == "message_start":
                    # Input and cache tokens are reported in message_start
                    if hasattr(event, "message") and hasattr(event.message, "usage"):
                        msg_usage = event.message.usage
                        input_tokens = getattr(msg_usage, "input_tokens", 0) or 0
                        cache_creation_tokens = (
                            getattr(msg_usage, "cache_creation_input_tokens", 0) or 0
                        )
                        cache_read_tokens = (
                            getattr(msg_usage, "cache_read_input_tokens", 0) or 0
                        )
                        # Surface the partial usage immediately so consumers
                        # interrupted mid-stream (guard block, error) can meter
                        # the input/cache tokens already billed. The complete
                        # usage on the message_stop chunk supersedes this one —
                        # consumers must treat per-stream usage as last-wins.
                        yield StreamChunk(
                            usage=Usage(
                                input_tokens=input_tokens,
                                output_tokens=0,
                                cache_creation_input_tokens=cache_creation_tokens,
                                cache_read_input_tokens=cache_read_tokens,
                            )
                        )

                elif event.type == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "tool_use":
                        current_tool_id = block.id  # type: ignore[union-attr]
                        current_tool_name = block.name  # type: ignore[union-attr]
                        current_tool_input = ""

                elif event.type == "content_block_delta":
                    delta_type = getattr(event.delta, "type", None)
                    if delta_type == "thinking_delta":
                        yield StreamChunk(
                            reasoning=event.delta.thinking,  # type: ignore[union-attr]
                        )
                    elif delta_type == "text_delta":
                        yield StreamChunk(
                            content=event.delta.text,  # type: ignore[union-attr]
                        )
                    elif delta_type == "input_json_delta":
                        current_tool_input += event.delta.partial_json  # type: ignore[union-attr]

                elif event.type == "content_block_stop":
                    if current_tool_id is not None:
                        args = (
                            json.loads(current_tool_input) if current_tool_input else {}
                        )
                        accumulated_tool_calls.append(
                            ToolCall(
                                id=ToolCallId(current_tool_id),
                                name=ToolName(current_tool_name or ""),
                                arguments=args if isinstance(args, dict) else {},
                            )
                        )
                        current_tool_id = None
                        current_tool_name = None
                        current_tool_input = ""

                elif event.type == "message_delta":
                    # Output tokens are reported in message_delta
                    if hasattr(event, "usage") and event.usage:
                        output_tokens = event.usage.output_tokens
                    # The API's actual stop reason (e.g. "max_tokens",
                    # "end_turn", "tool_use") also arrives here.
                    delta = getattr(event, "delta", None)
                    delta_stop = getattr(delta, "stop_reason", None)
                    if delta_stop:
                        stop_reason = delta_stop

                elif event.type == "message_stop":
                    # Prefer the API's stop reason; fall back to the
                    # synthesized value if the event never carried one.
                    finish_reason = stop_reason or (
                        "tool_use" if accumulated_tool_calls else "stop"
                    )
                    yield StreamChunk(
                        finish_reason=finish_reason,
                        usage=Usage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cache_creation_input_tokens=cache_creation_tokens,
                            cache_read_input_tokens=cache_read_tokens,
                        ),
                        tool_calls=accumulated_tool_calls,
                    )

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert internal messages to Anthropic format.

        Anthropic requires system message to be passed separately.

        Args:
            messages: Internal Message objects.

        Returns:
            Tuple of (system_prompt, messages_list).
        """
        system_prompt: str | None = None
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                if isinstance(msg.content, list):
                    raise UnsupportedContentError(
                        ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                            provider="anthropic (system role)",
                            block_type="system-message",
                        )
                    )
                system_prompt = msg.content
            elif msg.role == Role.USER:
                if isinstance(msg.content, list):
                    anthropic_messages.append(
                        {
                            "role": "user",
                            "content": self._convert_content_blocks(msg.content),
                        }
                    )
                else:
                    anthropic_messages.append(
                        {"role": "user", "content": msg.content or ""}
                    )
            elif msg.role == Role.ASSISTANT:
                if isinstance(msg.content, list):
                    raise UnsupportedContentError(
                        ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                            provider="anthropic (assistant role)",
                            block_type="assistant-message",
                        )
                    )
                if msg.tool_calls:
                    content: list[dict[str, Any]] = []
                    if msg.content:
                        content.append({"type": "text", "text": msg.content})
                    for tc in msg.tool_calls:
                        content.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                    anthropic_messages.append({"role": "assistant", "content": content})
                else:
                    anthropic_messages.append(
                        {"role": "assistant", "content": msg.content or ""}
                    )
            elif msg.role == Role.TOOL:
                if isinstance(msg.content, list):
                    raise UnsupportedContentError(
                        ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                            provider="anthropic (tool role)",
                            block_type="tool-result",
                        )
                    )
                # Tool results in Anthropic are user messages with tool_result content
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content or "",
                            }
                        ],
                    }
                )

        return system_prompt, anthropic_messages

    def _convert_content_blocks(
        self, blocks: list[ContentBlock]
    ) -> list[dict[str, Any]]:
        """Convert internal content blocks to Anthropic content-block dicts.

        Caller block order is preserved. Anthropic recommends placing media
        blocks before text blocks for best results — callers control this.
        """
        result: list[dict[str, Any]] = []
        for block in blocks:
            if isinstance(block, TextBlock):
                result.append({"type": "text", "text": block.text})
            elif isinstance(block, (ImageBlock, DocumentBlock)):
                block_type = "image" if isinstance(block, ImageBlock) else "document"
                source: dict[str, Any]
                if block.data is not None:
                    source = {
                        "type": "base64",
                        "media_type": block.media_type,
                        "data": block.data,
                    }
                else:
                    source = {"type": "url", "url": block.url}
                result.append({"type": block_type, "source": source})
        return result

    def _validate_content_support(self, messages: list[Message], model: Model) -> None:
        """Raise if messages carry content blocks the model cannot handle.

        All currently registered Claude models support both images and
        documents; this gate future-proofs against text-only entries.

        Raises:
            UnsupportedContentError: If a required capability is missing.
        """
        needs_images, needs_documents = required_content_types(messages)
        if needs_images and not model.supports_images:
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_TYPE_NOT_SUPPORTED_BY_MODEL.format(
                    model=model.value, block_type="image"
                )
            )
        if needs_documents and not model.supports_documents:
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_TYPE_NOT_SUPPORTED_BY_MODEL.format(
                    model=model.value, block_type="document"
                )
            )

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert internal tool definitions to Anthropic format.

        Tools with strict=True opt into provider-enforced constrained decoding.
        Anthropic enforces a complexity budget across the aggregated strict
        tool schemas (cap of 20 strict tools per request, plus a per-request
        schema-complexity ceiling). Non-strict tools pass through as
        best-effort hints and do not count against either limit.

        Adds cache_control to the last tool definition. Anthropic caches
        everything up to and including the marked block, so marking the
        last tool caches all tool definitions.

        Args:
            tools: Internal ToolDefinition objects.

        Returns:
            Anthropic-formatted tool definitions with cache_control on last.
        """
        result: list[dict[str, Any]] = []
        for tool in tools:
            input_schema = _strip_unsupported_constraints(
                tool.parameters, strict=tool.strict
            )
            entry: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": input_schema,
            }
            if tool.strict:
                entry["strict"] = True
                if (
                    input_schema.get("type") == "object"
                    and "additionalProperties" not in input_schema
                ):
                    input_schema["additionalProperties"] = False
            result.append(entry)
        if result:
            result[-1]["cache_control"] = {"type": "ephemeral"}
        return result

    def _apply_cache_control(
        self,
        system_prompt: str | None,
        anthropic_messages: list[dict[str, Any]],
        cache_last_message: bool = True,
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
        """Apply cache_control breakpoints for Anthropic prompt caching.

        Adds ephemeral cache breakpoints to the system prompt and the last
        message in the conversation. This enables Anthropic to cache the
        prefix (tools + system + conversation history) across API calls,
        reducing input token costs by up to 90% on cache reads.

        Cache order: tools (handled by _convert_tools) → system → messages.

        The last-message breakpoint lands on the final content block
        whatever its type — cache_control is valid on image/document blocks,
        and media tokens are exactly the expensive prefix worth caching in
        multi-turn conversations. For one-shot calls whose conversation is
        never re-sent, pass cache_last_message=False to skip the breakpoint
        (avoids paying the 1.25x cache-write premium for nothing); the
        system-prompt breakpoint is unaffected.

        Args:
            system_prompt: The system prompt string (or None).
            anthropic_messages: Converted Anthropic-format messages.
            cache_last_message: Whether to place the last-message breakpoint.

        Returns:
            Tuple of (cached_system, cached_messages).
            cached_system is a structured list with cache_control, or None.
            cached_messages has cache_control on the last message's content.
        """
        # System: plain string → structured list with cache_control
        cached_system: list[dict[str, Any]] | None = None
        if system_prompt:
            cached_system = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # Last message: add cache_control to the last content block.
        # This caches the entire prefix (tools + system + all messages up
        # to this point) so subsequent calls only process new messages.
        if cache_last_message and anthropic_messages:
            last_msg = anthropic_messages[-1]
            content = last_msg["content"]
            if isinstance(content, str):
                # Convert string content to structured format with cache_control
                last_msg["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            elif isinstance(content, list) and content:
                # Add cache_control to the last block in the list
                content[-1]["cache_control"] = {"type": "ephemeral"}

        return cached_system, anthropic_messages

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> dict[str, object]:
        """Convert ResponseFormat to the value of Anthropic's output_config.format.

        Args:
            response_format: Internal ResponseFormat configuration.

        Returns:
            Anthropic-compatible format spec (placed at output_config["format"]).
        """
        from neosian._foundation.shared.schema import get_json_schema

        # get_json_schema guarantees additionalProperties: false on every
        # object (root and $defs). Anthropic requires it on output-format
        # schemas regardless of strict mode, so no adapter-side patch here.
        schema = _strip_unsupported_constraints(
            get_json_schema(response_format.schema), strict=response_format.strict
        )
        return {
            "type": "json_schema",
            "schema": schema,
        }

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
