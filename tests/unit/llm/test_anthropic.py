"""Unit tests for the Anthropic LLM client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.types import Model, ReasoningEffort


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
        # Create mock events with delta.type to match Anthropic SDK format
        mock_event1 = MagicMock()
        mock_event1.type = "content_block_delta"
        mock_event1.delta = MagicMock(type="text_delta", text="Hello")

        mock_event2 = MagicMock()
        mock_event2.type = "content_block_delta"
        mock_event2.delta = MagicMock(type="text_delta", text=" world!")

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


@pytest.mark.unit
class TestAnthropicReasoningEffort:
    """Tests for reasoning_effort parameter handling."""

    @pytest.mark.asyncio
    async def test_reasoning_effort_passes_thinking_and_effort_kwargs(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify thinking and output_config kwargs are passed, temperature is NOT."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Answer")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-opus-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        client._client.messages.create.assert_called_once()
        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "adaptive"}
        assert call_kwargs["output_config"] == {"effort": "high"}
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_passes_max_effort(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is passed as 'max'."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Answer")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-opus-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "max"}

    @pytest.mark.asyncio
    async def test_reasoning_effort_raises_for_non_reasoning_model(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify UnsupportedParameterError for non-reasoning Anthropic models."""
        with pytest.raises(UnsupportedParameterError) as exc_info:
            await client.complete(
                messages=sample_messages,
                model=Model.CLAUDE_SONNET_4_5,
                reasoning_effort=ReasoningEffort.HIGH,
            )

        assert "claude-sonnet-4-5" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_reasoning_effort_none_includes_temperature(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify temperature is included when reasoning_effort is None."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Answer")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-sonnet-4-5-latest"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_5,
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert "temperature" in call_kwargs
        assert "thinking" not in call_kwargs
        assert "output_config" not in call_kwargs

    @pytest.mark.asyncio
    async def test_reasoning_effort_in_stream_passes_thinking_kwargs(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify stream passes thinking and output_config kwargs."""
        mock_event = MagicMock()
        mock_event.type = "message_stop"

        async def mock_stream_events():
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.MEDIUM,
        ):
            chunks.append(chunk)

        call_kwargs = client._client.messages.stream.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "adaptive"}
        assert call_kwargs["output_config"] == {"effort": "medium"}
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_reasoning_effort_stream_raises_for_non_reasoning_model(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify stream raises UnsupportedParameterError for non-reasoning models."""
        with pytest.raises(UnsupportedParameterError):
            async for _ in client.stream(
                messages=sample_messages,
                model=Model.CLAUDE_SONNET_4_5,
                reasoning_effort=ReasoningEffort.HIGH,
            ):
                pass


@pytest.mark.unit
class TestAnthropicReasoningContent:
    """Tests for reasoning content parsing in responses and streams."""

    @pytest.mark.asyncio
    async def test_complete_parses_thinking_block(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify thinking blocks are parsed into message.reasoning."""
        mock_thinking = MagicMock()
        mock_thinking.type = "thinking"
        mock_thinking.thinking = "Let me analyze step by step..."

        mock_text = MagicMock()
        mock_text.type = "text"
        mock_text.text = "The answer is 42."

        mock_response = MagicMock()
        mock_response.content = [mock_thinking, mock_text]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=20)
        mock_response.model = "claude-opus-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.reasoning == "Let me analyze step by step..."
        assert response.message.content == "The answer is 42."

    @pytest.mark.asyncio
    async def test_complete_handles_redacted_thinking(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify redacted_thinking blocks are skipped without error."""
        mock_redacted = MagicMock()
        mock_redacted.type = "redacted_thinking"
        mock_redacted.data = "encrypted-data-here"

        mock_text = MagicMock()
        mock_text.type = "text"
        mock_text.text = "The answer."

        mock_response = MagicMock()
        mock_response.content = [mock_redacted, mock_text]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-opus-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.reasoning is None
        assert response.message.content == "The answer."

    @pytest.mark.asyncio
    async def test_complete_handles_no_thinking(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify reasoning is None when no thinking blocks are present."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello!")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-sonnet-4-5-latest"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_5,
        )

        assert response.message.reasoning is None
        assert response.message.content == "Hello!"

    @pytest.mark.asyncio
    async def test_stream_parses_thinking_delta(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify thinking_delta events yield StreamChunk with reasoning."""
        # Thinking delta event
        mock_thinking_event = MagicMock()
        mock_thinking_event.type = "content_block_delta"
        mock_thinking_event.delta = MagicMock(
            type="thinking_delta", thinking="Step 1: analyze..."
        )

        # Text delta event
        mock_text_event = MagicMock()
        mock_text_event.type = "content_block_delta"
        mock_text_event.delta = MagicMock(type="text_delta", text="The answer.")

        mock_stop = MagicMock()
        mock_stop.type = "message_stop"

        async def mock_stream_events():
            yield mock_thinking_event
            yield mock_text_event
            yield mock_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_4_6,
            reasoning_effort=ReasoningEffort.HIGH,
        ):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].reasoning == "Step 1: analyze..."
        assert chunks[0].content is None
        assert chunks[1].content == "The answer."
        assert chunks[1].reasoning is None
        assert chunks[2].finish_reason == "stop"
