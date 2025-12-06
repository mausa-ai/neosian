"""Tests for Groq LLM client."""

import pytest

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.shared.types import ToolName


@pytest.mark.unit
class TestGroqClientInit:
    """Test GroqClient initialization."""

    def test_requires_api_key(self) -> None:
        """GroqClient should require an explicit API key."""
        # This should work - explicit key
        client = GroqClient(api_key="test-key")
        assert client is not None

    def test_api_key_is_required_parameter(self) -> None:
        """GroqClient should not accept None as api_key."""
        # The type signature enforces str, not str | None
        # This test documents the expected behavior
        with pytest.raises(TypeError):
            GroqClient()  # type: ignore[call-arg]


@pytest.mark.unit
class TestGroqClientMessageConversion:
    """Test message conversion to Groq format."""

    def test_convert_system_message(self) -> None:
        """System messages should convert correctly."""
        client = GroqClient(api_key="test-key")
        messages = [Message(role=Role.SYSTEM, content="You are helpful.")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_convert_user_message(self) -> None:
        """User messages should convert correctly."""
        client = GroqClient(api_key="test-key")
        messages = [Message(role=Role.USER, content="Hello")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_convert_assistant_message(self) -> None:
        """Assistant messages should convert correctly."""
        client = GroqClient(api_key="test-key")
        messages = [Message(role=Role.ASSISTANT, content="Hi there!")]

        result = client._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Hi there!"


@pytest.mark.unit
class TestGroqClientToolConversion:
    """Test tool conversion to Groq format."""

    def test_convert_tool_definition(self) -> None:
        """Tool definitions should convert correctly."""
        client = GroqClient(api_key="test-key")
        tools = [
            ToolDefinition(
                name=ToolName("search"),
                description="Search for information",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ]

        result = client._convert_tools(tools)

        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search"
        assert result[0]["function"]["description"] == "Search for information"
        assert "properties" in result[0]["function"]["parameters"]
