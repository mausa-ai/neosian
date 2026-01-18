"""Unit tests for the Anthropic LLM client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.shared.types import Model


@pytest.fixture
def client() -> AnthropicClient:
    """Create a client instance with a test API key."""
    return AnthropicClient(api_key="test-api-key")


@pytest.fixture
def sample_messages() -> list[Message]:
    """Sample conversation messages."""
    return [
        Message(role=Role.SYSTEM, content="You are a helpful assistant."),
        Message(role=Role.USER, content="Hello!"),
    ]


@pytest.fixture
def sample_tool() -> ToolDefinition:
    """Sample tool definition."""
    return ToolDefinition(
        name="get_weather",
        description="Get the weather for a location",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "The city name"},
            },
            "required": ["location"],
        },
    )


class TestAnthropicClient:
    """Tests for AnthropicClient."""

    @pytest.mark.unit
    def test_init(self) -> None:
        """Test client initialization."""
        client = AnthropicClient(api_key="test-key")
        assert client._client is not None

    @pytest.mark.unit
    def test_convert_messages_extracts_system(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Test that system message is extracted from messages."""
        system_prompt, messages = client._convert_messages(sample_messages)

        assert system_prompt == "You are a helpful assistant."
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello!"

    @pytest.mark.unit
    def test_convert_messages_handles_tool_calls(self, client: AnthropicClient) -> None:
        """Test message conversion handles assistant messages with tool calls."""
        from neosian._foundation.llm.base import ToolCall
        from neosian._foundation.shared.types import ToolCallId, ToolName

        messages = [
            Message(
                role=Role.ASSISTANT,
                content="Let me check the weather.",
                tool_calls=[
                    ToolCall(
                        id=ToolCallId("call_123"),
                        name=ToolName("get_weather"),
                        arguments={"location": "Paris"},
                    )
                ],
            ),
        ]

        _, converted = client._convert_messages(messages)

        assert len(converted) == 1
        assert converted[0]["role"] == "assistant"
        content = converted[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "Let me check the weather."
        assert content[1]["type"] == "tool_use"
        assert content[1]["id"] == "call_123"
        assert content[1]["name"] == "get_weather"
        assert content[1]["input"] == {"location": "Paris"}

    @pytest.mark.unit
    def test_convert_messages_handles_tool_results(
        self, client: AnthropicClient
    ) -> None:
        """Test message conversion handles tool result messages."""
        messages = [
            Message(
                role=Role.TOOL,
                content='{"temperature": 22}',
                tool_call_id="call_123",
            ),
        ]

        _, converted = client._convert_messages(messages)

        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        content = converted[0]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "call_123"
        assert content[0]["content"] == '{"temperature": 22}'

    @pytest.mark.unit
    def test_convert_tools(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """Test tool definition conversion."""
        converted = client._convert_tools([sample_tool])

        assert len(converted) == 1
        assert converted[0]["name"] == "get_weather"
        assert converted[0]["description"] == "Get the weather for a location"
        assert "input_schema" in converted[0]
        assert converted[0]["input_schema"]["type"] == "object"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_complete_basic(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Test basic completion without tools."""
        # Mock the Anthropic API response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello there!")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-sonnet-4-5-latest"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_5,
        )

        assert response.message.role == Role.ASSISTANT
        assert response.message.content == "Hello there!"
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Test completion that returns tool calls."""
        mock_tool_use = MagicMock()
        mock_tool_use.type = "tool_use"
        mock_tool_use.id = "toolu_123"
        mock_tool_use.name = "get_weather"
        mock_tool_use.input = {"location": "Paris"}

        mock_response = MagicMock()
        mock_response.content = [mock_tool_use]
        mock_response.usage = MagicMock(input_tokens=15, output_tokens=8)
        mock_response.model = "claude-sonnet-4-5-latest"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_5,
            tools=[
                ToolDefinition(
                    name="get_weather",
                    description="Get weather",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        )

        assert response.message.tool_calls is not None
        assert len(response.message.tool_calls) == 1
        assert response.message.tool_calls[0].id == "toolu_123"
        assert response.message.tool_calls[0].name == "get_weather"
        assert response.message.tool_calls[0].arguments == {"location": "Paris"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stream_basic(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Test basic streaming."""
        # Create mock events
        mock_event1 = MagicMock()
        mock_event1.type = "content_block_delta"
        mock_event1.delta = MagicMock(text="Hello")

        mock_event2 = MagicMock()
        mock_event2.type = "content_block_delta"
        mock_event2.delta = MagicMock(text=" world!")

        mock_event3 = MagicMock()
        mock_event3.type = "message_stop"

        # Create async iterator for stream
        async def mock_stream_events():
            yield mock_event1
            yield mock_event2
            yield mock_event3

        # Create mock context manager for stream
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_5,
        ):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].content == "Hello"
        assert chunks[1].content == " world!"
        assert chunks[2].finish_reason == "stop"

    @pytest.mark.unit
    def test_is_tool_call_error(self, client: AnthropicClient) -> None:
        """Test tool call error detection."""
        from anthropic import BadRequestError

        # Create mock error with tool-related message
        mock_body = {"message": "Invalid tool call"}

        error = BadRequestError(
            message="Invalid tool call format",
            response=MagicMock(status_code=400),
            body=mock_body,
        )

        assert client._is_tool_call_error(error) is True

        # Test with non-tool error
        error2 = BadRequestError(
            message="Invalid request",
            response=MagicMock(status_code=400),
            body={"message": "Invalid model"},
        )

        assert client._is_tool_call_error(error2) is False
