"""OpenAI LLM client implementation."""

import json
import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI, BadRequestError, omit
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionStreamOptionsParam,
    ChatCompletionToolParam,
)
from openai.types.chat.completion_create_params import (
    ResponseFormat as OpenAIResponseFormat,
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
from neosian._foundation.llm.errors import wrap_provider_error
from neosian._foundation.llm.openai_convert import (
    convert_messages,
    convert_response_format,
    convert_tools,
)
from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
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


class OpenAIClient(BaseLLMClient):
    """OpenAI LLM client.

    Uses the OpenAI SDK. Includes automatic retry with lower temperature
    when tool call generation fails.
    """

    def __init__(
        self, api_key: str, max_retries: int = LLMDefaults.MAX_RETRIES
    ) -> None:
        """Initialize the OpenAI client.

        Args:
            api_key: OpenAI API key. Required, no implicit env var reading.
            max_retries: Transport-level retries handled by the SDK
                (429/5xx/connection errors, exponential backoff).
        """
        self._client = AsyncOpenAI(api_key=api_key, max_retries=max_retries)

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
        server_compaction: bool = False,  # noqa: ARG002 - Anthropic-only compaction beta
    ) -> CompletionResponse:
        """Send a completion request to OpenAI.

        Automatically retries with lower temperature if tool call generation
        fails. After max retries, raises ToolCallGenerationError.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Not supported for GPT-5 models. Raises error if provided.
            response_format: Optional structured output configuration.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.

        Returns:
            CompletionResponse with the model's response.

        Raises:
            UnsupportedParameterError: If temperature is provided or reasoning_effort
                used with unsupported model.
            ToolCallGenerationError: If tool call generation fails after retries.
        """
        if temperature is not None:
            raise UnsupportedParameterError(
                ErrorMessages.OPENAI_TEMPERATURE_NOT_SUPPORTED
            )

        effective_effort = self._resolve_reasoning_effort(model, reasoning_effort)

        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None
        openai_response_format: OpenAIResponseFormat | None = (
            self._convert_response_format(response_format) if response_format else None
        )

        for attempt in range(LLMDefaults.MAX_TOOL_CALL_RETRIES + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=model.value,
                    messages=openai_messages,
                    tools=openai_tools if openai_tools else omit,
                    max_completion_tokens=max_tokens,
                    response_format=(
                        openai_response_format if openai_response_format else omit
                    ),
                    reasoning_effort=(
                        effective_effort.value if effective_effort else omit
                    ),
                )
                return self._parse_response(response)

            except BadRequestError as e:
                # Check if it's a tool call error
                if self._is_tool_call_error(e) and tools is not None:
                    # If we have retries left, retry
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
                        continue
                    # Max retries exceeded
                    raise ToolCallGenerationError(
                        retries=LLMDefaults.MAX_TOOL_CALL_RETRIES
                    ) from e
                raise wrap_provider_error("openai", e, model=model) from e
            except Exception as exc:
                raise wrap_provider_error("openai", exc, model=model) from exc

        # Should not reach here, but satisfy type checker
        raise ToolCallGenerationError(retries=LLMDefaults.MAX_TOOL_CALL_RETRIES)

    def _is_tool_call_error(self, error: BadRequestError) -> bool:
        """Check if the error is a tool call generation failure.

        Args:
            error: The BadRequestError to check.

        Returns:
            True if it's a tool call related error.
        """
        # OpenAI returns different error codes/messages for tool failures
        if error.body and isinstance(error.body, dict):
            err = error.body.get("error", {})
            if isinstance(err, dict):
                code = err.get("code", "")
                message = err.get("message", "")
                # Check for various tool-related error indicators
                return (
                    code == "invalid_tool_call"
                    or "tool" in message.lower()
                    or "function" in message.lower()
                )
        return False

    def _resolve_reasoning_effort(
        self, model: Model, reasoning_effort: ReasoningEffort | None
    ) -> ReasoningEffort | None:
        """Validate and normalize reasoning_effort for OpenAI models.

        Handles:
        - Validation: raises UnsupportedParameterError for non-reasoning models.
        - MAX downgrade: OpenAI does not support MAX, downgrade to HIGH.
        - GPT-5-Pro constraint: only supports HIGH, force other values to HIGH.

        Args:
            model: The model being used.
            reasoning_effort: The requested reasoning effort level, or None.

        Returns:
            The effective reasoning effort to pass to the API, or None.

        Raises:
            UnsupportedParameterError: If reasoning_effort used with non-reasoning model.
        """
        if reasoning_effort is None:
            return None

        if not model.supports_reasoning:
            raise UnsupportedParameterError(
                ErrorMessages.REASONING_EFFORT_NOT_SUPPORTED.format(model=model.value)
            )

        effective_effort = reasoning_effort

        # OpenAI does not support MAX - downgrade to HIGH with warning
        if reasoning_effort == ReasoningEffort.MAX:
            logger.warning(
                ErrorMessages.REASONING_EFFORT_MAX_DOWNGRADED_OPENAI.format(
                    model=model.value
                )
            )
            effective_effort = ReasoningEffort.HIGH

        # GPT-5-Pro only supports HIGH - force with warning
        if model == Model.GPT_5_PRO and effective_effort != ReasoningEffort.HIGH:
            logger.warning(
                ErrorMessages.REASONING_EFFORT_FORCED_HIGH.format(
                    model=model.value, requested=effective_effort.value
                )
            )
            effective_effort = ReasoningEffort.HIGH

        return effective_effort

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse OpenAI response into CompletionResponse.

        Args:
            response: Raw response from OpenAI API.

        Returns:
            Parsed CompletionResponse.
        """
        # Type ignore needed because openai SDK types are complex
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

        # Extract cached token count (OpenAI automatic prompt caching)
        cached_tokens = 0
        prompt_tokens = 0
        output_tokens = 0
        if response.usage:  # type: ignore[attr-defined]
            prompt_tokens = response.usage.prompt_tokens  # type: ignore[attr-defined]
            output_tokens = response.usage.completion_tokens  # type: ignore[attr-defined]
            details = getattr(response.usage, "prompt_tokens_details", None)  # type: ignore[attr-defined]
            cached_tokens = getattr(details, "cached_tokens", 0) or 0

        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=response_message.content,
                tool_calls=tool_calls,
            ),
            usage=Usage(
                input_tokens=prompt_tokens - cached_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cached_tokens,
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
        server_compaction: bool = False,  # noqa: ARG002 - Anthropic-only compaction beta
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from OpenAI.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Not supported for GPT-5 models. Raises error if provided.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.

        Yields:
            StreamChunk objects as they arrive.

        Raises:
            UnsupportedParameterError: If temperature is provided or reasoning_effort
                used with unsupported model.
        """
        if temperature is not None:
            raise UnsupportedParameterError(
                ErrorMessages.OPENAI_TEMPERATURE_NOT_SUPPORTED
            )

        effective_effort = self._resolve_reasoning_effort(model, reasoning_effort)

        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None

        stream_opts: ChatCompletionStreamOptionsParam = {"include_usage": True}
        try:
            stream = await self._client.chat.completions.create(
                model=model.value,
                messages=openai_messages,
                tools=openai_tools if openai_tools else omit,
                max_completion_tokens=max_tokens,
                stream=True,
                stream_options=stream_opts,
                reasoning_effort=effective_effort.value if effective_effort else omit,
            )

            # Track tool calls being built across chunks
            tool_call_builders: dict[int, dict[str, str]] = {}

            async for chunk in stream:
                # Handle usage-only chunk (comes after finish_reason)
                if not chunk.choices and chunk.usage:
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    cached_tokens = getattr(details, "cached_tokens", 0) or 0
                    prompt_tokens = chunk.usage.prompt_tokens

                    yield StreamChunk(
                        usage=Usage(
                            input_tokens=prompt_tokens - cached_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_read_tokens=cached_tokens,
                        ),
                        model=chunk.model,
                    )
                    continue

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Handle content
                content = delta.content if delta.content else None

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
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    model=chunk.model,
                )
        except Exception as exc:
            raise wrap_provider_error("openai", exc, model=model) from exc

    # The converter bodies live in openai_convert.py (pure move, size-gate
    # headroom); these delegates keep the client the single entry point.
    def _convert_messages(
        self, messages: list[Message]
    ) -> list[ChatCompletionMessageParam]:
        return convert_messages(messages)

    def _convert_tools(
        self, tools: list[ToolDefinition]
    ) -> list[ChatCompletionToolParam]:
        return convert_tools(tools)

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> OpenAIResponseFormat:
        return convert_response_format(response_format)

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
