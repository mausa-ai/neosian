"""Anthropic Claude LLM client implementation."""

import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from anthropic import NOT_GIVEN, AsyncAnthropic, BadRequestError
from anthropic.types import Message as AnthropicMessage
from anthropic.types.beta import BetaMessage

from neosian._foundation.llm.anthropic_convert import (
    apply_cache_control,
    convert_messages,
    validate_content_support,
)
from neosian._foundation.llm.anthropic_stream import iter_chunks, parse_message
from neosian._foundation.llm.anthropic_tools import (
    convert_response_format,
    convert_tool_choice,
    convert_tools,
)
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    StreamChunk,
    ToolDefinition,
)
from neosian._foundation.llm.errors import wrap_provider_error
from neosian._foundation.shared.constants import (
    ErrorMessages,
    LLMDefaults,
)
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    NeosianError,
    ToolCallGenerationError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    AnyModel,
    CacheTtl,
    Model,
    ReasoningEffort,
    ResponseFormat,
    ToolChoice,
)

logger = logging.getLogger(__name__)

# Server-side compaction (N4): the beta flag and its context_management
# edit type. The response's compaction blocks must be echoed back
# verbatim — see CompactionBlock.
_COMPACT_BETA = "compact-2026-01-12"
_COMPACT_EDIT = "compact_20260112"


