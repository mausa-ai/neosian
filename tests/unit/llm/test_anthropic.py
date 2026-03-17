"""Unit tests for the Anthropic LLM client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from neosian._foundation.llm.anthropic import (
    AnthropicClient,
    _strip_numeric_constraints,
)
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
        """Test tool definition conversion with cache_control on last tool."""
        converted = client._convert_tools([sample_tool])

        assert len(converted) == 1
        assert converted[0]["name"] == "get_weather"
        assert converted[0]["description"] == "Get the weather for a location"
        assert "input_schema" in converted[0]
        assert converted[0]["input_schema"]["type"] == "object"
        assert converted[0]["cache_control"] == {"type": "ephemeral"}

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
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
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
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
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
            model=Model.CLAUDE_SONNET_4_6,
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
                model=Model.CLAUDE_HAIKU_4_5,
                reasoning_effort=ReasoningEffort.HIGH,
            )

        assert "claude-haiku-4-5" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_reasoning_effort_none_includes_temperature(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify temperature is included when reasoning_effort is None."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Answer")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
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
                model=Model.CLAUDE_HAIKU_4_5,
                reasoning_effort=ReasoningEffort.HIGH,
            ):
                pass

    @pytest.mark.asyncio
    async def test_sonnet_4_6_supports_reasoning(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify Sonnet 4.6 supports reasoning with adaptive thinking."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Answer")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "adaptive"}
        assert call_kwargs["output_config"] == {"effort": "high"}
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_max_effort_downgraded_to_high_for_sonnet(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is downgraded to HIGH for Sonnet 4.6 (Opus-only)."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Answer")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "high"}

    @pytest.mark.asyncio
    async def test_max_effort_preserved_for_opus(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is NOT downgraded for Opus 4.6."""
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
    async def test_max_effort_downgraded_in_stream_for_sonnet(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is downgraded to HIGH in stream for Sonnet 4.6."""
        mock_event = MagicMock()
        mock_event.type = "message_stop"

        async def mock_stream_events():
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            reasoning_effort=ReasoningEffort.MAX,
        ):
            pass

        call_kwargs = client._client.messages.stream.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "high"}


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
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
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


@pytest.mark.unit
class TestAnthropicStreamingToolCalls:
    """Tests for streaming tool call support in Anthropic client."""

    @pytest.mark.asyncio
    async def test_stream_yields_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream should accumulate tool_use blocks and yield ToolCalls."""
        # content_block_start for tool_use
        mock_block_start = MagicMock()
        mock_block_start.type = "content_block_start"
        mock_content_block = MagicMock(type="tool_use", id="toolu_abc")
        mock_content_block.name = "get_weather"
        mock_block_start.content_block = mock_content_block

        # input_json_delta chunks
        mock_input_delta1 = MagicMock()
        mock_input_delta1.type = "content_block_delta"
        mock_input_delta1.delta = MagicMock(
            type="input_json_delta", partial_json='{"location":'
        )

        mock_input_delta2 = MagicMock()
        mock_input_delta2.type = "content_block_delta"
        mock_input_delta2.delta = MagicMock(
            type="input_json_delta", partial_json=' "Paris"}'
        )

        # content_block_stop finalizes the tool call
        mock_block_stop = MagicMock()
        mock_block_stop.type = "content_block_stop"

        # message_stop with usage
        mock_msg_delta = MagicMock()
        mock_msg_delta.type = "message_delta"
        mock_msg_delta.usage = MagicMock(input_tokens=0, output_tokens=15)

        mock_msg_stop = MagicMock()
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events():  # type: ignore[return]
            yield mock_block_start
            yield mock_input_delta1
            yield mock_input_delta2
            yield mock_block_stop
            yield mock_msg_delta
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            tools=[
                ToolDefinition(
                    name="get_weather",
                    description="Get weather",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        ):
            chunks.append(chunk)

        # Final chunk should have tool calls and finish_reason="tool_use"
        final = chunks[-1]
        assert final.finish_reason == "tool_use"
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].id == "toolu_abc"
        assert final.tool_calls[0].name == "get_weather"
        assert final.tool_calls[0].arguments == {"location": "Paris"}

    @pytest.mark.asyncio
    async def test_stream_mixed_content_and_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream should yield text content AND accumulate tool calls."""
        # Text content first
        mock_text_delta = MagicMock()
        mock_text_delta.type = "content_block_delta"
        mock_text_delta.delta = MagicMock(type="text_delta", text="Let me check.")

        # Then a tool_use block
        mock_block_start = MagicMock()
        mock_block_start.type = "content_block_start"
        mock_content_block = MagicMock(type="tool_use", id="toolu_xyz")
        mock_content_block.name = "search"
        mock_block_start.content_block = mock_content_block

        mock_input_delta = MagicMock()
        mock_input_delta.type = "content_block_delta"
        mock_input_delta.delta = MagicMock(
            type="input_json_delta", partial_json='{"query": "test"}'
        )

        mock_block_stop = MagicMock()
        mock_block_stop.type = "content_block_stop"

        mock_msg_stop = MagicMock()
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events():  # type: ignore[return]
            yield mock_text_delta
            yield mock_block_start
            yield mock_input_delta
            yield mock_block_stop
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            tools=[
                ToolDefinition(
                    name="search",
                    description="Search",
                    parameters={"type": "object", "properties": {}},
                )
            ],
        ):
            chunks.append(chunk)

        # First chunk: text content
        assert chunks[0].content == "Let me check."
        assert not chunks[0].tool_calls

        # Final chunk: tool calls
        final = chunks[-1]
        assert final.finish_reason == "tool_use"
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].name == "search"
        assert final.tool_calls[0].arguments == {"query": "test"}

    @pytest.mark.asyncio
    async def test_stream_passes_tools_to_api(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream should pass converted tools to the Anthropic API."""
        mock_event = MagicMock()
        mock_event.type = "message_stop"

        async def mock_stream_events():  # type: ignore[return]
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        tool_def = ToolDefinition(
            name="calc",
            description="Calculate",
            parameters={"type": "object", "properties": {"x": {"type": "number"}}},
        )

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            tools=[tool_def],
        ):
            pass

        call_kwargs = client._client.messages.stream.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["name"] == "calc"
        assert call_kwargs["tools"][0]["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_stream_no_tools_no_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream without tools should yield empty tool_calls in final chunk."""
        mock_text = MagicMock()
        mock_text.type = "content_block_delta"
        mock_text.delta = MagicMock(type="text_delta", text="Hello!")

        mock_stop = MagicMock()
        mock_stop.type = "message_stop"

        async def mock_stream_events():  # type: ignore[return]
            yield mock_text
            yield mock_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
        ):
            chunks.append(chunk)

        assert chunks[0].content == "Hello!"
        final = chunks[-1]
        assert final.finish_reason == "stop"
        assert final.tool_calls == []

        # Verify tools not passed to API when None
        call_kwargs = client._client.messages.stream.call_args.kwargs
        assert "tools" not in call_kwargs

    @pytest.mark.asyncio
    async def test_stream_multiple_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream should accumulate multiple tool_use blocks."""
        # First tool
        mock_start1 = MagicMock()
        mock_start1.type = "content_block_start"
        mock_block1 = MagicMock(type="tool_use", id="toolu_1")
        mock_block1.name = "search"
        mock_start1.content_block = mock_block1

        mock_input1 = MagicMock()
        mock_input1.type = "content_block_delta"
        mock_input1.delta = MagicMock(
            type="input_json_delta", partial_json='{"q": "a"}'
        )

        mock_stop1 = MagicMock()
        mock_stop1.type = "content_block_stop"

        # Second tool
        mock_start2 = MagicMock()
        mock_start2.type = "content_block_start"
        mock_block2 = MagicMock(type="tool_use", id="toolu_2")
        mock_block2.name = "fetch"
        mock_start2.content_block = mock_block2

        mock_input2 = MagicMock()
        mock_input2.type = "content_block_delta"
        mock_input2.delta = MagicMock(
            type="input_json_delta", partial_json='{"url": "https://x.com"}'
        )

        mock_stop2 = MagicMock()
        mock_stop2.type = "content_block_stop"

        mock_msg_stop = MagicMock()
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events():  # type: ignore[return]
            yield mock_start1
            yield mock_input1
            yield mock_stop1
            yield mock_start2
            yield mock_input2
            yield mock_stop2
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            tools=[
                ToolDefinition(
                    name="search",
                    description="Search",
                    parameters={"type": "object", "properties": {}},
                ),
                ToolDefinition(
                    name="fetch",
                    description="Fetch",
                    parameters={"type": "object", "properties": {}},
                ),
            ],
        ):
            chunks.append(chunk)

        final = chunks[-1]
        assert final.finish_reason == "tool_use"
        assert len(final.tool_calls) == 2
        assert final.tool_calls[0].id == "toolu_1"
        assert final.tool_calls[0].name == "search"
        assert final.tool_calls[1].id == "toolu_2"
        assert final.tool_calls[1].name == "fetch"


@pytest.mark.unit
class TestAnthropicPromptCaching:
    """Tests for Anthropic prompt caching (cache_control breakpoints)."""

    def test_apply_cache_control_system_prompt(self, client: AnthropicClient) -> None:
        """System prompt should be converted to structured list with cache_control."""
        cached_system, _ = client._apply_cache_control("You are helpful.", [])

        assert cached_system is not None
        assert len(cached_system) == 1
        assert cached_system[0]["type"] == "text"
        assert cached_system[0]["text"] == "You are helpful."
        assert cached_system[0]["cache_control"] == {"type": "ephemeral"}

    def test_apply_cache_control_no_system_prompt(
        self, client: AnthropicClient
    ) -> None:
        """None system prompt should return None cached_system."""
        cached_system, _ = client._apply_cache_control(None, [])

        assert cached_system is None

    def test_apply_cache_control_last_user_message_string(
        self, client: AnthropicClient
    ) -> None:
        """Last user message (string content) should get cache_control."""
        messages = [
            {"role": "user", "content": "Hello!"},
        ]
        _, cached_messages = client._apply_cache_control(None, messages)

        # String content should be converted to structured list
        last = cached_messages[-1]
        assert isinstance(last["content"], list)
        assert len(last["content"]) == 1
        assert last["content"][0]["type"] == "text"
        assert last["content"][0]["text"] == "Hello!"
        assert last["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_apply_cache_control_last_tool_result(
        self, client: AnthropicClient
    ) -> None:
        """Last tool_result message (list content) should get cache_control on last block."""
        messages = [
            {"role": "user", "content": "Hello!"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "content": '{"temp": 22}',
                    }
                ],
            },
        ]
        _, cached_messages = client._apply_cache_control(None, messages)

        # First message should NOT have cache_control
        first = cached_messages[0]
        assert isinstance(first["content"], str)  # Unchanged

        # Last message's last block should have cache_control
        last = cached_messages[-1]
        assert isinstance(last["content"], list)
        assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_apply_cache_control_last_assistant_message(
        self, client: AnthropicClient
    ) -> None:
        """Last assistant message (list content) should get cache_control on last block."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "search",
                        "input": {"q": "test"},
                    },
                ],
            },
        ]
        _, cached_messages = client._apply_cache_control(None, messages)

        last = cached_messages[-1]
        # cache_control should be on the last block (tool_use)
        assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}
        # First block should NOT have cache_control
        assert "cache_control" not in last["content"][0]

    def test_apply_cache_control_empty_messages(self, client: AnthropicClient) -> None:
        """Empty messages list should not raise."""
        cached_system, cached_messages = client._apply_cache_control("system", [])

        assert cached_system is not None
        assert cached_messages == []

    def test_convert_tools_cache_control_on_last_only(
        self, client: AnthropicClient
    ) -> None:
        """Only the last tool should have cache_control."""
        tools = [
            ToolDefinition(
                name="tool_a",
                description="Tool A",
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="tool_b",
                description="Tool B",
                parameters={"type": "object", "properties": {}},
            ),
        ]
        converted = client._convert_tools(tools)

        assert "cache_control" not in converted[0]
        assert converted[1]["cache_control"] == {"type": "ephemeral"}

    def test_convert_tools_empty_list(self, client: AnthropicClient) -> None:
        """Empty tools list should not raise."""
        converted = client._convert_tools([])
        assert converted == []

    @pytest.mark.asyncio
    async def test_complete_sends_structured_system(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() should send system as structured list with cache_control."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hi")]
        mock_response.usage = MagicMock(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=0,
        )
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        # System should be structured list, not plain string
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["type"] == "text"
        assert system[0]["cache_control"] == {"type": "ephemeral"}

        # Last message should have cache_control
        messages = call_kwargs["messages"]
        last_msg = messages[-1]
        assert isinstance(last_msg["content"], list)
        assert last_msg["content"][-1]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_complete_extracts_cache_tokens(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() should extract cache token counts from response usage."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hi")]
        mock_response.usage = MagicMock(
            input_tokens=50,
            output_tokens=10,
            cache_creation_input_tokens=2500,
            cache_read_input_tokens=0,
        )
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
        )

        assert response.usage.input_tokens == 50
        assert response.usage.output_tokens == 10
        assert response.usage.cache_creation_input_tokens == 2500
        assert response.usage.cache_read_input_tokens == 0

    @pytest.mark.asyncio
    async def test_complete_extracts_cache_read_tokens(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() should extract cache read tokens (cache hit scenario)."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hi")]
        mock_response.usage = MagicMock(
            input_tokens=50,
            output_tokens=10,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=2500,
        )
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
        )

        assert response.usage.cache_creation_input_tokens == 0
        assert response.usage.cache_read_input_tokens == 2500
        assert response.usage.total_tokens == 50 + 10 + 0 + 2500

    @pytest.mark.asyncio
    async def test_complete_handles_missing_cache_fields(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() should handle responses without cache fields (graceful fallback)."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hi")]
        # Simulate response without cache fields (spec=False lets getattr return default)
        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5
        del mock_usage.cache_creation_input_tokens
        del mock_usage.cache_read_input_tokens
        mock_response.usage = mock_usage
        mock_response.model = "claude-sonnet-4-6"

        client._client.messages.create = AsyncMock(return_value=mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
        )

        assert response.usage.cache_creation_input_tokens == 0
        assert response.usage.cache_read_input_tokens == 0

    @pytest.mark.asyncio
    async def test_stream_sends_structured_system(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """stream() should send system as structured list with cache_control."""
        mock_event = MagicMock()
        mock_event.type = "message_stop"

        async def mock_stream_events():
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
        ):
            pass

        call_kwargs = client._client.messages.stream.call_args.kwargs
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_stream_extracts_cache_tokens(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """stream() should extract cache tokens from message_start event."""
        # message_start with input + cache tokens
        mock_msg_start = MagicMock()
        mock_msg_start.type = "message_start"
        mock_msg_start.message = MagicMock()
        mock_msg_start.message.usage = MagicMock(
            input_tokens=50,
            cache_creation_input_tokens=2500,
            cache_read_input_tokens=0,
        )

        mock_text = MagicMock()
        mock_text.type = "content_block_delta"
        mock_text.delta = MagicMock(type="text_delta", text="Hi")

        # message_delta with output tokens
        mock_msg_delta = MagicMock()
        mock_msg_delta.type = "message_delta"
        mock_msg_delta.usage = MagicMock(output_tokens=10)

        mock_msg_stop = MagicMock()
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events():
            yield mock_msg_start
            yield mock_text
            yield mock_msg_delta
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
        ):
            chunks.append(chunk)

        # Final chunk should have combined usage from message_start + message_delta
        final = chunks[-1]
        assert final.usage is not None
        assert final.usage.input_tokens == 50
        assert final.usage.output_tokens == 10
        assert final.usage.cache_creation_input_tokens == 2500
        assert final.usage.cache_read_input_tokens == 0

    @pytest.mark.asyncio
    async def test_stream_tools_have_cache_control(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """stream() should send tools with cache_control on last tool."""
        mock_event = MagicMock()
        mock_event.type = "message_stop"

        async def mock_stream_events():
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        client._client.messages.stream = MagicMock(return_value=mock_stream)

        tool_def = ToolDefinition(
            name="calc",
            description="Calculate",
            parameters={"type": "object", "properties": {}},
        )

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_4_6,
            tools=[tool_def],
        ):
            pass

        call_kwargs = client._client.messages.stream.call_args.kwargs
        tools = call_kwargs["tools"]
        assert tools[-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.unit
class TestStripNumericConstraints:
    """Tests for _strip_numeric_constraints schema sanitizer."""

    def test_strips_integer_constraints(self) -> None:
        """minimum/maximum should be removed from integer properties."""
        schema = {
            "type": "object",
            "properties": {
                "fontsize": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Font size",
                },
            },
        }
        result = _strip_numeric_constraints(schema)
        prop = result["properties"]["fontsize"]
        assert "minimum" not in prop
        assert "maximum" not in prop
        assert prop["description"] == "Font size"

    def test_strips_number_constraints(self) -> None:
        """minimum/maximum should be removed from number properties."""
        schema = {
            "type": "object",
            "properties": {
                "width": {
                    "type": "number",
                    "minimum": 0.5,
                    "maximum": 5.0,
                },
            },
        }
        result = _strip_numeric_constraints(schema)
        assert "minimum" not in result["properties"]["width"]
        assert "maximum" not in result["properties"]["width"]

    def test_strips_exclusive_constraints(self) -> None:
        """exclusiveMinimum/exclusiveMaximum should also be removed."""
        schema = {
            "type": "object",
            "properties": {
                "val": {
                    "type": "integer",
                    "exclusiveMinimum": 0,
                    "exclusiveMaximum": 100,
                },
            },
        }
        result = _strip_numeric_constraints(schema)
        assert "exclusiveMinimum" not in result["properties"]["val"]
        assert "exclusiveMaximum" not in result["properties"]["val"]

    def test_preserves_string_constraints(self) -> None:
        """String constraints (minLength, maxLength, pattern) should NOT be removed."""
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "pattern": "^[a-z]+$",
                },
            },
        }
        result = _strip_numeric_constraints(schema)
        prop = result["properties"]["name"]
        assert prop["minLength"] == 1
        assert prop["maxLength"] == 100
        assert prop["pattern"] == "^[a-z]+$"

    def test_recurses_into_defs(self) -> None:
        """Should strip constraints in $defs."""
        schema = {
            "type": "object",
            "$defs": {
                "Size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "properties": {},
        }
        result = _strip_numeric_constraints(schema)
        assert "minimum" not in result["$defs"]["Size"]
        assert "maximum" not in result["$defs"]["Size"]

    def test_recurses_into_anyof(self) -> None:
        """Should strip constraints inside anyOf variants."""
        schema = {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "minimum": 0},
                    },
                },
            ],
        }
        result = _strip_numeric_constraints(schema)
        assert "minimum" not in result["anyOf"][0]["properties"]["count"]

    def test_does_not_mutate_original(self) -> None:
        """Original schema dict should not be modified."""
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 1, "maximum": 10},
            },
        }
        _strip_numeric_constraints(schema)
        assert schema["properties"]["x"]["minimum"] == 1
        assert schema["properties"]["x"]["maximum"] == 10

    def test_convert_tools_strips_constraints(self, client: AnthropicClient) -> None:
        """_convert_tools should produce schemas without numeric constraints."""
        tool = ToolDefinition(
            name="caption",
            description="Add captions",
            parameters={
                "type": "object",
                "properties": {
                    "fontsize": {"type": "integer", "minimum": 1, "maximum": 20},
                    "label": {"type": "string", "minLength": 1},
                },
            },
        )
        converted = client._convert_tools([tool])
        props = converted[0]["input_schema"]["properties"]
        assert "minimum" not in props["fontsize"]
        assert "maximum" not in props["fontsize"]
        # String constraints preserved
        assert props["label"]["minLength"] == 1
        # Original not mutated
        assert tool.parameters["properties"]["fontsize"]["minimum"] == 1
