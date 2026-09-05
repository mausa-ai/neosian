"""The OpenAI-compatible client, and OpenAI's own instance of it (DESIGN §19)."""

import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Final

from openai import NOT_GIVEN, AsyncOpenAI, BadRequestError, omit
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
from neosian._foundation.llm.errors import tool_arguments, wrap_provider_error
from neosian._foundation.llm.openai_convert import (
    convert_messages,
    convert_response_format,
    convert_tools,
    extra_of,
    refusal_of,
    usage_of,
)
from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    ToolCallGenerationError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    AnyModel,
    Model,
    OpenAICompatible,
    ReasoningEffort,
    ResponseFormat,
    ToolCallId,
    ToolName,
)

logger = logging.getLogger(__name__)

# OpenAI's own door: the SDK's endpoint (or OPENAI_BASE_URL), OpenAI's dialect.
OPENAI_DOOR: Final = OpenAICompatible(name="openai", api_key_env="OPENAI_API_KEY")


class OpenAICompatibleClient(BaseLLMClient):
    """A client for any OpenAI-compatible endpoint; quirks read off its door.

    Retries tool-call generation failures before raising
    ToolCallGenerationError.
    """

    def __init__(
        self,
        api_key: str,
        *,
        door: OpenAICompatible,
        max_retries: int = LLMDefaults.MAX_RETRIES,
        timeout: float | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            api_key: The door's API key. Required, no implicit env var reading.
            door: Where requests go and which dialect the wire speaks.
            max_retries: Transport-level retries handled by the SDK
                (429/5xx/connection errors, exponential backoff).
            timeout: Per-request deadline in seconds; None keeps the SDK's.
        """
        self._door = door
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=door.base_url,
            max_retries=max_retries,
            timeout=NOT_GIVEN if timeout is None else timeout,
        )

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
        """Send a completion request.

        Automatically retries if tool call generation fails. After max
        retries, raises ToolCallGenerationError.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Refused unless the door accepts the parameter.
            response_format: Optional structured output configuration.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.

        Returns:
            CompletionResponse with the model's response.

        Raises:
            UnsupportedParameterError: If temperature is provided on a door
                without it, or reasoning_effort used with an unsupported model.
            ToolCallGenerationError: If tool call generation fails after retries.
        """
        self._check_temperature(temperature)
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
                    temperature=temperature if temperature is not None else omit,
                    response_format=(
                        openai_response_format if openai_response_format else omit
                    ),
                    reasoning_effort=(
                        effective_effort.value if effective_effort else omit
                    ),
                )
                return self._parse_response(response)

            except BadRequestError as e:
                # An overflow is classified before the tool retry (LL-4).
                wrapped = wrap_provider_error(self._door.name, e, model=model)
                if isinstance(wrapped, ContextWindowExceededError):
                    raise wrapped from e
                if self._is_tool_call_error(e) and tools is not None:
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
                        continue
                    raise ToolCallGenerationError(
                        retries=LLMDefaults.MAX_TOOL_CALL_RETRIES
                    ) from e
                raise wrapped from e
            except Exception as exc:
                raise wrap_provider_error(self._door.name, exc, model=model) from exc

        # Should not reach here, but satisfy type checker
        raise ToolCallGenerationError(retries=LLMDefaults.MAX_TOOL_CALL_RETRIES)

    def _check_temperature(self, temperature: float | None) -> None:
        if temperature is None or self._door.temperature:
            return
        if self._door is OPENAI_DOOR:
            raise UnsupportedParameterError(
                ErrorMessages.OPENAI_TEMPERATURE_NOT_SUPPORTED
            )
        raise UnsupportedParameterError(
            f"temperature is not supported on the {self._door.name!r} door; "
            "declare OpenAICompatible(temperature=True) if the endpoint accepts it"
        )

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
        self, model: AnyModel, reasoning_effort: ReasoningEffort | None
    ) -> ReasoningEffort | None:
        """Validate and normalize reasoning_effort for the door's dialect.

        Handles:
        - Validation: raises UnsupportedParameterError for non-reasoning models.
        - A door without the parameter: dropped with a warning.
        - MAX downgrade: the dialect has no MAX, downgrade to HIGH.
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

        if not self._door.reasoning_effort:
            logger.warning(
                "reasoning_effort=%s dropped: the %r door has no such parameter",
                reasoning_effort.value,
                self._door.name,
            )
            return None

        effective_effort = reasoning_effort

        # The dialect has no MAX - downgrade to HIGH with warning
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

    def _reasoning_of(self, part: object) -> str | None:
        """The door's reasoning field off a message or delta, if it carries one."""
        if self._door.reasoning_field is None:
            return None
        value = getattr(part, self._door.reasoning_field, None)
        return value if isinstance(value, str) and value else None

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse the response into CompletionResponse.

        Args:
            response: Raw response from the API.

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
                tool_calls.append(
                    ToolCall(
                        id=ToolCallId(tc.id),
                        name=ToolName(tc.function.name),
                        arguments=tool_arguments(
                            self._door.name,
                            tc.function.arguments,
                            stop_reason=choice.finish_reason,
                        ),
                        extra=extra_of(tc),
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

        refusal = refusal_of(response_message)
        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=response_message.content or refusal,
                tool_calls=tool_calls,
                reasoning=self._reasoning_of(response_message),
            ),
            usage=Usage(
                input_tokens=prompt_tokens - cached_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cached_tokens,
            ),
            model=response.model,  # type: ignore[attr-defined]
            stop_reason=(
                "refusal" if refusal else getattr(choice, "finish_reason", None)
            ),
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
        """Stream a completion request.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Refused unless the door accepts the parameter.
            reasoning_effort: Optional reasoning effort level for supported models.
            max_tokens: Maximum output tokens for this request.

        Yields:
            StreamChunk objects as they arrive.

        Raises:
            UnsupportedParameterError: If temperature is provided on a door
                without it, or reasoning_effort used with an unsupported model.
        """
        self._check_temperature(temperature)
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
                temperature=temperature if temperature is not None else omit,
                stream=True,
                stream_options=stream_opts,
                reasoning_effort=effective_effort.value if effective_effort else omit,
            )

            # Track tool calls being built across chunks
            tool_call_builders: dict[int, dict[str, str]] = {}
            tool_call_extras: dict[int, dict[str, Any]] = {}
            refused = False

            async for chunk in stream:
                # Usage rides whichever chunk carries it — OpenAI's trailing
                # choices-empty chunk, or a door's final content chunk (LL-12).
                usage = usage_of(chunk.usage) if chunk.usage else None
                if not chunk.choices:
                    if usage:
                        yield StreamChunk(usage=usage, model=chunk.model)
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Handle content
                refusal = refusal_of(delta)
                refused = refused or refusal is not None
                content = delta.content or refusal

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
                        if extra := extra_of(tc):
                            tool_call_extras.setdefault(idx, {}).update(extra)
                        if tc.function:
                            if tc.function.name:
                                tool_call_builders[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_call_builders[idx][
                                    "arguments"
                                ] += tc.function.arguments

                # On finish, yield completed tool calls
                # A refusal arrives in deltas; the terminal chunk names it.
                finish_reason: str | None = choice.finish_reason
                if refused and finish_reason:
                    finish_reason = "refusal"
                # Any terminal finish releases the accumulated calls: Gemini
                # ends a streamed tool turn with "stop" (DESIGN §19.7), and
                # the agent loop keys on the calls' presence, not the reason.
                if finish_reason and tool_call_builders:
                    for idx, builder in tool_call_builders.items():
                        tool_calls.append(
                            ToolCall(
                                id=ToolCallId(builder["id"]),
                                name=ToolName(builder["name"]),
                                arguments=tool_arguments(
                                    self._door.name,
                                    builder["arguments"],
                                    stop_reason=finish_reason,
                                ),
                                extra=tool_call_extras.get(idx),
                            )
                        )

                yield StreamChunk(
                    content=content,
                    reasoning=self._reasoning_of(delta),
                    tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    usage=usage,
                    model=chunk.model,
                )
        except Exception as exc:
            raise wrap_provider_error(self._door.name, exc, model=model) from exc

    # The converter bodies live in openai_convert.py (pure move, size-gate
    # headroom); these delegates keep the client the single entry point.
    def _convert_messages(
        self, messages: list[Message]
    ) -> list[ChatCompletionMessageParam]:
        return convert_messages(messages)

    def _convert_tools(
        self, tools: list[ToolDefinition]
    ) -> list[ChatCompletionToolParam]:
        return convert_tools(tools, strict_schemas=self._door.strict_schemas)

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> OpenAIResponseFormat:
        if not self._door.strict_schemas:
            response_format = replace(response_format, strict=False)
        return convert_response_format(response_format)

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()


class OpenAIClient(OpenAICompatibleClient):
    """OpenAI's own client: the generic wire on OpenAI's door."""

    def __init__(
        self,
        api_key: str,
        max_retries: int = LLMDefaults.MAX_RETRIES,
        timeout: float | None = None,
    ) -> None:
        super().__init__(
            api_key, door=OPENAI_DOOR, max_retries=max_retries, timeout=timeout
        )