_SAMPLING_REJECTED_MODELS: frozenset[Model] = frozenset({Model.CLAUDE_OPUS_5})


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude LLM client.

    Uses the Anthropic SDK. Includes automatic retry when tool call
    generation fails.
    """

    def __init__(
        self,
        api_key: str,
        max_retries: int = LLMDefaults.MAX_RETRIES,
        timeout: float | None = None,
    ) -> None:
        """Initialize the Anthropic client.

        Args:
            api_key: Anthropic API key. Required, no implicit env var reading.
            max_retries: Transport-level retries handled by the SDK
                (429/5xx/connection errors, exponential backoff).
            timeout: Per-request deadline in seconds; None keeps the SDK's.
        """
        self._client = AsyncAnthropic(
            api_key=api_key,
            max_retries=max_retries,
            timeout=NOT_GIVEN if timeout is None else timeout,
        )

    def _validate_temperature_support(
        self, model: AnyModel, temperature: float | None
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
        self, model: AnyModel, reasoning_effort: ReasoningEffort | None
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
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        tool_choice: ToolChoice | None = None,
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
        cache_ttl: CacheTtl = "5m",
        server_compaction: bool = False,
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
            server_compaction: Opt into Anthropic's server-side compaction
                beta. Responses may carry a CompactionBlock the caller must
                echo back verbatim; its summarization spend is folded into
                the reported Usage.

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
        validate_content_support(messages, model, server_compaction=server_compaction)

        effective_effort = self._resolve_effort(model, reasoning_effort)

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools, cache_ttl) if tools else None

        # Apply prompt caching breakpoints
        cached_system, anthropic_messages = self._apply_cache_control(
            system_prompt,
            anthropic_messages,
            cache_last_message=cache_conversation,
            ttl=cache_ttl,
        )

        # Only send temperature when explicitly requested: newer Claude
        # models (e.g. Sonnet 5) reject non-default sampling parameters
        # with a 400, so the API default must apply when unset. It rides
        # extra_body: SDK 1.x dropped the keyword, the wire still takes it.
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
                    kwargs["extra_body"] = {"temperature": current_temp}

                if cached_system:
                    kwargs["system"] = cached_system

                if anthropic_tools:
                    kwargs["tools"] = anthropic_tools
                    # A choice without tools is a 400: the wire only takes
                    # one beside a tool list.
                    if tool_choice is not None:
                        kwargs["tool_choice"] = convert_tool_choice(tool_choice)

                if response_format:
                    # output_config may already exist from reasoning effort above;
                    # merge rather than overwrite.
                    output_config = kwargs.setdefault("output_config", {})
                    output_config["format"] = self._convert_response_format(
                        response_format
                    )
                # Stream internally: the SDK refuses non-streaming requests
                # it estimates may exceed ~10 minutes (large max_tokens).
                async with self._stream_manager(
                    kwargs, server_compaction=server_compaction
                ) as stream:
                    response = await stream.get_final_message()

                return self._parse_response(response)

            except BadRequestError as e:
                # An overflow is classified before the tool retry (LL-4).
                wrapped = wrap_provider_error("anthropic", e, model=model)
                if isinstance(wrapped, ContextWindowExceededError):
                    raise wrapped from e
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
                raise wrapped from e
            except Exception as exc:
                raise wrap_provider_error("anthropic", exc, model=model) from exc

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

    def _stream_manager(
        self, kwargs: dict[str, Any], *, server_compaction: bool
    ) -> Any:
        """The messages.stream context manager — GA or compaction-beta namespace.

        Returns Any: mypy joins the two namespaces' stream managers into an
        unusable type, and the event handling is duck-typed regardless. The
        beta stream accepts every GA kwarg, so nothing else changes.
        """
        if server_compaction:
            kwargs = {
                **kwargs,
                "betas": [_COMPACT_BETA],
                "context_management": {"edits": [{"type": _COMPACT_EDIT}]},
            }
            return self._client.beta.messages.stream(**kwargs)
        return self._client.messages.stream(**kwargs)

    async def stream(
        self,
        messages: list[Message],
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        tool_choice: ToolChoice | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
        cache_ttl: CacheTtl = "5m",
        server_compaction: bool = False,
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
            server_compaction: Opt into Anthropic's server-side compaction
                beta; compaction blocks arrive on the terminal chunk's
                `compaction` field and their spend folds into its usage.

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
        validate_content_support(messages, model, server_compaction=server_compaction)

        effective_effort = self._resolve_effort(model, reasoning_effort)

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools, cache_ttl) if tools else None

        # Apply prompt caching breakpoints
        cached_system, anthropic_messages = self._apply_cache_control(
            system_prompt,
            anthropic_messages,
            cache_last_message=cache_conversation,
            ttl=cache_ttl,
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }

        # Thinking mode: add adaptive thinking + effort, omit temperature.
        # Temperature is only sent when explicitly requested: newer Claude
        # models (e.g. Sonnet 5) reject non-default sampling parameters.
        # It rides extra_body: SDK 1.x dropped the keyword, the wire still
        # takes it.
        if effective_effort is not None:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effective_effort.value}
        elif temperature is not None:
            kwargs["extra_body"] = {"temperature": temperature}

        if cached_system:
            kwargs["system"] = cached_system

        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
            if tool_choice is not None:
                kwargs["tool_choice"] = convert_tool_choice(tool_choice)
        try:
            async with (
                self._stream_manager(
                    kwargs, server_compaction=server_compaction
                ) as stream,
                aclosing(iter_chunks(stream)) as chunks,
            ):
                async for chunk in chunks:
                    yield chunk
        except NeosianError:
            raise
        except Exception as exc:
            raise wrap_provider_error("anthropic", exc, model=model) from exc

    # The converter and reader bodies live in anthropic_convert.py and
    # anthropic_stream.py (pure moves, size-gate headroom); these delegates
    # keep the client the single entry point.
    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        return convert_messages(messages)

    def _convert_tools(
        self, tools: list[ToolDefinition], ttl: CacheTtl = "5m"
    ) -> list[dict[str, Any]]:
        return convert_tools(tools, ttl)

    def _apply_cache_control(
        self,
        system_prompt: str | None,
        anthropic_messages: list[dict[str, Any]],
        cache_last_message: bool = True,
        ttl: CacheTtl = "5m",
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
        return apply_cache_control(
            system_prompt,
            anthropic_messages,
            cache_last_message=cache_last_message,
            ttl=ttl,
        )

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> dict[str, object]:
        return convert_response_format(response_format)

    def _parse_response(
        self, response: AnthropicMessage | BetaMessage
    ) -> CompletionResponse:
        return parse_message(response)

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
