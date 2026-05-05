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
    Message,
    Role,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)
from neosian._foundation.shared.constants import (
    ErrorMessages,
    LLMDefaults,
)
from neosian._foundation.shared.exceptions import (
    ToolCallGenerationError,
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

# JSON Schema keywords Anthropic strict mode rejects, by JSON type.
# Stripping is required because tool calls are sent with strict: true and any
# unsupported keyword in a consumer's parameter schema yields a 400 from the API.
_UNSUPPORTED_NUMERIC_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
    }
)
_UNSUPPORTED_STRING_KEYS = frozenset({"minLength", "maxLength"})
# minItems/maxItems are allowed only when 0 or 1; values > 1 are rejected.
_BOUNDED_ARRAY_KEYS = frozenset({"minItems", "maxItems"})


def _strip_strict_unsupported_recursive(schema: Any) -> None:
    """Recursively strip strict-mode-incompatible keywords in place."""
    if not isinstance(schema, dict):
        return

    schema_type = schema.get("type")
    if schema_type in ("integer", "number"):
        for key in _UNSUPPORTED_NUMERIC_KEYS:
            schema.pop(key, None)
    elif schema_type == "string":
        for key in _UNSUPPORTED_STRING_KEYS:
            schema.pop(key, None)
    elif schema_type == "array":
        for key in _BOUNDED_ARRAY_KEYS:
            value = schema.get(key)
            if isinstance(value, int) and value > 1:
                schema.pop(key, None)

    if "properties" in schema:
        for prop_schema in schema["properties"].values():
            _strip_strict_unsupported_recursive(prop_schema)

    if "$defs" in schema:
        for def_schema in schema["$defs"].values():
            _strip_strict_unsupported_recursive(def_schema)

    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            for item in schema[key]:
                _strip_strict_unsupported_recursive(item)

    if "items" in schema:
        _strip_strict_unsupported_recursive(schema["items"])


def _strip_strict_unsupported_constraints(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove JSON Schema keywords incompatible with Anthropic strict mode.

    Strict mode (used on tool calls and structured outputs) rejects:
      - minimum/maximum/exclusiveMinimum/exclusiveMaximum/multipleOf on integer/number
      - minLength/maxLength on string
      - minItems/maxItems > 1 on array (0 and 1 are allowed)

    `pattern` is preserved; only specific regex constructs (backreferences,
    lookaheads, word boundaries) are rejected, and detecting those is left as
    a follow-up if real bugs appear.

    Returns a sanitized deep copy; the input is not mutated.
    """
    result: dict[str, Any] = copy.deepcopy(schema)
    _strip_strict_unsupported_recursive(result)
    return result


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
        """Send a completion request to Anthropic.

        Automatically retries if tool call generation fails.
        After max retries, raises ToolCallGenerationError.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-1.0). None uses default.
            response_format: Optional structured output configuration.
            reasoning_effort: Optional reasoning effort level for supported models.

        Returns:
            CompletionResponse with the model's response.

        Raises:
            ToolCallGenerationError: If tool call generation fails after retries.
            UnsupportedParameterError: If reasoning_effort used with unsupported model.
        """
        # Validate reasoning_effort
        if reasoning_effort is not None and not model.supports_reasoning:
            raise UnsupportedParameterError(
                ErrorMessages.REASONING_EFFORT_NOT_SUPPORTED.format(model=model.value)
            )

        # Anthropic MAX is Opus 4.6 only — downgrade to HIGH for other models
        effective_effort = reasoning_effort
        if reasoning_effort == ReasoningEffort.MAX and model != Model.CLAUDE_OPUS_4_6:
            logger.warning(
                ErrorMessages.REASONING_EFFORT_MAX_DOWNGRADED_ANTHROPIC.format(
                    model=model.value
                )
            )
            effective_effort = ReasoningEffort.HIGH

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        # Apply prompt caching breakpoints
        cached_system, anthropic_messages = self._apply_cache_control(
            system_prompt, anthropic_messages
        )

        current_temp = (
            temperature if temperature is not None else LLMDefaults.TEMPERATURE
        )

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
                else:
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
                response = await self._client.messages.create(**kwargs)

                return self._parse_response(response)

            except BadRequestError as e:
                if self._is_tool_call_error(e) and tools is not None:
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
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
        )

    async def stream(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
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

        Yields:
            StreamChunk objects as they arrive.

        Raises:
            UnsupportedParameterError: If reasoning_effort used with unsupported model.
        """
        # Validate reasoning_effort
        if reasoning_effort is not None and not model.supports_reasoning:
            raise UnsupportedParameterError(
                ErrorMessages.REASONING_EFFORT_NOT_SUPPORTED.format(model=model.value)
            )

        # Anthropic MAX is Opus 4.6 only — downgrade to HIGH for other models
        effective_effort = reasoning_effort
        if reasoning_effort == ReasoningEffort.MAX and model != Model.CLAUDE_OPUS_4_6:
            logger.warning(
                ErrorMessages.REASONING_EFFORT_MAX_DOWNGRADED_ANTHROPIC.format(
                    model=model.value
                )
            )
            effective_effort = ReasoningEffort.HIGH

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        # Apply prompt caching breakpoints
        cached_system, anthropic_messages = self._apply_cache_control(
            system_prompt, anthropic_messages
        )

        temp = temperature if temperature is not None else LLMDefaults.TEMPERATURE

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }

        # Thinking mode: add adaptive thinking + effort, omit temperature
        if effective_effort is not None:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effective_effort.value}
        else:
            kwargs["temperature"] = temp

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

                elif event.type == "message_stop":
                    finish_reason = "tool_use" if accumulated_tool_calls else "stop"
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
                system_prompt = msg.content
            elif msg.role == Role.USER:
                anthropic_messages.append(
                    {"role": "user", "content": msg.content or ""}
                )
            elif msg.role == Role.ASSISTANT:
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

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert internal tool definitions to Anthropic format.

        Tools are sent with strict: true so the model is constrained to emit
        arguments matching the input_schema. Strict mode caps at 20 tools per
        request (upstream Anthropic limit; not enforced here) and requires
        additionalProperties: false on the root object schema.

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
            input_schema = _strip_strict_unsupported_constraints(tool.parameters)
            if (
                input_schema.get("type") == "object"
                and "additionalProperties" not in input_schema
            ):
                input_schema["additionalProperties"] = False
            result.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "strict": True,
                    "input_schema": input_schema,
                }
            )
        if result:
            result[-1]["cache_control"] = {"type": "ephemeral"}
        return result

    def _apply_cache_control(
        self,
        system_prompt: str | None,
        anthropic_messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
        """Apply cache_control breakpoints for Anthropic prompt caching.

        Adds ephemeral cache breakpoints to the system prompt and the last
        message in the conversation. This enables Anthropic to cache the
        prefix (tools + system + conversation history) across API calls,
        reducing input token costs by up to 90% on cache reads.

        Cache order: tools (handled by _convert_tools) → system → messages.

        Args:
            system_prompt: The system prompt string (or None).
            anthropic_messages: Converted Anthropic-format messages.

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
        if anthropic_messages:
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

        schema = _strip_strict_unsupported_constraints(
            get_json_schema(response_format.schema)
        )
        # Anthropic requires additionalProperties: false for strict schemas
        if response_format.strict and "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        return {
            "type": "json_schema",
            "schema": schema,
        }

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
