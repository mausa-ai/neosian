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


class SSEEventEmitter:
    """Emits SSE events with automatic sequence metadata.

    Each streaming session should create a new emitter instance.
    The emitter tracks sequence numbers (starting at 0) to ensure
    correct event ordering within a single streaming session.

    Usage:
        emitter = SSEEventEmitter()
        yield emitter.emit(content_event("Hello"))
        yield emitter.emit(content_event(" world"))
        yield emitter.emit(done_event())
        # Events will have sequence 0, 1, 2
    """

    def __init__(self) -> None:
        """Initialize emitter with sequence starting at 0."""
        self._sequence = 0

    def emit(self, event: SSEEvent) -> str:
        """Emit an SSE event with sequence metadata.

        Adds sequence number to the event data for ordering,
        then formats as SSE string.

        Args:
            event: The SSE event to emit.

        Returns:
            SSE-formatted string with sequence included in data payload.
        """
        event.data["sequence"] = self._sequence
        self._sequence += 1
        return event.to_sse()


async def stream_to_sse(
    stream: AsyncIterator[StreamChunk],
    emitter: SSEEventEmitter | None = None,
) -> AsyncIterator[str]:
    """Convert a stream of chunks to SSE strings.

    Args:
        stream: AsyncIterator of StreamChunk from LLM.
        emitter: Optional emitter for metadata. If None, creates a new one.

    Yields:
        SSE-formatted strings with sequence metadata.

    Note:
        Usage data handling varies by provider:
        - OpenAI/Groq: Usage comes in a separate chunk after finish_reason
        - Anthropic: Usage comes with the finish_reason chunk
        We handle both by deferring the done event until we have usage or stream ends.
    """
    if emitter is None:
        emitter = SSEEventEmitter()

    pending_done = False
    final_usage: Usage | None = None

    async for chunk in stream:
        # Yield content if present
        if chunk.content:
            yield emitter.emit(content_event(chunk.content))

        # Yield tool calls if present
        for tool_call in chunk.tool_calls:
            yield emitter.emit(tool_call_event(tool_call))

        # Handle finish_reason
        if chunk.finish_reason:
            if chunk.usage:
                # Anthropic: usage comes with finish_reason
                yield emitter.emit(done_event(chunk.usage))
            else:
                # OpenAI/Groq: usage may come in next chunk
                pending_done = True

        # Handle usage-only chunk (OpenAI/Groq pattern)
        if chunk.usage and not chunk.finish_reason and not chunk.content:
            final_usage = chunk.usage
            if pending_done:
                yield emitter.emit(done_event(final_usage))
                pending_done = False

    # If we have a pending done without usage, emit it now
    if pending_done:
        yield emitter.emit(done_event(final_usage))
