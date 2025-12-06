"""Tests for LLM base protocol."""

import pytest

from neosian._foundation.llm.base import (
    CompletionResponse,
    Message,
    Role,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)
from neosian._foundation.shared.types import ModelId, ToolCallId, ToolName


@pytest.mark.unit
class TestRole:
    """Test Role enum."""

    def test_role_values(self) -> None:
        """Role should have expected values."""
        assert Role.SYSTEM.value == "system"
        assert Role.USER.value == "user"
        assert Role.ASSISTANT.value == "assistant"
        assert Role.TOOL.value == "tool"


@pytest.mark.unit
class TestMessage:
    """Test Message dataclass."""

    def test_simple_message(self) -> None:
        """Message with just role and content."""
        msg = Message(role=Role.USER, content="Hello")
        assert msg.role == Role.USER
        assert msg.content == "Hello"
        assert msg.tool_calls == []
        assert msg.tool_call_id is None

    def test_message_with_tool_calls(self) -> None:
        """Message with tool calls."""
        tool_call = ToolCall(
            id=ToolCallId("call_123"),
            name=ToolName("search"),
            arguments={"query": "test"},
        )
        msg = Message(role=Role.ASSISTANT, tool_calls=[tool_call])
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "search"

    def test_tool_result_message(self) -> None:
        """Message representing a tool result."""
        msg = Message(
            role=Role.TOOL,
            content='{"result": "found"}',
            tool_call_id=ToolCallId("call_123"),
        )
        assert msg.role == Role.TOOL
        assert msg.tool_call_id == "call_123"


@pytest.mark.unit
class TestToolDefinition:
    """Test ToolDefinition dataclass."""

    def test_tool_definition(self) -> None:
        """ToolDefinition should hold tool schema."""
        tool = ToolDefinition(
            name=ToolName("search"),
            description="Search for information",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        assert tool.name == "search"
        assert tool.description == "Search for information"
        assert "properties" in tool.parameters


@pytest.mark.unit
class TestUsage:
    """Test Usage dataclass."""

    def test_total_tokens(self) -> None:
        """Usage should calculate total tokens."""
        usage = Usage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150


@pytest.mark.unit
class TestStreamChunk:
    """Test StreamChunk dataclass."""

    def test_content_chunk(self) -> None:
        """StreamChunk with content."""
        chunk = StreamChunk(content="Hello")
        assert chunk.content == "Hello"
        assert chunk.tool_calls == []
        assert chunk.finish_reason is None

    def test_finish_chunk(self) -> None:
        """StreamChunk with finish reason."""
        chunk = StreamChunk(finish_reason="stop")
        assert chunk.content is None
        assert chunk.finish_reason == "stop"


@pytest.mark.unit
class TestCompletionResponse:
    """Test CompletionResponse dataclass."""

    def test_completion_response(self) -> None:
        """CompletionResponse should hold full response."""
        response = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hi!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model=ModelId("llama-3.3-70b-versatile"),
        )
        assert response.message.content == "Hi!"
        assert response.usage.total_tokens == 15
        assert response.model == "llama-3.3-70b-versatile"
