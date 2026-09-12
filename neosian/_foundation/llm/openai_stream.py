"""The OpenAI wire's streamed chunk reader.

Split out of `openai.py` at NC9 for the reason `anthropic_stream.py` was
split out of `anthropic.py`: the client owns the request, the reader owns
the event grammar, and only the reader grows when a wire learns to say
something new. Door-keyed rather than self-keyed — everything it needs is
the dialect row.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from neosian._foundation.llm.base import (
    StreamChunk,
    ToolCall,
    ToolCallFragment,
)
from neosian._foundation.llm.errors import tool_arguments
from neosian._foundation.llm.openai_convert import extra_of, refusal_of, usage_of
from neosian._foundation.shared.exceptions import ProviderError
from neosian._foundation.shared.types import (
    OpenAICompatible,
    ToolCallId,
    ToolName,
)


def reasoning_of(part: object, door: OpenAICompatible) -> str | None:
    """The door's reasoning field off a message or delta, if it carries one."""
    if door.reasoning_field is None:
        return None
    value = getattr(part, door.reasoning_field, None)
    return value if isinstance(value, str) and value else None


async def iter_chunks(
    stream: AsyncIterator[Any], door: OpenAICompatible
) -> AsyncGenerator[StreamChunk]:
    """The wire's chunks as `StreamChunk`s, tool calls assembled on the way.

    Arguments arrive in pieces and are both buffered and forwarded: the
    buffer decodes the finished call at the terminal chunk, the forwarded
    `ToolCallFragment` lets a consumer read the arguments as they land
    (#226). A fragment is never parsed — it is a slice of JSON text.
    """
    # Track tool calls being built across chunks
    tool_call_builders: dict[int, dict[str, str]] = {}
    tool_call_extras: dict[int, dict[str, Any]] = {}
    refused = False

    async for chunk in stream:
        # An in-band error frame ends the stream loudly (#218).
        if (extra := extra_of(chunk)) and "error" in extra:
            raise ProviderError(door.name, str(extra["error"]))
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
        fragments: list[ToolCallFragment] = []
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_call_builders:
                    tool_call_builders[idx] = {"id": "", "name": "", "arguments": ""}

                if tc.id:
                    tool_call_builders[idx]["id"] = tc.id
                if extra := extra_of(tc):
                    tool_call_extras.setdefault(idx, {}).update(extra)
                if tc.function:
                    if tc.function.name:
                        tool_call_builders[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_call_builders[idx]["arguments"] += tc.function.arguments
                        # The id was announced on the first delta and is
                        # held in the builder; a later fragment carries it
                        # even though the wire stopped repeating it.
                        fragments.append(
                            ToolCallFragment(
                                id=ToolCallId(tool_call_builders[idx]["id"]),
                                name=ToolName(tool_call_builders[idx]["name"]),
                                fragment=tc.function.arguments,
                            )
                        )

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
                            door.name, builder["arguments"], stop_reason=finish_reason
                        ),
                        extra=tool_call_extras.get(idx),
                    )
                )

        yield StreamChunk(
            content=content,
            reasoning=reasoning_of(delta, door),
            tool_calls=tool_calls,
            tool_call_fragments=tuple(fragments),
            finish_reason=finish_reason,
            usage=usage,
            model=chunk.model,
        )
