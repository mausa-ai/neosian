"""Tests for SSE streaming."""

import json
from collections.abc import AsyncIterator

import pytest

from neosian._foundation.agent.streaming import (
    SSEEvent,
    SSEEventType,
    content_event,
    done_event,
    error_event,
    stream_to_sse,
    tool_call_event,
    tool_result_event,
)
from neosian._foundation.llm.base import StreamChunk, ToolCall
from neosian._foundation.shared.types import ToolCallId, ToolName
from neosian._foundation.tools.base import ToolResult


@pytest.mark.unit
class TestSSEEvent:
    """Test SSEEvent dataclass."""

    def test_to_sse_format(self) -> None:
        """SSEEvent should format correctly."""
        event = SSEEvent(
            event=SSEEventType.CONTENT,
            data={"content": "Hello"},
        )
        result = event.to_sse()

        assert result.startswith("event: content\n")
        assert "data: " in result
        assert result.endswith("\n\n")

        # Parse the data line
        lines = result.strip().split("\n")
        data_line = lines[1]
        data_json = data_line.replace("data: ", "")
        parsed = json.loads(data_json)
        assert parsed["content"] == "Hello"


@pytest.mark.unit
class TestEventFactories:
    """Test event factory functions."""

    def test_content_event(self) -> None:
        """content_event should create content SSE event."""
        event = content_event("Hello world")

        assert event.event == SSEEventType.CONTENT
        assert event.data["content"] == "Hello world"

    def test_tool_call_event(self) -> None:
        """tool_call_event should create tool call SSE event."""
        tool_call = ToolCall(
            id=ToolCallId("call_123"),
            name=ToolName("search"),
            arguments={"query": "test"},
        )
        event = tool_call_event(tool_call)

        assert event.event == SSEEventType.TOOL_CALL
        assert event.data["id"] == "call_123"
        assert event.data["name"] == "search"
        assert event.data["arguments"] == {"query": "test"}

    def test_tool_result_event_success(self) -> None:
        """tool_result_event should create success result event."""
        result: ToolResult[str] = ToolResult.ok("found it")
        event = tool_result_event("call_123", result)

        assert event.event == SSEEventType.TOOL_RESULT
        assert event.data["tool_call_id"] == "call_123"
        assert event.data["success"] is True
        assert event.data["data"] == "found it"
        assert event.data["error"] is None

    def test_tool_result_event_failure(self) -> None:
        """tool_result_event should create failure result event."""
        result: ToolResult[str] = ToolResult.fail("something went wrong")
        event = tool_result_event("call_123", result)

        assert event.event == SSEEventType.TOOL_RESULT
        assert event.data["tool_call_id"] == "call_123"
        assert event.data["success"] is False
        assert event.data["data"] is None
        assert event.data["error"] == "something went wrong"

    def test_error_event(self) -> None:
        """error_event should create error SSE event."""
        event = error_event("Connection failed")

        assert event.event == SSEEventType.ERROR
        assert event.data["error"] == "Connection failed"

    def test_done_event(self) -> None:
        """done_event should create done SSE event."""
        event = done_event()

        assert event.event == SSEEventType.DONE
        assert event.data == {}


@pytest.mark.unit
class TestStreamToSSE:
    """Test stream_to_sse converter."""

    @pytest.mark.asyncio
    async def test_content_chunks(self) -> None:
        """stream_to_sse should convert content chunks."""

        async def mock_stream() -> "AsyncIterator[StreamChunk]":
            yield StreamChunk(content="Hello ")
            yield StreamChunk(content="world")
            yield StreamChunk(finish_reason="stop")

        events = []
        async for sse in stream_to_sse(mock_stream()):
            events.append(sse)

        assert len(events) == 3
        assert "content" in events[0]
        assert "Hello " in events[0]
        assert "world" in events[1]
        assert "done" in events[2]

    @pytest.mark.asyncio
    async def test_tool_call_chunks(self) -> None:
        """stream_to_sse should convert tool call chunks."""
        tool_call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("search"),
            arguments={"query": "test"},
        )

        async def mock_stream() -> "AsyncIterator[StreamChunk]":
            yield StreamChunk(tool_calls=[tool_call], finish_reason="tool_calls")

        events = []
        async for sse in stream_to_sse(mock_stream()):
            events.append(sse)

        assert len(events) == 2  # tool_call + done
        assert "tool_call" in events[0]
        assert "done" in events[1]

    @pytest.mark.asyncio
    async def test_empty_chunks_skipped(self) -> None:
        """stream_to_sse should skip empty chunks."""

        async def mock_stream() -> "AsyncIterator[StreamChunk]":
            yield StreamChunk()  # Empty chunk
            yield StreamChunk(content="Hello")
            yield StreamChunk()  # Another empty chunk
            yield StreamChunk(finish_reason="stop")

        events = []
        async for sse in stream_to_sse(mock_stream()):
            events.append(sse)

        assert len(events) == 2  # content + done
