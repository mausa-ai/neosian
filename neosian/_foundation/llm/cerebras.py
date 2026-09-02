"""Cerebras LLM client implementation."""

import logging
from collections.abc import AsyncIterator
from typing import Any

from cerebras.cloud.sdk import AsyncCerebras, BadRequestError, omit
from cerebras.cloud.sdk.types.chat.chat_completion import (
    ChatChunkResponse,
    ChatChunkResponseUsage,
    ErrorChunkResponse,
)

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
from neosian._foundation.llm.cerebras_convert import (
    convert_messages,
    convert_response_format,
    convert_tools,
)
from neosian._foundation.llm.errors import tool_arguments, wrap_provider_error
from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    NeosianError,
    ProviderError,
    ToolCallGenerationError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    AnyModel,
    ReasoningEffort,
    ResponseFormat,
    ToolCallId,
    ToolName,
)

logger = logging.getLogger(__name__)


def _usage_of(usage: ChatChunkResponseUsage) -> Usage:
    details = usage.prompt_tokens_details
    cache_read = (details.cached_tokens if details else 0) or 0
    return Usage(
        input_tokens=(usage.prompt_tokens or 0) - cache_read,
        output_tokens=usage.completion_tokens or 0,
        cache_read_tokens=cache_read,
    )


class CerebrasClient(BaseLLMClient):
    """Cerebras LLM client.

    Uses the Cerebras SDK for fast inference on wafer-scale hardware.
    Includes automatic retry with lower temperature when tool call
    generation fails.
    """

    def __init__(
        self, api_key: str, max_retries: int = LLMDefaults.MAX_RETRIES
    ) -> None:
        """Initialize the Cerebras client.

        Args:
            api_key: Cerebras API key. Required, no implicit env var reading.
            max_retries: Transport-level retries handled by the SDK
                (429/5xx/connection errors, exponential backoff).
        """
        self._client = AsyncCerebras(api_key=api_key, max_retries=max_retries)

    async def complete(
        self,
        messages: list[Message],
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,  # noqa: ARG002 - no explicit cache breakpoints
        server_compaction: bool = False,  # noqa: ARG002 - Anthropic-only compaction beta
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

        # Build kwargs — only include reasoning params when set,
        # as Cerebras API rejects None values for these fields.
        kwargs: dict[str, Any] = {
            "model": model.value,
            "messages": cerebras_messages,
            "tools": cerebras_tools,
            "temperature": temperature if temperature is not None else omit,
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
                # An overflow is classified before the tool retry (LL-4).
                wrapped = wrap_provider_error("cerebras", e, model=model)
                if isinstance(wrapped, ContextWindowExceededError):
                    raise wrapped from e
                if self._is_tool_call_error(e) and tools is not None:
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
                        # Lower the temperature on retry only when one was
                        # explicitly in play (LL-15).
                        if temperature is not None:
                            kwargs["temperature"] = LLMDefaults.RETRY_TEMPERATURE
                        continue
                    raise ToolCallGenerationError(
                        retries=LLMDefaults.MAX_TOOL_CALL_RETRIES
                    ) from e
                raise wrapped from e
            except Exception as exc:
                raise wrap_provider_error("cerebras", exc, model=model) from exc

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
                tool_calls.append(
                    ToolCall(
                        id=ToolCallId(tc.id),
                        name=ToolName(tc.function.name),
                        arguments=tool_arguments(
                            "cerebras",
                            tc.function.arguments or "",
                            stop_reason=choice.finish_reason,
                        ),
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
                cache_read_tokens=cache_read,
            ),
            model=response.model,  # type: ignore[attr-defined]
            stop_reason=getattr(choice, "finish_reason", None),
        )

    async def stream(
        self,
        messages: list[Message],
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,  # noqa: ARG002 - no explicit cache breakpoints
        server_compaction: bool = False,  # noqa: ARG002 - Anthropic-only compaction beta
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
        # Build kwargs — only include reasoning params when set,
        # as Cerebras API rejects None values for these fields.
        kwargs: dict[str, Any] = {
            "model": model.value,
            "messages": cerebras_messages,
            "tools": cerebras_tools,
            "temperature": temperature if temperature is not None else omit,
            "max_completion_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if effective_effort:
            kwargs["reasoning_effort"] = effective_effort.value
            kwargs["reasoning_format"] = "parsed"

        try:
            stream = await self._client.chat.completions.create(
                **kwargs,
            )

            # Track tool calls being built across chunks
            tool_call_builders: dict[int, dict[str, str]] = {}

            async for chunk in stream:  # type: ignore[union-attr]
                # The stream union also carries ErrorChunkResponse; surface it
                # rather than silently dropping a mid-stream provider error.
                if isinstance(chunk, ErrorChunkResponse):
                    raise ProviderError("cerebras", str(chunk.error))
                if not isinstance(chunk, ChatChunkResponse):
                    continue

                # Usage rides whichever chunk carries it — OpenAI's trailing
                # choices-empty chunk, or a door's final content chunk (LL-12).
                usage = _usage_of(chunk.usage) if chunk.usage else None
                if not chunk.choices:
                    if usage:
                        yield StreamChunk(usage=usage, model=chunk.model)
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Handle content
                content = delta.content if delta and delta.content else None

                # Handle reasoning
                reasoning = getattr(delta, "reasoning", None) if delta else None

                # Handle tool calls (streamed in parts)
                tool_calls: list[ToolCall] = []
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
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
                # Any terminal finish releases the accumulated calls (the
                # OpenAI wire's rule, LL-13): the agent loop keys on the
                # calls' presence, and a truncated call must name "length".
                if finish_reason and tool_call_builders:
                    for builder in tool_call_builders.values():
                        tool_calls.append(
                            ToolCall(
                                id=ToolCallId(builder["id"]),
                                name=ToolName(builder["name"]),
                                arguments=tool_arguments(
                                    "cerebras",
                                    builder["arguments"],
                                    stop_reason=finish_reason,
                                ),
                            )
                        )

                yield StreamChunk(
                    content=content,
                    reasoning=reasoning,
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    usage=usage,
                    model=chunk.model,
                )
        except NeosianError:
            raise
        except Exception as exc:
            raise wrap_provider_error("cerebras", exc, model=model) from exc

    # The converter bodies live in cerebras_convert.py (pure move, size-gate
    # headroom); these delegates keep the client the single entry point.
    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        return convert_messages(messages)

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return convert_tools(tools)

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> dict[str, object]:
        return convert_response_format(response_format)

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
