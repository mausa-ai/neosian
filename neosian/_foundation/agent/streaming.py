"""SSE streaming for agent responses.

Converts agent stream chunks to Server-Sent Events format.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

from neosian._foundation.llm.base import StreamChunk, ToolCall, Usage
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.tools.base import ToolResult


class SSEEventType(str, Enum):
    """SSE event types for agent streaming."""

    CONTENT = "content"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    DONE = "done"
    BLOCKED = "blocked"
    HEARTBEAT = "heartbeat"


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
        return f"event: {self.event.value}\ndata: {safe_json_dumps(self.data, 'sse_event.data')}\n\n"


def content_event(content: str) -> SSEEvent:
    """Create a content SSE event."""
    return SSEEvent(event=SSEEventType.CONTENT, data={"content": content})


def reasoning_event(reasoning: str) -> SSEEvent:
    """Create a reasoning SSE event for model thinking/reasoning content."""
    return SSEEvent(event=SSEEventType.REASONING, data={"reasoning": reasoning})


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


def _usage_payload(usage: Usage) -> dict[str, Any]:
    """Serialize Usage to the SSE data payload shared by done/blocked/error."""
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "total_tokens": usage.total_tokens,
    }


def error_event(error: str, usage: Usage | None = None) -> SSEEvent:
    """Create an error SSE event.

    Args:
        error: Error description.
        usage: Best-effort token usage billed before the failure
            (same keys as the done event).
    """
    data: dict[str, Any] = {"error": error}
    if usage:
        data["usage"] = _usage_payload(usage)
    return SSEEvent(event=SSEEventType.ERROR, data=data)


def done_event(usage: Usage | None = None) -> SSEEvent:
    """Create a done SSE event.

    Args:
        usage: Optional token usage statistics.

    Returns:
        SSEEvent with done data, including usage if provided.
    """
    data: dict[str, Any] = {}
    if usage:
        data["usage"] = _usage_payload(usage)
    return SSEEvent(event=SSEEventType.DONE, data=data)


def blocked_event(
    rationale: str | None = None,
    usage: Usage | None = None,
) -> SSEEvent:
    """Create a blocked SSE event for guardrail interruption.

    Args:
        rationale: Policy rationale explaining the block.
        usage: Best-effort token usage billed before the block
            (same keys as the done event).

    Returns:
        SSEEvent with blocked data. Only includes non-None fields.
    """
    data: dict[str, Any] = {}
    if rationale:
        data["rationale"] = rationale
    if usage:
        data["usage"] = _usage_payload(usage)
    return SSEEvent(event=SSEEventType.BLOCKED, data=data)


def heartbeat_event(tool_call_id: str, elapsed_seconds: float) -> SSEEvent:
    """Create a heartbeat SSE event during long-running tool execution.

    Heartbeats keep SSE connections alive and prevent frontend clients
    from timing out and reconnecting during long tool executions.

    Args:
        tool_call_id: ID of the tool call currently executing.
        elapsed_seconds: Time elapsed since tool execution started.

    Returns:
        SSEEvent with heartbeat data.
    """
    return SSEEvent(
        event=SSEEventType.HEARTBEAT,
        data={
            "tool_call_id": tool_call_id,
            "elapsed_seconds": elapsed_seconds,
        },
    )


class SSEEventEmitter:
    """Emits SSE events with automatic sequence metadata.

    Each streaming session should create a new emitter instance.
    The emitter tracks sequence numbers (starting at 1) to ensure
    correct event ordering within a single streaming session.

    Note: Sequence starts at 1 because the frontend assigns sequence 0
    to the user message. SSE events follow the user message.

    Usage:
        emitter = SSEEventEmitter()
        yield emitter.emit(content_event("Hello"))
        yield emitter.emit(content_event(" world"))
        yield emitter.emit(done_event())
        # Events will have sequence 1, 2, 3
    """

    def __init__(self) -> None:
        """Initialize emitter with sequence starting at 1.

        Note: Sequence starts at 1 because the frontend assigns sequence 0
        to the user message. SSE events (assistant responses, tool calls,
        tool results) follow the user message.
        """
        self._sequence = 1

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
        # Yield reasoning if present (comes before content for reasoning models)
        if chunk.reasoning:
            yield emitter.emit(reasoning_event(chunk.reasoning))

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
