"""Anthropic Claude LLM client implementation."""

import copy
import logging
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic, BadRequestError

from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompactionBlock,
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
    requires_compaction_support,
)
from neosian._foundation.llm.errors import tool_arguments, wrap_provider_error
from neosian._foundation.shared.constants import (
    ErrorMessages,
    LLMDefaults,
)
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    NeosianError,
    ToolCallGenerationError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    AnyModel,
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

# Server-side compaction (N4): the beta flag and its context_management
# edit type. The response's compaction blocks must be echoed back
# verbatim — see CompactionBlock.
_COMPACT_BETA = "compact-2026-01-12"
_COMPACT_EDIT = "compact_20260112"


def _compaction_usage(usage_obj: object) -> Usage:
    """Compaction-iteration token spend on an API usage object.

    The compact beta reports summarization tokens only under
    usage.iterations — never in the top-level counts — so they are folded
    into the Usage neosian reports; hidden spend would break the
    cost-visibility promise (ledger #29's philosophy).
    """
    total = Usage(input_tokens=0, output_tokens=0)
    for iteration in getattr(usage_obj, "iterations", None) or []:
        if getattr(iteration, "type", None) == "compaction":
            total = total + Usage(
                input_tokens=getattr(iteration, "input_tokens", 0) or 0,
                output_tokens=getattr(iteration, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(iteration, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(iteration, "cache_creation_input_tokens", 0)
                or 0,
            )
    return total


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

    def __init__(
        self, api_key: str, max_retries: int = LLMDefaults.MAX_RETRIES
    ) -> None:
        """Initialize the Anthropic client.

        Args:
            api_key: Anthropic API key. Required, no implicit env var reading.
            max_retries: Transport-level retries handled by the SDK
                (429/5xx/connection errors, exponential backoff).
        """
        self._client = AsyncAnthropic(api_key=api_key, max_retries=max_retries)

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
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
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
        self._validate_content_support(
            messages, model, server_compaction=server_compaction
        )

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

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse Anthropic response into CompletionResponse.

        Args:
            response: Raw response from Anthropic API.

        Returns:
            Parsed CompletionResponse.
        """
        # Build text content, reasoning, and tool calls from content blocks.
        # Compaction blocks (server-side compaction beta) are collected in
        # provider order; when any exist, content becomes an ordered block
        # list the caller echoes back — otherwise the plain-string shape is
        # byte-identical to the flag-off path.
        text_content = ""
        reasoning_content = ""
        tool_calls: list[ToolCall] = []
        ordered_blocks: list[ContentBlock] = []
        saw_compaction = False

        for block in response.content:  # type: ignore[attr-defined]
            if block.type == "thinking":
                reasoning_content += block.thinking
            elif block.type == "redacted_thinking":
                pass  # Encrypted thinking block - cannot read content
            elif block.type == "text":
                text_content += block.text
                ordered_blocks.append(TextBlock(text=block.text))
            elif block.type == "compaction":
                saw_compaction = True
                ordered_blocks.append(
                    CompactionBlock(
                        content=getattr(block, "content", None),
                        encrypted_content=getattr(block, "encrypted_content", None),
                    )
                )
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

        content: str | list[ContentBlock] | None
        if saw_compaction:
            content = ordered_blocks
        else:
            content = text_content if text_content else None

        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=content,
                reasoning=reasoning_content if reasoning_content else None,
                tool_calls=tool_calls,
            ),
            usage=Usage(
                input_tokens=response.usage.input_tokens,  # type: ignore[attr-defined]
                output_tokens=response.usage.output_tokens,  # type: ignore[attr-defined]
                cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,  # type: ignore[attr-defined]
                cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,  # type: ignore[attr-defined]
            )
            + _compaction_usage(response.usage),  # type: ignore[attr-defined]
            model=response.model,  # type: ignore[attr-defined]
            stop_reason=getattr(response, "stop_reason", None),
        )

    async def stream(
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
        self._validate_content_support(
            messages, model, server_compaction=server_compaction
        )

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

        try:
            async with self._stream_manager(
                kwargs, server_compaction=server_compaction
            ) as stream:
                # Usage tracking: input/cache from message_start, output from message_delta
                input_tokens: int = 0
                output_tokens: int = 0
                cache_creation_tokens: int = 0
                cache_read_tokens: int = 0
                # Compaction spend rides usage.iterations; snapshots are
                # cumulative, so each sighting replaces (never adds to)
                # the previous one.
                compaction_usage = Usage(input_tokens=0, output_tokens=0)

                # Tool call accumulation state: (id, name, raw input),
                # decoded at message_stop once the stop reason is known.
                pending_tools: list[tuple[str, str, str]] = []
                current_tool_id: str | None = None
                current_tool_name: str | None = None
                current_tool_input: str = ""

                # Compaction block accumulation (compaction_delta carries
                # the FULL value — assignment, never concatenation)
                compaction_blocks: list[CompactionBlock] = []
                current_compaction: CompactionBlock | None = None

                # Real stop reason from the API (reported in message_delta)
                stop_reason: str | None = None

                # API-reported model string (reported in message_start)
                api_model: str | None = None

                async for event in stream:
                    if event.type == "message_start":
                        if hasattr(event, "message"):
                            api_model = getattr(event.message, "model", None)
                        # Input and cache tokens are reported in message_start
                        if hasattr(event, "message") and hasattr(
                            event.message, "usage"
                        ):
                            msg_usage = event.message.usage
                            input_tokens = getattr(msg_usage, "input_tokens", 0) or 0
                            cache_creation_tokens = (
                                getattr(msg_usage, "cache_creation_input_tokens", 0)
                                or 0
                            )
                            cache_read_tokens = (
                                getattr(msg_usage, "cache_read_input_tokens", 0) or 0
                            )
                            found = _compaction_usage(msg_usage)
                            if found.total_tokens:
                                compaction_usage = found
                            # Surface the partial usage immediately so consumers
                            # interrupted mid-stream (guard block, error) can meter
                            # the input/cache tokens already billed. The complete
                            # usage on the message_stop chunk supersedes this one —
                            # consumers must treat per-stream usage as last-wins.
                            yield StreamChunk(
                                usage=Usage(
                                    input_tokens=input_tokens,
                                    output_tokens=0,
                                    cache_read_tokens=cache_read_tokens,
                                    cache_write_tokens=cache_creation_tokens,
                                ),
                                model=api_model,
                            )

                    elif event.type == "content_block_start":
                        block = event.content_block
                        if getattr(block, "type", None) == "tool_use":
                            current_tool_id = block.id
                            current_tool_name = block.name
                            current_tool_input = ""
                        elif getattr(block, "type", None) == "compaction":
                            current_compaction = CompactionBlock(
                                content=getattr(block, "content", None),
                                encrypted_content=getattr(
                                    block, "encrypted_content", None
                                ),
                            )

                    elif event.type == "content_block_delta":
                        delta_type = getattr(event.delta, "type", None)
                        if delta_type == "thinking_delta":
                            yield StreamChunk(
                                reasoning=event.delta.thinking,
                                model=api_model,
                            )
                        elif delta_type == "text_delta":
                            yield StreamChunk(
                                content=event.delta.text,
                                model=api_model,
                            )
                        elif delta_type == "input_json_delta":
                            current_tool_input += event.delta.partial_json
                        elif (
                            delta_type == "compaction_delta"
                            and current_compaction is not None
                        ):
                            # The delta carries the FULL summary (the SDK
                            # accumulator assigns, never appends) — += here
                            # would duplicate content.
                            current_compaction.content = getattr(
                                event.delta, "content", None
                            )

                    elif event.type == "content_block_stop":
                        if current_tool_id is not None:
                            pending_tools.append(
                                (
                                    current_tool_id,
                                    current_tool_name or "",
                                    current_tool_input,
                                )
                            )
                            current_tool_id = None
                            current_tool_name = None
                            current_tool_input = ""
                        elif current_compaction is not None:
                            compaction_blocks.append(current_compaction)
                            current_compaction = None

                    elif event.type == "message_delta":
                        # Output tokens are reported in message_delta
                        if hasattr(event, "usage") and event.usage:
                            output_tokens = event.usage.output_tokens
                            found = _compaction_usage(event.usage)
                            if found.total_tokens:
                                compaction_usage = found
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
                            "tool_use" if pending_tools else "stop"
                        )
                        tool_calls = [
                            ToolCall(
                                id=ToolCallId(tool_id),
                                name=ToolName(name),
                                arguments=tool_arguments(
                                    "anthropic", raw, stop_reason=stop_reason
                                ),
                            )
                            for tool_id, name, raw in pending_tools
                        ]
                        yield StreamChunk(
                            finish_reason=finish_reason,
                            usage=Usage(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                cache_read_tokens=cache_read_tokens,
                                cache_write_tokens=cache_creation_tokens,
                            )
                            + compaction_usage,
                            tool_calls=tool_calls,
                            model=api_model,
                            compaction=tuple(compaction_blocks),
                        )
        except NeosianError:
            raise
        except Exception as exc:
            raise wrap_provider_error("anthropic", exc, model=model) from exc

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
                    # Text + compaction blocks (server-compaction echo),
                    # in provider order, with tool_use appended after —
                    # media on the assistant role still raises.
                    content = self._convert_assistant_blocks(msg.content)
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
                elif msg.tool_calls:
                    content = []
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
            else:
                # A CompactionBlock belongs to assistant content only —
                # never silently dropped (the media-block rule).
                raise UnsupportedContentError(
                    ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                        provider="anthropic (user role)",
                        block_type="compaction",
                    )
                )
        return result

    def _convert_assistant_blocks(
        self, blocks: list[ContentBlock]
    ) -> list[dict[str, Any]]:
        """Convert assistant content blocks — text and compaction only.

        Compaction blocks are echoed verbatim in provider order; the API
        replaces everything before the block with it. Media blocks on the
        assistant role raise, as they always have.
        """
        result: list[dict[str, Any]] = []
        for block in blocks:
            if isinstance(block, TextBlock):
                result.append({"type": "text", "text": block.text})
            elif isinstance(block, CompactionBlock):
                entry: dict[str, Any] = {
                    "type": "compaction",
                    "content": block.content,
                }
                if block.encrypted_content is not None:
                    entry["encrypted_content"] = block.encrypted_content
                result.append(entry)
            else:
                raise UnsupportedContentError(
                    ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                        provider="anthropic (assistant role)",
                        block_type="assistant-message",
                    )
                )
        return result

    def _validate_content_support(
        self,
        messages: list[Message],
        model: AnyModel,
        *,
        server_compaction: bool = False,
    ) -> None:
        """Raise if messages carry content blocks the model cannot handle.

        All currently registered Claude models support both images and
        documents; this gate future-proofs against text-only entries. The
        compaction gates are live today: Haiku 4.5 is Anthropic and
        outside the compact-2026-01-12 support set.

        Raises:
            UnsupportedContentError: If a required capability is missing.
            UnsupportedParameterError: If server_compaction is requested
                on a model outside the beta's support set.
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
        if server_compaction and not model.supports_compaction_blocks:
            raise UnsupportedParameterError(
                f"server_compaction is not supported by {model.value}"
            )
        if (
            requires_compaction_support(messages)
            and not model.supports_compaction_blocks
        ):
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_TYPE_NOT_SUPPORTED_BY_MODEL.format(
                    model=model.value, block_type="compaction"
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

        Tools carrying `native_type` become the schema-less native
        declaration ({"type", "name"} only) — no description or
        input_schema; the model's trained behavior replaces both.
        cache_control remains valid on native entries.

        Args:
            tools: Internal ToolDefinition objects.

        Returns:
            Anthropic-formatted tool definitions with cache_control on last.
        """
        result: list[dict[str, Any]] = []
        for tool in tools:
            if tool.native_type is not None:
                result.append({"type": tool.native_type, "name": tool.name})
                continue
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
