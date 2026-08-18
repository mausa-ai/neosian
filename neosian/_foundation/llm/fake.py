"""Deterministic, scriptable, keyless provider (ECOSYSTEM §7).

The public surface is `neosian.fake`; this module is the implementation.
A FakeClient plays back a FakeScript turn by turn: canned responses,
scripted tool calls, assertable token counts, failure injection — the
default test tier and offline dev loop boot with zero third-party accounts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Final

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
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import FakeScriptExhaustedError
from neosian._foundation.shared.types import Model, ReasoningEffort, ResponseFormat


class StreamShape(str, Enum):
    """Which real provider's stream layout the fake reproduces.

    OPENAI: finish chunk first, then a trailing usage-only chunk.
    ANTHROPIC: a leading partial-usage chunk (input/cache, zero output),
    complete usage riding the finish chunk. Both exercise the agent's
    last-wins usage rule.
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True, slots=True)
class FakeTurn:
    """One scripted assistant turn.

    stop_reason=None derives "tool_calls" when tool calls are present,
    "stop" otherwise. When `error` is set, complete() raises it (through
    wrap_provider_error, exactly like a real provider failure) and stream()
    yields `error_after_chunks` chunks first.
    """

    content: str | None = None
    reasoning: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage(input_tokens=0, output_tokens=0)
    stop_reason: str | None = None
    error: Exception | None = None
    error_after_chunks: int = 0


@dataclass(frozen=True, slots=True)
class FakeScript:
    """An ordered playback of turns, shareable across clients.

    chunk_chars is the fixed content/reasoning slice width for streaming
    (0 means one chunk). Exhaustion raises FakeScriptExhaustedError unless
    repeat_last — a silently repeating tool-call turn would spin the agent
    loop, so repeating is opt-in.
    """

    turns: tuple[FakeTurn, ...] = ()
    stream_shape: StreamShape = StreamShape.OPENAI
    chunk_chars: int = 16
    repeat_last: bool = False


@dataclass(frozen=True, slots=True)
class FakeCall:
    """A snapshot of one client call — the assertion surface.

    `messages` is a tuple copy taken at call time: the agent mutates its
    message list in place across tool iterations, so storing the reference
    would show the final conversation on every recorded call.
    """

    model: Model
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    temperature: float | None
    response_format: ResponseFormat | None
    reasoning_effort: ReasoningEffort | None
    max_tokens: int
    cache_conversation: bool
    stream: bool


_CANNED: Final = FakeScript(
    turns=(
        FakeTurn(
            content="fake response",
            usage=Usage(input_tokens=10, output_tokens=5),
        ),
    ),
    repeat_last=True,
)


def _stop_reason(turn: FakeTurn) -> str:
    if turn.stop_reason is not None:
        return turn.stop_reason
    return "tool_calls" if turn.tool_calls else "stop"


def _split(text: str | None, width: int) -> list[str]:
    if not text:
        return []
    if width <= 0:
        return [text]
    return [text[i : i + width] for i in range(0, len(text), width)]


def _chunks(turn: FakeTurn, script: FakeScript, model: Model) -> list[StreamChunk]:
    """The full deterministic chunk sequence for a turn."""
    api_model = model.value
    parts = [
        StreamChunk(reasoning=part, model=api_model)
        for part in _split(turn.reasoning, script.chunk_chars)
    ] + [
        StreamChunk(content=part, model=api_model)
        for part in _split(turn.content, script.chunk_chars)
    ]
    finish = _stop_reason(turn)
    if script.stream_shape is StreamShape.ANTHROPIC:
        lead = StreamChunk(
            usage=Usage(
                input_tokens=turn.usage.input_tokens,
                output_tokens=0,
                cache_read_tokens=turn.usage.cache_read_tokens,
                cache_write_tokens=turn.usage.cache_write_tokens,
            ),
            model=api_model,
        )
        tail = StreamChunk(
            finish_reason=finish,
            usage=turn.usage,
            tool_calls=list(turn.tool_calls),
            model=api_model,
        )
        return [lead, *parts, tail]
    tail = StreamChunk(
        finish_reason=finish, tool_calls=list(turn.tool_calls), model=api_model
    )
    return [*parts, tail, StreamChunk(usage=turn.usage, model=api_model)]


class FakeClient(BaseLLMClient):
    """Scripted BaseLLMClient: same script in, same output out, always.

    The turn cursor lives on the client (scripts stay shareable and
    immutable); `calls` records every request; `closed` flips on close().
    """

    def __init__(self, script: FakeScript | None = None) -> None:
        self._script = script if script is not None else _CANNED
        self._index = 0
        self.calls: list[FakeCall] = []
        self.closed = False

    def _next_turn(self) -> FakeTurn:
        turns = self._script.turns
        if self._index >= len(turns):
            if self._script.repeat_last and turns:
                return turns[-1]
            raise FakeScriptExhaustedError(consumed=len(turns))
        turn = turns[self._index]
        self._index += 1
        return turn

    def _record(
        self,
        *,
        model: Model,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        temperature: float | None,
        response_format: ResponseFormat | None,
        reasoning_effort: ReasoningEffort | None,
        max_tokens: int,
        cache_conversation: bool,
        stream: bool,
    ) -> None:
        self.calls.append(
            FakeCall(
                model=model,
                messages=tuple(messages),
                tools=tuple(tools) if tools else (),
                temperature=temperature,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                max_tokens=max_tokens,
                cache_conversation=cache_conversation,
                stream=stream,
            )
        )

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
        self._record(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            cache_conversation=cache_conversation,
            stream=False,
        )
        turn = self._next_turn()
        if turn.error is not None:
            raise wrap_provider_error("fake", turn.error, model=model) from turn.error
        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=turn.content,
                reasoning=turn.reasoning,
                tool_calls=list(turn.tool_calls),
            ),
            usage=turn.usage,
            model=model.value,
            stop_reason=_stop_reason(turn),
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
        self._record(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            response_format=None,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            cache_conversation=cache_conversation,
            stream=True,
        )
        turn = self._next_turn()
        chunks = _chunks(turn, self._script, model)
        limit = turn.error_after_chunks if turn.error is not None else len(chunks)
        for chunk in chunks[:limit]:
            yield chunk
        if turn.error is not None:
            raise wrap_provider_error("fake", turn.error, model=model) from turn.error

    async def close(self) -> None:
        self.closed = True
