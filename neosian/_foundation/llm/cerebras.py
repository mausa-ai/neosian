"""Cerebras LLM client implementation."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from cerebras.cloud.sdk import AsyncCerebras, BadRequestError

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
from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
from neosian._foundation.shared.exceptions import (
    ToolCallGenerationError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ResponseFormat,
    ToolCallId,
    ToolName,
)

logger = logging.getLogger(__name__)


class CerebrasClient(BaseLLMClient):
    """Cerebras LLM client.

    Uses the Cerebras SDK for fast inference on wafer-scale hardware.
    Includes automatic retry with lower temperature when tool call
    generation fails.
    """

    def __init__(self, api_key: str) -> None:
        """Initialize the Cerebras client.

        Args:
            api_key: Cerebras API key. Required, no implicit env var reading.
        """
        self._client = AsyncCerebras(api_key=api_key)

    async def complete(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,  # noqa: ARG002 - no explicit cache breakpoints
    ) -> CompletionResponse:
        """Send a completion request to Cerebras.

        Automatically retries with lower temperature if tool call generation
        fails. After max retries, raises ToolCallGenerationError.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-1.5). None uses default.
            response_format: Optional structured output configuration.
            reasoning_effort: Optional reasoning effort level.
            max_tokens: Maximum output tokens for this request.

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

        # Cerebras does not support MAX - downgrade to HIGH with warning
        effective_effort = reasoning_effort
        if reasoning_effort == ReasoningEffort.MAX:
            logger.warning(
                ErrorMessages.REASONING_EFFORT_MAX_DOWNGRADED_CEREBRAS.format(
                    model=model.value
                )
            )
            effective_effort = ReasoningEffort.HIGH

        cerebras_messages = self._convert_messages(messages)
        cerebras_tools = self._convert_tools(tools) if tools else None
        cerebras_response_format = (
            self._convert_response_format(response_format) if response_format else None
        )

        # Use provided temperature or default
        current_temp = (
            temperature if temperature is not None else LLMDefaults.TEMPERATURE
        )

        # Build kwargs — only include reasoning params when set,
        # as Cerebras API rejects None values for these fields.
        kwargs: dict[str, Any] = {
            "model": model.value,
            "messages": cerebras_messages,
            "tools": cerebras_tools,
            "temperature": current_temp,
            "max_completion_tokens": max_tokens,
            "response_format": cerebras_response_format,
        }
        if effective_effort:
            kwargs["reasoning_effort"] = effective_effort.value
            kwargs["reasoning_format"] = "parsed"

        for attempt in range(LLMDefaults.MAX_TOOL_CALL_RETRIES + 1):
            try:
                response = await self._client.chat.completions.create(
                    **kwargs,
                )
                return self._parse_response(response)

            except BadRequestError as e:
                # Check if it's a tool call error
                if self._is_tool_call_error(e) and tools is not None:
                    # If we have retries left, try with lower temperature
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
                        kwargs["temperature"] = LLMDefaults.RETRY_TEMPERATURE
                        continue
                    # Max retries exceeded
                    raise ToolCallGenerationError(
                        retries=LLMDefaults.MAX_TOOL_CALL_RETRIES
                    ) from e
                # Not a tool call error, re-raise
                raise

        # Should not reach here, but satisfy type checker
        raise ToolCallGenerationError(retries=LLMDefaults.MAX_TOOL_CALL_RETRIES)

    def _is_tool_call_error(self, error: BadRequestError) -> bool:
        """Check if the error is a tool call generation failure.

        Args:
            error: The BadRequestError to check.

        Returns:
            True if it's a tool call error.
        """
        if error.body and isinstance(error.body, dict):
            # Cerebras SDK unwraps body — error.body is the inner error dict
            code = error.body.get("code", "")
            message = error.body.get("message", "")
            if code == "tool_use_failed":
                return True
            if "tool" in str(message).lower() or "function" in str(message).lower():
                return True
        return False

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse Cerebras response into CompletionResponse.

        Args:
            response: Raw response from Cerebras API.

        Returns:
            Parsed CompletionResponse.
        """
        choice = response.choices[0]  # type: ignore[attr-defined]
        response_message = choice.message

        # Convert tool calls if present
        tool_calls: list[ToolCall] = []
        if response_message.tool_calls:
            for tc in response_message.tool_calls:
                # Normalize empty/missing arguments to {}
                args_str = tc.function.arguments or "{}"
                tool_calls.append(
                    ToolCall(
                        id=ToolCallId(tc.id),
                        name=ToolName(tc.function.name),
                        arguments=json.loads(args_str),
                    )
                )

        # Extract reasoning content if present
        reasoning = getattr(response_message, "reasoning", None)

        # Extract cache tokens if available
        cache_read = 0
        usage = response.usage  # type: ignore[attr-defined]
        if usage:
            details = getattr(usage, "prompt_tokens_details", None)
            if details:
                cache_read = getattr(details, "cached_tokens", 0) or 0

        prompt_tokens = usage.prompt_tokens if usage else 0

        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=response_message.content,
                reasoning=reasoning,
                tool_calls=tool_calls,
            ),
            usage=Usage(
                input_tokens=prompt_tokens - cache_read,
                output_tokens=usage.completion_tokens if usage else 0,
                cache_read_input_tokens=cache_read,
            ),
            model=response.model,  # type: ignore[attr-defined]
            stop_reason=getattr(choice, "finish_reason", None),
        )

    async def stream(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,  # noqa: ARG002 - no explicit cache breakpoints
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from Cerebras.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-1.5). None uses default.
            reasoning_effort: Optional reasoning effort level.
            max_tokens: Maximum output tokens for this request.

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

        # Cerebras does not support MAX - downgrade to HIGH with warning
        effective_effort = reasoning_effort
        if reasoning_effort == ReasoningEffort.MAX:
            logger.warning(
                ErrorMessages.REASONING_EFFORT_MAX_DOWNGRADED_CEREBRAS.format(
                    model=model.value
                )
            )
            effective_effort = ReasoningEffort.HIGH

        cerebras_messages = self._convert_messages(messages)
        cerebras_tools = self._convert_tools(tools) if tools else None
        temp = temperature if temperature is not None else LLMDefaults.TEMPERATURE

        # Build kwargs — only include reasoning params when set,
        # as Cerebras API rejects None values for these fields.
        kwargs: dict[str, Any] = {
            "model": model.value,
            "messages": cerebras_messages,
            "tools": cerebras_tools,
            "temperature": temp,
            "max_completion_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if effective_effort:
            kwargs["reasoning_effort"] = effective_effort.value
            kwargs["reasoning_format"] = "parsed"

        stream = await self._client.chat.completions.create(
            **kwargs,
        )

        # Track tool calls being built across chunks
        tool_call_builders: dict[int, dict[str, str]] = {}

        async for chunk in stream:  # type: ignore[union-attr]
            # Handle usage-only chunk (comes after finish_reason)
            if not chunk.choices and chunk.usage:
                # Extract cache tokens if available
                cache_read = 0
                details = getattr(chunk.usage, "prompt_tokens_details", None)
                if details:
                    cache_read = getattr(details, "cached_tokens", 0) or 0

                prompt_tokens = chunk.usage.prompt_tokens  # type: ignore[union-attr]

                yield StreamChunk(
                    usage=Usage(
                        input_tokens=prompt_tokens - cache_read,
                        output_tokens=chunk.usage.completion_tokens,  # type: ignore[union-attr]
                        cache_read_input_tokens=cache_read,
                    ),
                )
                continue

            if not chunk.choices:
                continue

            choice = chunk.choices[0]  # type: ignore[index]
            delta = choice.delta

            # Handle content
            content = delta.content if delta.content else None

            # Handle reasoning
            reasoning = getattr(delta, "reasoning", None)

            # Handle tool calls (streamed in parts)
            tool_calls: list[ToolCall] = []
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_builders:
                        tool_call_builders[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }

                    if tc.id:
                        tool_call_builders[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_builders[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_call_builders[idx][
                                "arguments"
                            ] += tc.function.arguments

            # On finish, yield completed tool calls
            finish_reason = choice.finish_reason
            if finish_reason == "tool_calls" and tool_call_builders:
                for builder in tool_call_builders.values():
                    # Normalize empty arguments to {}
                    args_str = builder["arguments"] or "{}"
                    tool_calls.append(
                        ToolCall(
                            id=ToolCallId(builder["id"]),
                            name=ToolName(builder["name"]),
                            arguments=json.loads(args_str),
                        )
                    )

            yield StreamChunk(
                content=content,
                reasoning=reasoning,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert internal messages to Cerebras format (OpenAI-compatible).

        Raises:
            UnsupportedContentError: On block-list content — neosian's
                Cerebras converter is text-only; media is never silently
                dropped.
        """
        result: list[dict[str, Any]] = []

        for msg in messages:
            if isinstance(msg.content, list):
                raise UnsupportedContentError(
                    ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                        provider="cerebras", block_type="multimodal"
                    )
                )
            if msg.role == Role.SYSTEM:
                result.append({"role": "system", "content": msg.content or ""})
            elif msg.role == Role.USER:
                result.append({"role": "user", "content": msg.content or ""})
            elif msg.role == Role.ASSISTANT:
                if msg.tool_calls:
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": safe_json_dumps(
                                        tc.arguments, "tool_call.arguments"
                                    ),
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                    result.append(assistant_msg)
                else:
                    result.append({"role": "assistant", "content": msg.content})
            elif msg.role == Role.TOOL:
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content or "",
                        "tool_call_id": msg.tool_call_id or "",
                    }
                )

        return result

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert internal tool definitions to Cerebras format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> dict[str, object]:
        """Convert ResponseFormat to Cerebras json_schema format.

        Args:
            response_format: Internal ResponseFormat configuration.

        Returns:
            Cerebras-compatible response_format dict.
        """
        from neosian._foundation.shared.schema import get_json_schema, get_schema_name

        schema = get_json_schema(response_format.schema)
        schema["additionalProperties"] = False
        return {
            "type": "json_schema",
            "json_schema": {
                "name": get_schema_name(response_format.schema),
                "strict": response_format.strict,
                "schema": schema,
            },
        }

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
