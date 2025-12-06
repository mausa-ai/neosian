"""SSE streaming for agent responses.

Converts agent stream chunks to Server-Sent Events format.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

from neosian._foundation.llm.base import StreamChunk, ToolCall
from neosian._foundation.tools.base import ToolResult


class SSEEventType(str, Enum):
    """SSE event types for agent streaming."""

    CONTENT = "content"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    DONE = "done"


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


def done_event() -> SSEEvent:
    """Create a done SSE event."""
    return SSEEvent(event=SSEEventType.DONE, data={})


async def stream_to_sse(
    stream: AsyncIterator[StreamChunk],
) -> AsyncIterator[str]:
    """Convert a stream of chunks to SSE strings.

    Args:
        stream: AsyncIterator of StreamChunk from LLM.

    Yields:
        SSE-formatted strings.
    """
    async for chunk in stream:
        # Yield content if present
        if chunk.content:
            yield content_event(chunk.content).to_sse()

        # Yield tool calls if present
        for tool_call in chunk.tool_calls:
            yield tool_call_event(tool_call).to_sse()

        # Yield done event on finish
        if chunk.finish_reason:
            yield done_event().to_sse()
