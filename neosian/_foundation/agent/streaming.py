"""SSE streaming for agent responses.

Converts agent stream chunks to Server-Sent Events format.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

from neosian._foundation.llm.base import StreamChunk, ToolCall, Usage
from neosian._foundation.tools.base import ToolResult


class SSEEventType(str, Enum):
    """SSE event types for agent streaming."""

    CONTENT = "content"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass
class SSEEvent:
    """A Server-Sent Event."""

    event: SSEEventType
    data: dict[str, Any]

    def to_sse(self) -> str:
        """Format as SSE string.

        Returns:
            SSE-formatted string with event and data lines.
        """
        return f"event: {self.event.value}\ndata: {json.dumps(self.data)}\n\n"


def content_event(content: str) -> SSEEvent:
    """Create a content SSE event."""
    return SSEEvent(event=SSEEventType.CONTENT, data={"content": content})


def tool_call_event(tool_call: ToolCall) -> SSEEvent:
    """Create a tool call SSE event."""
    return SSEEvent(
        event=SSEEventType.TOOL_CALL,
        data={
            "id": tool_call.id,
            "name": tool_call.name,
            "arguments": tool_call.arguments,
        },
    )


def tool_result_event(tool_call_id: str, result: ToolResult[Any]) -> SSEEvent:
    """Create a tool result SSE event."""
    return SSEEvent(
        event=SSEEventType.TOOL_RESULT,
        data={
            "tool_call_id": tool_call_id,
            "success": result.success,
            "data": result.data if result.success else None,
            "error": result.error if not result.success else None,
        },
    )


def error_event(error: str) -> SSEEvent:
    """Create an error SSE event."""
    return SSEEvent(event=SSEEventType.ERROR, data={"error": error})


def done_event(usage: Usage | None = None) -> SSEEvent:
    """Create a done SSE event.

    Args:
        usage: Optional token usage statistics.

    Returns:
        SSEEvent with done data, including usage if provided.
    """
    data: dict[str, Any] = {}
    if usage:
        data["usage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }
    return SSEEvent(event=SSEEventType.DONE, data=data)


def blocked_event(
    categories: list[str] | None = None,
    rationale: str | None = None,
) -> SSEEvent:
    """Create a blocked SSE event for guardrail interruption.

    Supports both classifier-only (categories) and policy (rationale) results.

    Args:
        categories: Llama Guard category codes (e.g., ["S1", "S2"]).
        rationale: Policy rationale explaining the block.

    Returns:
        SSEEvent with blocked data. Only includes non-None fields.
    """
    data: dict[str, Any] = {}
    if categories:
        data["categories"] = categories
    if rationale:
        data["rationale"] = rationale
    return SSEEvent(event=SSEEventType.BLOCKED, data=data)


async def stream_to_sse(
    stream: AsyncIterator[StreamChunk],
) -> AsyncIterator[str]:
    """Convert a stream of chunks to SSE strings.

    Args:
        stream: AsyncIterator of StreamChunk from LLM.

    Yields:
        SSE-formatted strings.

    Note:
        Usage data handling varies by provider:
        - OpenAI/Groq: Usage comes in a separate chunk after finish_reason
        - Anthropic: Usage comes with the finish_reason chunk
        We handle both by deferring the done event until we have usage or stream ends.
    """
    pending_done = False
    final_usage: Usage | None = None

    async for chunk in stream:
        # Yield content if present
        if chunk.content:
            yield content_event(chunk.content).to_sse()

        # Yield tool calls if present
        for tool_call in chunk.tool_calls:
            yield tool_call_event(tool_call).to_sse()

        # Handle finish_reason
        if chunk.finish_reason:
            if chunk.usage:
                # Anthropic: usage comes with finish_reason
                yield done_event(chunk.usage).to_sse()
            else:
                # OpenAI/Groq: usage may come in next chunk
                pending_done = True

        # Handle usage-only chunk (OpenAI/Groq pattern)
        if chunk.usage and not chunk.finish_reason and not chunk.content:
            final_usage = chunk.usage
            if pending_done:
                yield done_event(final_usage).to_sse()
                pending_done = False

    # If we have a pending done without usage, emit it now
    if pending_done:
        yield done_event(final_usage).to_sse()
