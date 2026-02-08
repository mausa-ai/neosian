"""Anthropic Claude LLM client implementation."""

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
    StructuredOutputs,
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

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

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
                if reasoning_effort is not None:
                    kwargs["thinking"] = {"type": "adaptive"}
                    kwargs["output_config"] = {"effort": reasoning_effort.value}
                else:
                    kwargs["temperature"] = current_temp

                if system_prompt:
                    kwargs["system"] = system_prompt

                if anthropic_tools:
                    kwargs["tools"] = anthropic_tools

                # Use beta API for structured outputs
                if response_format:
                    kwargs["betas"] = [StructuredOutputs.ANTHROPIC_BETA]
                    kwargs["output_format"] = self._convert_response_format(
                        response_format
                    )
                    response = await self._client.beta.messages.create(**kwargs)
                else:
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

        system_prompt, anthropic_messages = self._convert_messages(messages)
        temp = temperature if temperature is not None else LLMDefaults.TEMPERATURE

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }

        # Thinking mode: add adaptive thinking + effort, omit temperature
        if reasoning_effort is not None:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": reasoning_effort.value}
        else:
            kwargs["temperature"] = temp

        if system_prompt:
            kwargs["system"] = system_prompt

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        async with self._client.messages.stream(**kwargs) as stream:
            usage_data: Usage | None = None

            # Tool call accumulation state
            accumulated_tool_calls: list[ToolCall] = []
            current_tool_id: str | None = None
            current_tool_name: str | None = None
            current_tool_input: str = ""

            async for event in stream:
                if event.type == "content_block_start":
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
                    # Capture usage from message_delta event
                    if hasattr(event, "usage") and event.usage:
                        usage_data = Usage(
                            input_tokens=event.usage.input_tokens or 0,
                            output_tokens=event.usage.output_tokens,
                        )

                elif event.type == "message_stop":
                    finish_reason = "tool_use" if accumulated_tool_calls else "stop"
                    yield StreamChunk(
                        finish_reason=finish_reason,
                        usage=usage_data,
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

        Args:
            tools: Internal ToolDefinition objects.

        Returns:
            Anthropic-formatted tool definitions.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> dict[str, object]:
        """Convert ResponseFormat to Anthropic output_format.

        Args:
            response_format: Internal ResponseFormat configuration.

        Returns:
            Anthropic-compatible output_format dict.
        """
        from neosian._foundation.shared.schema import get_json_schema

        schema = get_json_schema(response_format.schema)
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
