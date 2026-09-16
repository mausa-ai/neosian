"""The Responses wire's streamed event reader (DESIGN §31.5).

The twin of `openai_stream.py` for the second wire: the client owns the
request, the reader owns the event grammar. Text and refusal deltas are
content, reasoning-summary deltas are `reasoning`, a function call is
opened when its item is added and its arguments arrive as fragments
(#226); the terminal event releases the finished calls, the usage, the
finish reason and the reasoning items on the channel — which the agent
loops already lift onto the assembled message (#172).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem

from neosian._foundation.llm.base import StreamChunk, ToolCall, ToolCallFragment
from neosian._foundation.llm.errors import tool_arguments
from neosian._foundation.llm.openai_responses import (
    finish_reason,
    reasoning_channel,
    reasoning_item,
    usage_of,
)
from neosian._foundation.shared.exceptions import ProviderError
from neosian._foundation.shared.types import OpenAICompatible, ToolCallId, ToolName

_TERMINAL = frozenset({"response.completed", "response.incomplete"})


async def iter_events(
    stream: AsyncIterator[Any], door: OpenAICompatible
) -> AsyncGenerator[StreamChunk]:
    """The wire's events as `StreamChunk`s, tool calls assembled on the way."""
    calls: dict[int, dict[str, str]] = {}
    items: list[dict[str, Any]] = []
    model: str | None = None
    refused = False

    async for event in stream:
        kind = event.type
        if kind == "response.created":
            model = event.response.model
        elif kind == "response.output_text.delta":
            yield StreamChunk(content=event.delta, model=model)
        elif kind == "response.refusal.delta":
            refused = True
            yield StreamChunk(content=event.delta, model=model)
        elif kind == "response.reasoning_summary_text.delta":
            yield StreamChunk(reasoning=event.delta, model=model)
        elif kind == "response.output_item.added":
            if isinstance(event.item, ResponseFunctionToolCall):
                calls[event.output_index] = _builder(event.item)
        elif kind == "response.function_call_arguments.delta":
            builder = calls[event.output_index]
            builder["arguments"] += event.delta
            yield StreamChunk(
                tool_call_fragments=(
                    ToolCallFragment(
                        id=ToolCallId(builder["id"]),
                        name=ToolName(builder["name"]),
                        fragment=event.delta,
                    ),
                ),
                model=model,
            )
        elif kind == "response.output_item.done":
            item = event.item
            if isinstance(item, ResponseFunctionToolCall):
                # The finished item's text is authoritative over the deltas.
                calls.setdefault(event.output_index, _builder(item))[
                    "arguments"
                ] = item.arguments
            elif isinstance(item, ResponseReasoningItem) and (
                (kept := reasoning_item(item)) is not None
            ):
                items.append(kept)
        elif kind in _TERMINAL:
            response = event.response
            finish = finish_reason(response, refused=refused, calls=bool(calls))
            yield StreamChunk(
                tool_calls=[
                    ToolCall(
                        id=ToolCallId(builder["id"]),
                        name=ToolName(builder["name"]),
                        arguments=tool_arguments(
                            door.name, builder["arguments"], stop_reason=finish
                        ),
                    )
                    for builder in calls.values()
                ],
                finish_reason=finish,
                usage=usage_of(response.usage) if response.usage else None,
                model=response.model,
                extra=reasoning_channel(items),
            )
        elif kind == "response.failed":
            error = event.response.error
            raise ProviderError(
                door.name, error.message if error else "response failed"
            )
        elif kind == "error":
            raise ProviderError(door.name, str(event.message))


def _builder(item: ResponseFunctionToolCall) -> dict[str, str]:
    return {"id": item.call_id, "name": item.name, "arguments": item.arguments or ""}
