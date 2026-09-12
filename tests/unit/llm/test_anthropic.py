"""Unit tests for the Anthropic LLM client."""

import dataclasses
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.anthropic_tools import (
    cache_control,
    convert_tool_choice,
    strip_unsupported_constraints,
)
from neosian._foundation.llm.base import (
    CompactionBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolCall,
    ToolDefinition,
)
from neosian._foundation.shared import models as models_module
from neosian._foundation.shared.exceptions import (
    ProviderError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ToolCallId,
    ToolChoice,
    ToolName,
)
from neosian._foundation.tools.result import ToolResult
from tests.unit.llm.sdk_specs import ANTHROPIC as SPEC


def _sdk(client: AnthropicClient) -> Any:
    """The underlying SDK client, untyped for mock wiring and inspection."""
    return client._client


def _stub_stream(client: AnthropicClient, mock_stream: MagicMock) -> None:
    """Stub messages.stream against the installed SDK's real signature, so a
    keyword the SDK dropped turns these suites red (TP-10)."""
    real = _sdk(client).messages.stream
    _sdk(client).messages.stream = create_autospec(real, return_value=mock_stream)


def _mock_complete(client: AnthropicClient, mock_response: MagicMock) -> None:
    """Wire a mocked final message into complete()'s internal-streaming path.

    complete() uses messages.stream() + get_final_message() rather than
    messages.create(), so tests mock the stream context manager and inspect
    _sdk(client).messages.stream.call_args.
    """
    inner = MagicMock()
    inner.get_final_message = AsyncMock(return_value=mock_response)
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=inner)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    _stub_stream(client, mock_stream)


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
        name=ToolName("get_weather"),
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
                tool_call_id=ToolCallId("call_123"),
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
    def test_a_failed_envelope_is_marked_is_error(
        self, client: AnthropicClient
    ) -> None:
        """A failed `ToolResult` rides as `is_error: true` (LL-10); a
        success and a foreign payload carry no flag."""
        messages = [
            Message(
                role=Role.TOOL,
                content=ToolResult.fail("boom", code="tool_execution_failed").to_json(),
                tool_call_id=ToolCallId("call_1"),
            ),
            Message(
                role=Role.TOOL,
                content=ToolResult.ok("fine").to_json(),
                tool_call_id=ToolCallId("call_2"),
            ),
            Message(role=Role.TOOL, content="22", tool_call_id=ToolCallId("call_3")),
        ]

        _, converted = client._convert_messages(messages)

        blocks = converted[0]["content"]
        assert blocks[0]["is_error"] is True
        assert "is_error" not in blocks[1]
        assert "is_error" not in blocks[2]

    def test_consecutive_tool_results_share_one_user_message(
        self, client: AnthropicClient
    ) -> None:
        """A parallel batch's results ride one user message (LL-2)."""
        messages = [
            Message(role=Role.TOOL, content="22", tool_call_id=ToolCallId("call_1")),
            Message(role=Role.TOOL, content="rain", tool_call_id=ToolCallId("call_2")),
        ]

        _, converted = client._convert_messages(messages)

        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        assert [block["tool_use_id"] for block in converted[0]["content"]] == [
            "call_1",
            "call_2",
        ]
        assert [block["content"] for block in converted[0]["content"]] == ["22", "rain"]

    @pytest.mark.unit
    def test_tool_results_split_by_a_user_turn_stay_apart(
        self, client: AnthropicClient
    ) -> None:
        messages = [
            Message(role=Role.TOOL, content="22", tool_call_id=ToolCallId("call_1")),
            Message(role=Role.USER, content="and tomorrow?"),
            Message(role=Role.TOOL, content="rain", tool_call_id=ToolCallId("call_2")),
        ]

        _, converted = client._convert_messages(messages)

        assert [m["role"] for m in converted] == ["user", "user", "user"]
        assert converted[1]["content"] == "and tomorrow?"
        assert len(converted[2]["content"]) == 1

    @pytest.mark.unit
    def test_system_messages_join_in_order(self, client: AnthropicClient) -> None:
        """Every SYSTEM message reaches the prompt; none overwrites (LL-3)."""
        messages = [
            Message(role=Role.SYSTEM, content="Be brief."),
            Message(role=Role.USER, content="Hi"),
            Message(role=Role.SYSTEM, content="Answer in French."),
        ]

        system_prompt, converted = client._convert_messages(messages)

        assert system_prompt == "Be brief.\n\nAnswer in French."
        assert len(converted) == 1

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
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hello there!")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
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
        mock_tool_use = MagicMock(spec_set=SPEC["tool_use"])
        mock_tool_use.type = "tool_use"
        mock_tool_use.id = "toolu_123"
        mock_tool_use.name = "get_weather"
        mock_tool_use.input = {"location": "Paris"}

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [mock_tool_use]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=15, output_tokens=8
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[
                ToolDefinition(
                    name=ToolName("get_weather"),
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
        mock_event1 = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_event1.type = "content_block_delta"
        mock_event1.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="Hello"
        )

        mock_event2 = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_event2.type = "content_block_delta"
        mock_event2.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text=" world!"
        )

        mock_event3 = MagicMock(spec_set=SPEC["message_stop"])
        mock_event3.type = "message_stop"

        # Create async iterator for stream
        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_event1
            yield mock_event2
            yield mock_event3

        # Create mock context manager for stream
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].content == "Hello"
        assert chunks[1].content == " world!"
        assert chunks[2].finish_reason == "stop"

    @pytest.mark.unit
    async def test_a_400_naming_a_tool_is_one_call(
        self, client: AnthropicClient
    ) -> None:
        """LL-5/LL-6 (#236): no tool-call retry — Anthropic has no coded
        generation failure, and a re-send of a bad request repeats it."""
        from anthropic import BadRequestError

        error = BadRequestError(
            message="tools.0.custom.name: invalid tool 'search_functions'",
            response=MagicMock(status_code=400),
            body={"message": "invalid tool"},
        )
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=error)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        _stub_stream(client, mock_stream)

        with pytest.raises(ProviderError) as info:
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.CLAUDE_SONNET_5,
                tools=[
                    ToolDefinition(
                        name=ToolName("search_functions"),
                        description="Search",
                        parameters={"type": "object", "properties": {}},
                    )
                ],
            )
        assert info.value.__cause__ is error
        assert _sdk(client).messages.stream.call_count == 1


@pytest.mark.unit
class TestAnthropicReasoningEffort:
    """Tests for reasoning_effort parameter handling."""

    @pytest.mark.asyncio
    async def test_reasoning_effort_passes_thinking_and_effort_kwargs(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify thinking and output_config kwargs are passed, temperature is NOT."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-opus-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        _sdk(client).messages.stream.assert_called_once()
        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "adaptive"}
        assert call_kwargs["output_config"] == {"effort": "high"}
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_reasoning_effort_max_passes_max_effort(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is passed as 'max'."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-opus-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
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
    async def test_reasoning_effort_none_omits_temperature_by_default(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """No reasoning effort and no explicit temperature -> neither is sent.

        Newer Claude models (e.g. Sonnet 5) reject non-default sampling
        parameters with a 400, so the API default must apply when unset.
        """
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert "temperature" not in call_kwargs
        assert "thinking" not in call_kwargs
        assert "output_config" not in call_kwargs

    @pytest.mark.asyncio
    async def test_explicit_temperature_is_sent(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """An explicitly provided temperature still reaches the API."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-haiku-4-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_HAIKU_4_5,
            temperature=0.3,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["extra_body"] == {"temperature": 0.3}

    @pytest.mark.asyncio
    async def test_explicit_temperature_is_sent_in_stream(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """The stream path sends an explicit temperature the same way."""
        mock_event = MagicMock(spec_set=SPEC["message_stop"])
        mock_event.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_HAIKU_4_5,
            temperature=0.3,
        ):
            pass

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["extra_body"] == {"temperature": 0.3}

    @pytest.mark.asyncio
    async def test_reasoning_effort_in_stream_passes_thinking_kwargs(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify stream passes thinking and output_config kwargs."""
        mock_event = MagicMock(spec_set=SPEC["message_stop"])
        mock_event.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.MEDIUM,
        ):
            chunks.append(chunk)

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
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
    async def test_sonnet_5_supports_reasoning(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify Sonnet 4.6 supports reasoning with adaptive thinking."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "adaptive"}
        assert call_kwargs["output_config"] == {"effort": "high"}
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_temperature_rejected_for_opus_5(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify explicit temperature raises for models rejecting sampling params."""
        with pytest.raises(UnsupportedParameterError):
            await client.complete(
                messages=sample_messages,
                model=Model.CLAUDE_OPUS_5,
                temperature=0.5,
            )

    @pytest.mark.asyncio
    async def test_temperature_rejected_for_opus_5_stream(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify explicit temperature raises in the stream path too."""
        with pytest.raises(UnsupportedParameterError):
            async for _ in client.stream(
                messages=sample_messages,
                model=Model.CLAUDE_OPUS_5,
                temperature=0.5,
            ):
                pass

    @pytest.mark.asyncio
    async def test_max_effort_passed_through_for_sonnet(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is passed through for Sonnet 5 (supports_max_effort)."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "max"}

    @pytest.mark.asyncio
    async def test_max_effort_downgraded_without_spec_support(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is downgraded when the spec disallows it."""
        spec = models_module._MODEL_SPECS[Model.CLAUDE_SONNET_5.value]
        patched = dataclasses.replace(spec, supports_max_effort=False)

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        with patch.dict(
            models_module._MODEL_SPECS, {Model.CLAUDE_SONNET_5.value: patched}
        ):
            await client.complete(
                messages=sample_messages,
                model=Model.CLAUDE_SONNET_5,
                reasoning_effort=ReasoningEffort.MAX,
            )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "high"}

    @pytest.mark.asyncio
    async def test_max_effort_preserved_for_opus(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is NOT downgraded for Opus 4.6."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Answer")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-opus-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "max"}

    @pytest.mark.asyncio
    async def test_max_effort_passed_through_in_stream_for_sonnet(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify MAX effort is passed through in stream for Sonnet 5."""
        mock_event = MagicMock(spec_set=SPEC["message_stop"])
        mock_event.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            reasoning_effort=ReasoningEffort.MAX,
        ):
            pass

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "max"}


@pytest.mark.unit
class TestAnthropicReasoningContent:
    """Tests for reasoning content parsing in responses and streams."""

    @pytest.mark.asyncio
    async def test_complete_parses_thinking_block(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify thinking blocks are parsed into message.reasoning."""
        mock_thinking = MagicMock(spec_set=SPEC["thinking"])
        mock_thinking.type = "thinking"
        mock_thinking.thinking = "Let me analyze step by step..."

        mock_text = MagicMock(spec_set=SPEC["text"])
        mock_text.type = "text"
        mock_text.text = "The answer is 42."

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [mock_thinking, mock_text]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=20
        )
        mock_response.model = "claude-opus-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.reasoning == "Let me analyze step by step..."
        assert response.message.content == "The answer is 42."

    @pytest.mark.asyncio
    async def test_complete_handles_redacted_thinking(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify redacted_thinking blocks are skipped without error."""
        mock_redacted = MagicMock(spec_set=SPEC["redacted_thinking"])
        mock_redacted.type = "redacted_thinking"
        mock_redacted.data = "encrypted-data-here"

        mock_text = MagicMock(spec_set=SPEC["text"])
        mock_text.type = "text"
        mock_text.text = "The answer."

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [mock_redacted, mock_text]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-opus-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.reasoning is None
        assert response.message.content == "The answer."

    @pytest.mark.asyncio
    async def test_complete_handles_no_thinking(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify reasoning is None when no thinking blocks are present."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hello!")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        assert response.message.reasoning is None
        assert response.message.content == "Hello!"

    @pytest.mark.asyncio
    async def test_stream_parses_thinking_delta(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Verify thinking_delta events yield StreamChunk with reasoning."""
        # Thinking delta event
        mock_thinking_event = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_thinking_event.type = "content_block_delta"
        mock_thinking_event.delta = MagicMock(
            spec=SPEC["thinking_delta"],
            type="thinking_delta",
            thinking="Step 1: analyze...",
        )

        # Text delta event
        mock_text_event = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_text_event.type = "content_block_delta"
        mock_text_event.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="The answer."
        )

        mock_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_thinking_event
            yield mock_text_event
            yield mock_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
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
        mock_block_start = MagicMock(spec_set=SPEC["content_block_start"])
        mock_block_start.type = "content_block_start"
        mock_content_block = MagicMock(
            spec=SPEC["tool_use"], type="tool_use", id="toolu_abc"
        )
        mock_content_block.name = "get_weather"
        mock_block_start.content_block = mock_content_block

        # input_json_delta chunks
        mock_input_delta1 = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_input_delta1.type = "content_block_delta"
        mock_input_delta1.delta = MagicMock(
            spec=SPEC["input_json_delta"],
            type="input_json_delta",
            partial_json='{"location":',
        )

        mock_input_delta2 = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_input_delta2.type = "content_block_delta"
        mock_input_delta2.delta = MagicMock(
            spec=SPEC["input_json_delta"],
            type="input_json_delta",
            partial_json=' "Paris"}',
        )

        # content_block_stop finalizes the tool call
        mock_block_stop = MagicMock(spec_set=SPEC["content_block_stop"])
        mock_block_stop.type = "content_block_stop"

        # message_stop with usage; the API reports the real stop reason
        # on the message_delta event
        mock_msg_delta = MagicMock(spec_set=SPEC["message_delta"])
        mock_msg_delta.type = "message_delta"
        mock_msg_delta.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=0, output_tokens=15
        )
        mock_msg_delta.delta = MagicMock(spec_set=SPEC["stop"], stop_reason="tool_use")

        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
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

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[
                ToolDefinition(
                    name=ToolName("get_weather"),
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
    async def test_a_truncated_tool_call_names_the_stop_reason(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Arguments cut off at max_tokens raise naming the stop reason,
        not the decoder — the stop reason arrives after the block ends,
        so decoding waits for it (LL-7)."""
        mock_block_start = MagicMock(spec_set=SPEC["content_block_start"])
        mock_block_start.type = "content_block_start"
        mock_content_block = MagicMock(
            spec=SPEC["tool_use"], type="tool_use", id="toolu_abc"
        )
        mock_content_block.name = "get_weather"
        mock_block_start.content_block = mock_content_block

        mock_input_delta = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_input_delta.type = "content_block_delta"
        mock_input_delta.delta = MagicMock(
            spec=SPEC["input_json_delta"],
            type="input_json_delta",
            partial_json='{"location":',
        )
        mock_block_stop = MagicMock(spec_set=SPEC["content_block_stop"])
        mock_block_stop.type = "content_block_stop"
        mock_msg_delta = MagicMock(spec_set=SPEC["message_delta"])
        mock_msg_delta.type = "message_delta"
        mock_msg_delta.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=0, output_tokens=15
        )
        mock_msg_delta.delta = MagicMock(
            spec_set=SPEC["stop"], stop_reason="max_tokens"
        )
        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_block_start
            yield mock_input_delta
            yield mock_block_stop
            yield mock_msg_delta
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()
        _stub_stream(client, mock_stream)

        chunks = []
        with pytest.raises(ProviderError, match="stop reason: max_tokens") as info:
            async for chunk in client.stream(
                messages=sample_messages, model=Model.CLAUDE_SONNET_5
            ):
                chunks.append(chunk)
        assert info.value.provider == "anthropic"
        assert info.value.retryable is False
        assert info.value.__cause__ is not info.value
        assert not any(chunk.tool_calls for chunk in chunks)

    @pytest.mark.asyncio
    async def test_stream_mixed_content_and_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream should yield text content AND accumulate tool calls."""
        # Text content first
        mock_text_delta = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_text_delta.type = "content_block_delta"
        mock_text_delta.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="Let me check."
        )

        # Then a tool_use block
        mock_block_start = MagicMock(spec_set=SPEC["content_block_start"])
        mock_block_start.type = "content_block_start"
        mock_content_block = MagicMock(
            spec=SPEC["tool_use"], type="tool_use", id="toolu_xyz"
        )
        mock_content_block.name = "search"
        mock_block_start.content_block = mock_content_block

        mock_input_delta = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_input_delta.type = "content_block_delta"
        mock_input_delta.delta = MagicMock(
            spec=SPEC["input_json_delta"],
            type="input_json_delta",
            partial_json='{"query": "test"}',
        )

        mock_block_stop = MagicMock(spec_set=SPEC["content_block_stop"])
        mock_block_stop.type = "content_block_stop"

        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_text_delta
            yield mock_block_start
            yield mock_input_delta
            yield mock_block_stop
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[
                ToolDefinition(
                    name=ToolName("search"),
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
        mock_event = MagicMock(spec_set=SPEC["message_stop"])
        mock_event.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        tool_def = ToolDefinition(
            name=ToolName("calc"),
            description="Calculate",
            parameters={"type": "object", "properties": {"x": {"type": "number"}}},
        )

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[tool_def],
        ):
            pass

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["name"] == "calc"
        assert call_kwargs["tools"][0]["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_stream_no_tools_no_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream without tools should yield empty tool_calls in final chunk."""
        mock_text = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_text.type = "content_block_delta"
        mock_text.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="Hello!"
        )

        mock_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_text
            yield mock_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            chunks.append(chunk)

        assert chunks[0].content == "Hello!"
        final = chunks[-1]
        assert final.finish_reason == "stop"
        assert final.tool_calls == []

        # Verify tools not passed to API when None
        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert "tools" not in call_kwargs

    @pytest.mark.asyncio
    async def test_stream_multiple_tool_calls(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Stream should accumulate multiple tool_use blocks."""
        # First tool
        mock_start1 = MagicMock(spec_set=SPEC["content_block_start"])
        mock_start1.type = "content_block_start"
        mock_block1 = MagicMock(
            spec_set=SPEC["tool_use"], type="tool_use", id="toolu_1"
        )
        mock_block1.name = "search"
        mock_start1.content_block = mock_block1

        mock_input1 = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_input1.type = "content_block_delta"
        mock_input1.delta = MagicMock(
            spec=SPEC["input_json_delta"],
            type="input_json_delta",
            partial_json='{"q": "a"}',
        )

        mock_stop1 = MagicMock(spec_set=SPEC["content_block_stop"])
        mock_stop1.type = "content_block_stop"

        # Second tool
        mock_start2 = MagicMock(spec_set=SPEC["content_block_start"])
        mock_start2.type = "content_block_start"
        mock_block2 = MagicMock(
            spec_set=SPEC["tool_use"], type="tool_use", id="toolu_2"
        )
        mock_block2.name = "fetch"
        mock_start2.content_block = mock_block2

        mock_input2 = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_input2.type = "content_block_delta"
        mock_input2.delta = MagicMock(
            spec=SPEC["input_json_delta"],
            type="input_json_delta",
            partial_json='{"url": "https://x.com"}',
        )

        mock_stop2 = MagicMock(spec_set=SPEC["content_block_stop"])
        mock_stop2.type = "content_block_stop"

        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
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

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[
                ToolDefinition(
                    name=ToolName("search"),
                    description="Search",
                    parameters={"type": "object", "properties": {}},
                ),
                ToolDefinition(
                    name=ToolName("fetch"),
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
        messages: list[dict[str, Any]] = [
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
                name=ToolName("tool_a"),
                description="Tool A",
                parameters={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name=ToolName("tool_b"),
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
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"],
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=0,
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
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
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"],
            input_tokens=50,
            output_tokens=10,
            cache_creation_input_tokens=2500,
            cache_read_input_tokens=0,
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        assert response.usage.input_tokens == 50
        assert response.usage.output_tokens == 10
        assert response.usage.cache_write_tokens == 2500
        assert response.usage.cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_complete_extracts_cache_read_tokens(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() should extract cache read tokens (cache hit scenario)."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"],
            input_tokens=50,
            output_tokens=10,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=2500,
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        assert response.usage.cache_write_tokens == 0
        assert response.usage.cache_read_tokens == 2500
        assert response.usage.total_tokens == 50 + 10 + 0 + 2500

    @pytest.mark.asyncio
    async def test_complete_handles_missing_cache_fields(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() reads absent cache fields — None on the SDK type — as 0."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hi")
        ]
        mock_usage = MagicMock(spec_set=SPEC["usage"])
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5
        mock_usage.cache_creation_input_tokens = None
        mock_usage.cache_read_input_tokens = None
        mock_response.usage = mock_usage
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        assert response.usage.cache_write_tokens == 0
        assert response.usage.cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_stream_sends_structured_system(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """stream() should send system as structured list with cache_control."""
        mock_event = MagicMock(spec_set=SPEC["message_stop"])
        mock_event.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            pass

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_stream_extracts_cache_tokens(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """stream() should extract cache tokens from message_start event."""
        # message_start with input + cache tokens
        mock_msg_start = MagicMock(spec_set=SPEC["message_start"])
        mock_msg_start.type = "message_start"
        mock_msg_start.message = MagicMock(spec_set=SPEC["message"])
        mock_msg_start.message.usage = MagicMock(
            spec=SPEC["usage"],
            input_tokens=50,
            cache_creation_input_tokens=2500,
            cache_read_input_tokens=0,
        )

        mock_text = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_text.type = "content_block_delta"
        mock_text.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="Hi"
        )

        # message_delta with output tokens
        mock_msg_delta = MagicMock(spec_set=SPEC["message_delta"])
        mock_msg_delta.type = "message_delta"
        mock_msg_delta.usage = MagicMock(spec_set=SPEC["usage"], output_tokens=10)

        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_msg_start
            yield mock_text
            yield mock_msg_delta
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            chunks.append(chunk)

        # Final chunk should have combined usage from message_start + message_delta
        final = chunks[-1]
        assert final.usage is not None
        assert final.usage.input_tokens == 50
        assert final.usage.output_tokens == 10
        assert final.usage.cache_write_tokens == 2500
        assert final.usage.cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_stream_yields_early_partial_usage_chunk(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """stream() should surface a usage-only chunk right after message_start
        so consumers interrupted mid-stream can meter input/cache tokens."""
        mock_msg_start = MagicMock(spec_set=SPEC["message_start"])
        mock_msg_start.type = "message_start"
        mock_msg_start.message = MagicMock(spec_set=SPEC["message"])
        mock_msg_start.message.usage = MagicMock(
            spec=SPEC["usage"],
            input_tokens=50,
            cache_creation_input_tokens=2500,
            cache_read_input_tokens=0,
        )

        mock_text = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_text.type = "content_block_delta"
        mock_text.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="Hi"
        )

        mock_msg_delta = MagicMock(spec_set=SPEC["message_delta"])
        mock_msg_delta.type = "message_delta"
        mock_msg_delta.usage = MagicMock(spec_set=SPEC["usage"], output_tokens=10)
        mock_msg_delta.delta = MagicMock(spec_set=SPEC["stop"], stop_reason="end_turn")

        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_msg_start
            yield mock_text
            yield mock_msg_delta
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            chunks.append(chunk)

        # First chunk is usage-only: partial usage, no content/finish_reason
        partial = chunks[0]
        assert partial.content is None
        assert partial.finish_reason is None
        assert partial.usage is not None
        assert partial.usage.input_tokens == 50
        assert partial.usage.output_tokens == 0
        assert partial.usage.cache_write_tokens == 2500

        # Final chunk still carries the complete usage (last-wins for consumers)
        final = chunks[-1]
        assert final.finish_reason == "end_turn"
        assert final.usage is not None
        assert final.usage.input_tokens == 50
        assert final.usage.output_tokens == 10
        assert final.usage.cache_write_tokens == 2500

    @pytest.mark.asyncio
    async def test_stream_chunks_carry_api_model(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """The message_start model string rides every subsequent chunk."""
        mock_msg_start = MagicMock(spec_set=SPEC["message_start"])
        mock_msg_start.type = "message_start"
        mock_msg_start.message = MagicMock(spec_set=SPEC["message"])
        mock_msg_start.message.model = "claude-sonnet-5-20260101"
        mock_msg_start.message.usage = MagicMock(
            spec=SPEC["usage"],
            input_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        mock_reasoning = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_reasoning.type = "content_block_delta"
        mock_reasoning.delta = MagicMock(
            spec=SPEC["thinking_delta"], type="thinking_delta", thinking="hmm"
        )

        mock_text = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_text.type = "content_block_delta"
        mock_text.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="Hi"
        )

        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_msg_start
            yield mock_reasoning
            yield mock_text
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            chunks.append(chunk)

        # partial-usage + reasoning + text + final = every construction site
        assert len(chunks) == 4
        assert all(c.model == "claude-sonnet-5-20260101" for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_tools_have_cache_control(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """stream() should send tools with cache_control on last tool."""
        mock_event = MagicMock(spec_set=SPEC["message_stop"])
        mock_event.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        tool_def = ToolDefinition(
            name=ToolName("calc"),
            description="Calculate",
            parameters={"type": "object", "properties": {}},
        )

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[tool_def],
        ):
            pass

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        tools = call_kwargs["tools"]
        assert tools[-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.unit
class TestStripUnsupportedConstraints:
    """Tests for _strip_unsupported_constraints schema sanitizer."""

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
        result = strip_unsupported_constraints(schema, strict=True)
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
        result = strip_unsupported_constraints(schema, strict=True)
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
        result = strip_unsupported_constraints(schema, strict=True)
        assert "exclusiveMinimum" not in result["properties"]["val"]
        assert "exclusiveMaximum" not in result["properties"]["val"]

    def test_strips_string_length_constraints_preserves_pattern(self) -> None:
        """minLength/maxLength stripped; pattern preserved (per strict-mode docs)."""
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
        result = strip_unsupported_constraints(schema, strict=True)
        prop = result["properties"]["name"]
        assert "minLength" not in prop
        assert "maxLength" not in prop
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
        result = strip_unsupported_constraints(schema, strict=True)
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
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minimum" not in result["anyOf"][0]["properties"]["count"]

    def test_does_not_mutate_original(self) -> None:
        """Original schema dict should not be modified."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 1, "maximum": 10},
            },
        }
        strip_unsupported_constraints(schema, strict=True)
        assert schema["properties"]["x"]["minimum"] == 1
        assert schema["properties"]["x"]["maximum"] == 10

    def test_convert_tools_strips_constraints(self, client: AnthropicClient) -> None:
        """Strict tools get full strict-mode stripping via _convert_tools."""
        tool = ToolDefinition(
            name=ToolName("caption"),
            description="Add captions",
            parameters={
                "type": "object",
                "properties": {
                    "fontsize": {"type": "integer", "minimum": 1, "maximum": 20},
                    "label": {"type": "string", "minLength": 1, "maxLength": 50},
                },
            },
            strict=True,
        )
        converted = client._convert_tools([tool])
        props = converted[0]["input_schema"]["properties"]
        assert "minimum" not in props["fontsize"]
        assert "maximum" not in props["fontsize"]
        # String length constraints also stripped under strict mode
        assert "minLength" not in props["label"]
        assert "maxLength" not in props["label"]
        # Original not mutated
        assert tool.parameters["properties"]["fontsize"]["minimum"] == 1
        assert tool.parameters["properties"]["label"]["minLength"] == 1

    def test_strips_minlength_on_strings(self) -> None:
        """minLength on string properties is stripped."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minLength" not in result["properties"]["name"]

    def test_strips_maxlength_on_strings(self) -> None:
        """maxLength on string properties is stripped."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 100},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "maxLength" not in result["properties"]["name"]

    def test_strips_multipleof_on_numbers(self) -> None:
        """multipleOf is stripped from integer/number properties."""
        schema = {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "multipleOf": 5},
                "f": {"type": "number", "multipleOf": 0.25},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "multipleOf" not in result["properties"]["n"]
        assert "multipleOf" not in result["properties"]["f"]

    def test_strips_minitems_above_one(self) -> None:
        """minItems > 1 is stripped from array properties."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "minItems": 3, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minItems" not in result["properties"]["tags"]

    def test_keeps_minitems_zero_or_one(self) -> None:
        """minItems of 0 or 1 is kept (allowed under strict mode)."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "array", "minItems": 0, "items": {"type": "string"}},
                "b": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert result["properties"]["a"]["minItems"] == 0
        assert result["properties"]["b"]["minItems"] == 1

    def test_strips_maxitems_above_one(self) -> None:
        """maxItems > 1 is stripped from array properties."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "maxItems" not in result["properties"]["tags"]

    def test_keeps_maxitems_zero_or_one(self) -> None:
        """maxItems of 0 or 1 is kept (allowed under strict mode)."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "array", "maxItems": 0, "items": {"type": "string"}},
                "b": {"type": "array", "maxItems": 1, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert result["properties"]["a"]["maxItems"] == 0
        assert result["properties"]["b"]["maxItems"] == 1

    def test_strict_false_preserves_minlength(self) -> None:
        """strict=False keeps minLength on string schemas."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
        }
        result = strip_unsupported_constraints(schema, strict=False)
        assert result["properties"]["name"]["minLength"] == 1

    def test_strict_false_preserves_multipleof(self) -> None:
        """strict=False keeps multipleOf on integer/number schemas."""
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer", "multipleOf": 5}},
        }
        result = strip_unsupported_constraints(schema, strict=False)
        assert result["properties"]["n"]["multipleOf"] == 5

    def test_strict_false_still_strips_numeric_minimum(self) -> None:
        """strict=False still strips minimum/maximum (Anthropic always rejects)."""
        schema = {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        }
        result = strip_unsupported_constraints(schema, strict=False)
        assert "minimum" not in result["properties"]["n"]
        assert "maximum" not in result["properties"]["n"]


@pytest.mark.unit
class TestAnthropicStrictToolUse:
    """Tests for strict tool use payload shape."""

    def test_strict_tool_emits_strict_flag(self, client: AnthropicClient) -> None:
        """A tool with strict=True declares strict: true on the wire."""
        tool = ToolDefinition(
            name=ToolName("get_weather"),
            description="Get the weather",
            parameters={"type": "object", "properties": {}},
            strict=True,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["strict"] is True

    def test_strict_tool_sets_additional_properties_false(
        self, client: AnthropicClient
    ) -> None:
        """Strict tool's object input_schema gets additionalProperties: false set."""
        tool = ToolDefinition(
            name=ToolName("get_weather"),
            description="Get the weather",
            parameters={"type": "object", "properties": {}},
            strict=True,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["input_schema"]["additionalProperties"] is False

    def test_strict_tool_preserves_existing_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """A strict tool that already declares additionalProperties is not overwritten."""
        tool = ToolDefinition(
            name=ToolName("passthrough"),
            description="Passthrough tool",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": True,
            },
            strict=True,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["input_schema"]["additionalProperties"] is True

    def test_non_strict_tool_omits_strict_key(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """Non-strict tools must not have a `strict` key on the wire."""
        # sample_tool fixture has strict=False (default)
        converted = client._convert_tools([sample_tool])
        assert "strict" not in converted[0]

    def test_non_strict_tool_omits_additional_properties_injection(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """Non-strict tools must not get auto-injected additionalProperties."""
        converted = client._convert_tools([sample_tool])
        assert "additionalProperties" not in converted[0]["input_schema"]

    def test_non_strict_tool_preserves_caller_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """If a caller declared additionalProperties on a non-strict tool, keep it."""
        tool = ToolDefinition(
            name=ToolName("passthrough"),
            description="Passthrough tool",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": True,
            },
            strict=False,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["input_schema"]["additionalProperties"] is True

    def test_mixed_strict_and_non_strict_tools_in_one_call(
        self, client: AnthropicClient
    ) -> None:
        """Each tool is treated independently when strict differs across the list."""
        strict_tool = ToolDefinition(
            name=ToolName("strict_one"),
            description="Strict tool",
            parameters={"type": "object", "properties": {}},
            strict=True,
        )
        non_strict_tool = ToolDefinition(
            name=ToolName("lax_one"),
            description="Non-strict tool",
            parameters={"type": "object", "properties": {}},
            strict=False,
        )
        converted = client._convert_tools([strict_tool, non_strict_tool])

        assert converted[0]["strict"] is True
        assert converted[0]["input_schema"]["additionalProperties"] is False

        assert "strict" not in converted[1]
        assert "additionalProperties" not in converted[1]["input_schema"]

    def test_non_strict_tool_preserves_string_length_constraints(
        self, client: AnthropicClient
    ) -> None:
        """Non-strict tools keep minLength/maxLength (only strict mode strips them)."""
        tool = ToolDefinition(
            name=ToolName("caption"),
            description="Add captions",
            parameters={
                "type": "object",
                "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": 50},
                },
            },
            strict=False,
        )
        converted = client._convert_tools([tool])
        label = converted[0]["input_schema"]["properties"]["label"]
        assert label["minLength"] == 1
        assert label["maxLength"] == 50

    def test_non_strict_tool_strips_only_always_unsupported_numeric(
        self, client: AnthropicClient
    ) -> None:
        """Non-strict tools still drop minimum/maximum (always rejected by Anthropic)
        but keep multipleOf (rejected only under strict mode)."""
        tool = ToolDefinition(
            name=ToolName("size"),
            description="Size in pixels",
            parameters={
                "type": "object",
                "properties": {
                    "px": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "multipleOf": 5,
                    },
                },
            },
            strict=False,
        )
        converted = client._convert_tools([tool])
        px = converted[0]["input_schema"]["properties"]["px"]
        assert "minimum" not in px
        assert "maximum" not in px
        assert px["multipleOf"] == 5


@pytest.mark.unit
class TestAnthropicCacheTtl:
    """The hour-long breakpoint (NC9 #227): opt-in, and invisible at 5m."""

    def test_the_default_is_the_bare_marker(self) -> None:
        """5m is the wire's own default, so the request does not change
        shape for an agent that never asked for anything else — which is
        what keeps every existing cache assertion true."""
        assert cache_control() == {"type": "ephemeral"}
        assert cache_control("5m") == {"type": "ephemeral"}

    def test_an_hour_adds_the_ttl_key(self) -> None:
        assert cache_control("1h") == {"type": "ephemeral", "ttl": "1h"}

    def test_every_breakpoint_carries_it(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """All four: the system prompt, the last message in both content
        shapes, and the tool block."""
        cached_system, messages = client._apply_cache_control(
            "You are helpful.",
            [{"role": "user", "content": "hi"}],
            ttl="1h",
        )
        assert cached_system is not None
        assert cached_system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert messages[-1]["content"][-1]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

        _, blocks = client._apply_cache_control(
            None,
            [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            ttl="1h",
        )
        assert blocks[-1]["content"][-1]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

        tools = client._convert_tools([sample_tool], "1h")
        assert tools[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    @pytest.mark.asyncio
    async def test_it_reaches_the_wire_on_the_ga_namespace(
        self,
        client: AnthropicClient,
        sample_messages: list[Message],
        sample_tool: ToolDefinition,
    ) -> None:
        """`ttl` is a GA parameter, so no beta is opened for it: a beta
        namespace would drag `betas` along and collide with compaction."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-sonnet-5"
        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[sample_tool],
            cache_ttl="1h",
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["system"][0]["cache_control"]["ttl"] == "1h"
        assert call_kwargs["tools"][-1]["cache_control"]["ttl"] == "1h"
        assert "betas" not in call_kwargs


@pytest.mark.unit
class TestAnthropicArgumentFragments:
    """`input_json_delta` reaches the caller as well as the buffer (#226)."""

    @pytest.mark.asyncio
    async def test_each_delta_is_forwarded_and_still_buffered(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        block_start = MagicMock(spec_set=SPEC["content_block_start"])
        block_start.type = "content_block_start"
        block = MagicMock(spec=SPEC["tool_use"], type="tool_use", id="toolu_abc")
        block.name = "get_weather"
        block_start.content_block = block

        deltas = []
        for piece in ('{"location":', ' "Paris"}'):
            event = MagicMock(spec_set=SPEC["content_block_delta"])
            event.type = "content_block_delta"
            event.delta = MagicMock(
                spec=SPEC["input_json_delta"],
                type="input_json_delta",
                partial_json=piece,
            )
            deltas.append(event)

        block_stop = MagicMock(spec_set=SPEC["content_block_stop"])
        block_stop.type = "content_block_stop"
        msg_delta = MagicMock(spec_set=SPEC["message_delta"])
        msg_delta.type = "message_delta"
        msg_delta.usage = MagicMock(spec=SPEC["usage"], input_tokens=0, output_tokens=5)
        msg_delta.delta = MagicMock(spec_set=SPEC["stop"], stop_reason="tool_use")
        msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        msg_stop.type = "message_stop"

        async def events() -> AsyncIterator[Any]:
            yield block_start
            for event in deltas:
                yield event
            yield block_stop
            yield msg_delta
            yield msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: events()
        _stub_stream(client, mock_stream)

        chunks = [
            chunk
            async for chunk in client.stream(
                messages=sample_messages, model=Model.CLAUDE_SONNET_5
            )
        ]

        fragments = [f for chunk in chunks for f in chunk.tool_call_fragments]
        assert [f.fragment for f in fragments] == ['{"location":', ' "Paris"}']
        # Every fragment names the call the block announced.
        assert {f.id for f in fragments} == {"toolu_abc"}
        assert {f.name for f in fragments} == {"get_weather"}
        # The buffer still decodes the finished call at message_stop.
        assert chunks[-1].tool_calls[0].arguments == {"location": "Paris"}

    @pytest.mark.asyncio
    async def test_text_deltas_carry_no_fragments(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        text_delta = MagicMock(spec_set=SPEC["content_block_delta"])
        text_delta.type = "content_block_delta"
        text_delta.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="hi"
        )
        msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        msg_stop.type = "message_stop"

        async def events() -> AsyncIterator[Any]:
            yield text_delta
            yield msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: events()
        _stub_stream(client, mock_stream)

        chunks = [
            chunk
            async for chunk in client.stream(
                messages=sample_messages, model=Model.CLAUDE_SONNET_5
            )
        ]
        assert all(chunk.tool_call_fragments == () for chunk in chunks)


@pytest.mark.unit
class TestAnthropicToolChoice:
    """The four choices are Anthropic's four types, and a choice only ever
    rides beside a tool list (NC9 #224)."""

    @pytest.mark.parametrize(
        ("choice", "expected"),
        [
            (ToolChoice.auto(), {"type": "auto"}),
            (ToolChoice.required(), {"type": "any"}),
            (ToolChoice.none(), {"type": "none"}),
            (ToolChoice.tool("get_weather"), {"type": "tool", "name": "get_weather"}),
            (
                ToolChoice.required(parallel=False),
                {"type": "any", "disable_parallel_tool_use": True},
            ),
        ],
        ids=["auto", "required", "none", "tool", "serial"],
    )
    def test_the_wire_shapes(
        self, choice: ToolChoice, expected: dict[str, Any]
    ) -> None:
        assert convert_tool_choice(choice) == expected

    def test_none_has_no_parallel_knob(self) -> None:
        """Nothing is called, so nothing can be parallel."""
        assert "disable_parallel_tool_use" not in convert_tool_choice(ToolChoice.none())

    @pytest.mark.asyncio
    async def test_the_choice_reaches_the_body_beside_the_tools(
        self,
        client: AnthropicClient,
        sample_messages: list[Message],
        sample_tool: ToolDefinition,
    ) -> None:
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-sonnet-5"
        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[sample_tool],
            tool_choice=ToolChoice.tool("get_weather"),
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "get_weather"}

    @pytest.mark.asyncio
    async def test_without_tools_no_choice_is_sent(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """A tool_choice with no tools is a 400 on the wire."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-sonnet-5"
        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tool_choice=ToolChoice.required(),
        )

        assert "tool_choice" not in _sdk(client).messages.stream.call_args.kwargs


@pytest.mark.unit
class TestAnthropicStructuredOutput:
    """Tests for GA structured-output payload shape (output_config.format)."""

    @pytest.mark.asyncio
    async def test_uses_output_config_format(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """response_format is sent under output_config.format on the GA endpoint."""
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Out(BaseModel):
            answer: str

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text='{"answer":"hi"}')
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            response_format=ResponseFormat(schema=Out),
        )

        _sdk(client).messages.stream.assert_called_once()
        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        # GA shape: format lives under output_config["format"]
        assert "output_config" in call_kwargs
        format_spec = call_kwargs["output_config"]["format"]
        assert format_spec["type"] == "json_schema"
        assert "schema" in format_spec
        # Beta-era keys must be absent
        assert "output_format" not in call_kwargs
        assert "betas" not in call_kwargs

    @pytest.mark.asyncio
    async def test_output_config_format_merges_with_reasoning_effort(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """When reasoning_effort and response_format both set, output_config holds both."""
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Out(BaseModel):
            answer: str

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text='{"answer":"hi"}')
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-opus-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            response_format=ResponseFormat(schema=Out),
            reasoning_effort=ReasoningEffort.HIGH,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        output_config = call_kwargs["output_config"]
        assert output_config["effort"] == "high"
        assert output_config["format"]["type"] == "json_schema"


@pytest.mark.unit
class TestAnthropicMultimodal:
    """Tests for image/document content block support."""

    def test_convert_messages_image_block_base64(self, client: AnthropicClient) -> None:
        """ImageBlock with base64 data maps to a base64 image source."""
        messages = [
            Message(
                role=Role.USER,
                content=[
                    ImageBlock(media_type="image/png", data="aGVsbG8="),
                    TextBlock(text="What is in this image?"),
                ],
            ),
        ]

        _, converted = client._convert_messages(messages)

        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        content = converted[0]["content"]
        assert content[0] == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "aGVsbG8=",
            },
        }
        assert content[1] == {"type": "text", "text": "What is in this image?"}

    def test_convert_messages_image_block_url(self, client: AnthropicClient) -> None:
        """ImageBlock with url maps to a url image source (no media_type)."""
        messages = [
            Message(
                role=Role.USER,
                content=[
                    ImageBlock(url="https://example.com/cat.png"),
                    TextBlock(text="Describe this image"),
                ],
            ),
        ]

        _, converted = client._convert_messages(messages)

        assert converted[0]["content"][0] == {
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/cat.png"},
        }

    def test_convert_messages_document_block_base64(
        self, client: AnthropicClient
    ) -> None:
        """DocumentBlock with base64 data maps to a base64 document source."""
        messages = [
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                    TextBlock(text="Transcribe this document."),
                ],
            ),
        ]

        _, converted = client._convert_messages(messages)

        assert converted[0]["content"][0] == {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": "JVBERi0=",
            },
        }

    def test_convert_messages_document_block_url(self, client: AnthropicClient) -> None:
        """DocumentBlock with url maps to a url document source."""
        messages = [
            Message(
                role=Role.USER,
                content=[DocumentBlock(url="https://example.com/paper.pdf")],
            ),
        ]

        _, converted = client._convert_messages(messages)

        assert converted[0]["content"][0] == {
            "type": "document",
            "source": {"type": "url", "url": "https://example.com/paper.pdf"},
        }

    def test_convert_messages_preserves_block_order(
        self, client: AnthropicClient
    ) -> None:
        """Caller block order is preserved — no silent reordering."""
        messages = [
            Message(
                role=Role.USER,
                content=[
                    TextBlock(text="The document below:"),
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                ],
            ),
        ]

        _, converted = client._convert_messages(messages)

        content = converted[0]["content"]
        assert [block["type"] for block in content] == ["text", "document"]

    def test_convert_messages_plain_str_unchanged(
        self, client: AnthropicClient
    ) -> None:
        """Regression: plain-str user content stays a bare string, not a list."""
        _, converted = client._convert_messages(
            [Message(role=Role.USER, content="Hello!")]
        )
        assert converted[0]["content"] == "Hello!"

    def test_system_block_content_raises(self, client: AnthropicClient) -> None:
        """SYSTEM messages must stay plain-str content."""
        messages = [
            Message(role=Role.SYSTEM, content=[TextBlock(text="Be helpful.")]),
        ]
        with pytest.raises(UnsupportedContentError):
            client._convert_messages(messages)

    def test_assistant_media_block_content_raises(
        self, client: AnthropicClient
    ) -> None:
        """ASSISTANT block content admits text/compaction only (N4);
        media on the assistant role still raises."""
        messages = [
            Message(
                role=Role.ASSISTANT,
                content=[
                    ImageBlock(media_type="image/png", data="aWc="),
                ],
            ),
        ]
        with pytest.raises(UnsupportedContentError):
            client._convert_messages(messages)

    def test_tool_block_content_raises(self, client: AnthropicClient) -> None:
        """TOOL messages must stay plain-str content."""
        messages = [
            Message(
                role=Role.TOOL,
                content=[TextBlock(text="result")],
                tool_call_id=ToolCallId("call_123"),
            ),
        ]
        with pytest.raises(UnsupportedContentError):
            client._convert_messages(messages)

    def test_cache_control_on_last_block_of_block_list(
        self, client: AnthropicClient
    ) -> None:
        """The breakpoint lands on the final converted block only."""
        messages = [
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                    TextBlock(text="Transcribe."),
                ],
            ),
        ]
        system_prompt, converted = client._convert_messages(messages)

        _, cached = client._apply_cache_control(system_prompt, converted)

        content = cached[-1]["content"]
        assert "cache_control" not in content[0]
        assert content[1]["cache_control"] == {"type": "ephemeral"}

    def test_cache_control_on_trailing_document_block(
        self, client: AnthropicClient
    ) -> None:
        """A trailing media block validly carries the breakpoint."""
        messages = [
            Message(
                role=Role.USER,
                content=[
                    TextBlock(text="The document below:"),
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                ],
            ),
        ]
        system_prompt, converted = client._convert_messages(messages)

        _, cached = client._apply_cache_control(system_prompt, converted)

        content = cached[-1]["content"]
        assert content[-1]["type"] == "document"
        assert content[-1]["cache_control"] == {"type": "ephemeral"}

    def test_cache_last_message_false_skips_breakpoint(
        self, client: AnthropicClient
    ) -> None:
        """cache_last_message=False leaves messages unmarked; system still cached."""
        messages = [
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                    TextBlock(text="Transcribe."),
                ],
            ),
        ]
        system_prompt, converted = client._convert_messages(
            [Message(role=Role.SYSTEM, content="You transcribe PDFs."), *messages]
        )

        cached_system, cached = client._apply_cache_control(
            system_prompt, converted, cache_last_message=False
        )

        assert cached_system is not None
        assert cached_system[0]["cache_control"] == {"type": "ephemeral"}
        for block in cached[-1]["content"]:
            assert "cache_control" not in block

    def test_cache_last_message_false_plain_str_untouched(
        self, client: AnthropicClient
    ) -> None:
        """cache_last_message=False keeps plain-str content as a bare string."""
        _, cached = client._apply_cache_control(
            None,
            [{"role": "user", "content": "Hello!"}],
            cache_last_message=False,
        )
        assert cached[-1]["content"] == "Hello!"

    @pytest.mark.asyncio
    async def test_unsupported_model_raises_for_documents(
        self,
        client: AnthropicClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """complete() raises before any API call when the model lacks support."""
        from neosian._foundation.shared.models import _MODEL_SPECS, ModelSpec, Provider

        monkeypatch.setitem(
            _MODEL_SPECS,
            Model.CLAUDE_HAIKU_4_5.value,
            ModelSpec(
                provider=Provider.ANTHROPIC,
                context_window=200_000,
                max_output_tokens=64_000,
                supports_images=True,
                supports_documents=False,
            ),
        )
        _sdk(client).messages.stream = MagicMock()

        messages = [
            Message(
                role=Role.USER,
                content=[DocumentBlock(media_type="application/pdf", data="JVBERi0=")],
            ),
        ]

        with pytest.raises(UnsupportedContentError, match="document"):
            await client.complete(messages=messages, model=Model.CLAUDE_HAIKU_4_5)

        _sdk(client).messages.stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_surfaces_stop_reason(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() passes the API's stop_reason through."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Truncated...")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=10, output_tokens=5
        )
        mock_response.model = "claude-sonnet-5"
        mock_response.stop_reason = "max_tokens"

        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        assert response.stop_reason == "max_tokens"

    @pytest.mark.asyncio
    async def test_complete_never_calls_messages_create(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """complete() streams internally — messages.create must not be used."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=1, output_tokens=1
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)
        _sdk(client).messages.create = AsyncMock()

        await client.complete(messages=sample_messages, model=Model.CLAUDE_SONNET_5)

        _sdk(client).messages.create.assert_not_called()
        _sdk(client).messages.stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_passes_cache_conversation(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """cache_conversation=False must reach _apply_cache_control."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="Hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=1, output_tokens=1
        )
        mock_response.model = "claude-sonnet-5"

        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            cache_conversation=False,
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        # Last message keeps its plain-str content (no breakpoint applied)
        assert call_kwargs["messages"][-1]["content"] == "Hello!"

    @pytest.mark.asyncio
    async def test_stream_surfaces_real_stop_reason(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """The final chunk carries the API's stop_reason, not a synthesized one."""
        mock_text_delta = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_text_delta.type = "content_block_delta"
        mock_text_delta.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="Truncated"
        )

        mock_msg_delta = MagicMock(spec_set=SPEC["message_delta"])
        mock_msg_delta.type = "message_delta"
        mock_msg_delta.usage = MagicMock(spec_set=SPEC["usage"], output_tokens=5)
        mock_msg_delta.delta = MagicMock(
            spec_set=SPEC["stop"], stop_reason="max_tokens"
        )

        mock_msg_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_msg_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_text_delta
            yield mock_msg_delta
            yield mock_msg_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()

        _stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            chunks.append(chunk)

        assert chunks[-1].finish_reason == "max_tokens"


@pytest.mark.unit
class TestAnthropicSchemaAdditionalProperties:
    """Every object in an output-format schema must carry additionalProperties.

    Anthropic rejects object schemas without it — including nested models
    under $defs, and regardless of strict mode.
    """

    def _nested_format(self, strict: bool = True) -> object:
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Question(BaseModel):
            question: str
            options: list[str]

        class Quiz(BaseModel):
            questions: list[Question]

        return ResponseFormat(schema=Quiz, strict=strict)

    def test_nested_model_defs_carry_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """Regression: $defs objects reached the API without the flag (400)."""
        payload = client._convert_response_format(self._nested_format())  # type: ignore[arg-type]
        schema = payload["schema"]

        assert schema["additionalProperties"] is False  # type: ignore[index]
        assert schema["$defs"]["Question"]["additionalProperties"] is False  # type: ignore[index]

    def test_non_strict_schema_still_carries_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """Regression: the root patch used to be gated on strict, so
        strict=False sent an object schema without the flag and 400'd."""
        payload = client._convert_response_format(
            self._nested_format(strict=False)  # type: ignore[arg-type]
        )
        schema = payload["schema"]

        assert schema["additionalProperties"] is False  # type: ignore[index]
        assert schema["$defs"]["Question"]["additionalProperties"] is False  # type: ignore[index]


@pytest.mark.unit
class TestNativeToolType:
    """Tools marked native_type ride the schema-less native declaration."""

    def _native(self) -> ToolDefinition:
        return ToolDefinition(
            name=ToolName("memory"),
            description="ignored on the wire under native transport",
            parameters={"type": "object", "properties": {}},
            native_type="memory_20250818",
        )

    def test_native_wire_shape(self, client: AnthropicClient) -> None:
        """A native entry is exactly {type, name} plus the cache breakpoint."""
        converted = client._convert_tools([self._native()])
        assert converted == [
            {
                "type": "memory_20250818",
                "name": "memory",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_mixed_list_keeps_function_schema_beside_native(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        converted = client._convert_tools([sample_tool, self._native()])
        assert converted[0]["name"] == "get_weather"
        assert converted[0]["description"] == "Get the weather for a location"
        assert "input_schema" in converted[0]
        assert "cache_control" not in converted[0]
        assert converted[1] == {
            "type": "memory_20250818",
            "name": "memory",
            "cache_control": {"type": "ephemeral"},
        }

    def test_unmarked_definition_is_unchanged(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """Regression: native_type=None produces today's exact shape."""
        converted = client._convert_tools([sample_tool])
        assert converted == [
            {
                "name": "get_weather",
                "description": "Get the weather for a location",
                "input_schema": sample_tool.parameters,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @pytest.mark.asyncio
    async def test_complete_sends_native_entry_without_betas(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """The native memory tool is GA — no beta header, no schema."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="ok")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=1, output_tokens=1
        )
        mock_response.model = "claude-sonnet-5"
        _mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[self._native()],
        )

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["tools"] == [
            {
                "type": "memory_20250818",
                "name": "memory",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        assert "betas" not in call_kwargs

    @pytest.mark.asyncio
    async def test_stream_sends_the_same_native_entry(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        mock_delta = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_delta.type = "content_block_delta"
        mock_delta.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="ok"
        )
        mock_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_delta
            yield mock_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()
        _stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[self._native()],
        ):
            pass

        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["tools"][0] == {
            "type": "memory_20250818",
            "name": "memory",
            "cache_control": {"type": "ephemeral"},
        }
        assert "betas" not in call_kwargs


@pytest.mark.unit
class TestServerCompaction:
    """The compact beta opt-in: namespace switch, block round-trip, spend."""

    def _response(self, content_blocks: list[Any], usage: Any = None) -> MagicMock:
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = content_blocks
        mock_response.usage = (
            usage
            if usage is not None
            else MagicMock(spec_set=SPEC["usage"], input_tokens=10, output_tokens=5)
        )
        mock_response.model = "claude-sonnet-5"
        return mock_response

    def _mock_beta(
        self, client: AnthropicClient, mock_response: MagicMock
    ) -> MagicMock:
        inner = MagicMock()
        inner.get_final_message = AsyncMock(return_value=mock_response)
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=inner)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        beta_stream = MagicMock(return_value=mock_stream)
        _sdk(client).beta.messages.stream = beta_stream
        return beta_stream

    @pytest.mark.asyncio
    async def test_flag_rides_the_beta_namespace(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        ga_stream = MagicMock()
        _sdk(client).messages.stream = ga_stream
        beta_stream = self._mock_beta(
            client,
            self._response([MagicMock(spec_set=SPEC["text"], type="text", text="ok")]),
        )
        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            server_compaction=True,
        )
        assert not ga_stream.called
        call_kwargs = beta_stream.call_args.kwargs
        assert call_kwargs["betas"] == ["compact-2026-01-12"]
        assert call_kwargs["context_management"] == {
            "edits": [{"type": "compact_20260112"}]
        }

    @pytest.mark.asyncio
    async def test_flag_off_stays_on_the_ga_namespace(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """Regression: default requests are byte-identical to before."""
        mock_response = self._response(
            [MagicMock(spec_set=SPEC["text"], type="text", text="ok")]
        )
        _mock_complete(client, mock_response)
        beta_stream = MagicMock()
        _sdk(client).beta.messages.stream = beta_stream
        await client.complete(messages=sample_messages, model=Model.CLAUDE_SONNET_5)
        assert not beta_stream.called
        call_kwargs = _sdk(client).messages.stream.call_args.kwargs
        assert "betas" not in call_kwargs
        assert "context_management" not in call_kwargs

    def test_parse_with_compaction_block_is_an_ordered_list(
        self, client: AnthropicClient
    ) -> None:
        comp = MagicMock(spec_set=SPEC["compaction"])
        comp.type = "compaction"
        comp.content = "summary of earlier turns"
        comp.encrypted_content = None
        parsed = client._parse_response(
            self._response(
                [comp, MagicMock(spec_set=SPEC["text"], type="text", text="hello")]
            )
        )
        assert parsed.message.content == [
            CompactionBlock(content="summary of earlier turns"),
            TextBlock(text="hello"),
        ]

    def test_parse_without_compaction_stays_a_plain_string(
        self, client: AnthropicClient
    ) -> None:
        parsed = client._parse_response(
            self._response(
                [MagicMock(spec_set=SPEC["text"], type="text", text="hello")]
            )
        )
        assert parsed.message.content == "hello"

    def test_compaction_iteration_spend_folds_into_usage(
        self, client: AnthropicClient
    ) -> None:
        """The beta reports summarization tokens only under
        usage.iterations — hiding them would hide real spend."""
        compaction_iter = MagicMock(spec_set=SPEC["compaction_iteration"])
        compaction_iter.type = "compaction"
        compaction_iter.input_tokens = 100
        compaction_iter.output_tokens = 50
        compaction_iter.cache_read_input_tokens = 7
        compaction_iter.cache_creation_input_tokens = 3
        message_iter = MagicMock(spec_set=SPEC["message_iteration"])
        message_iter.type = "message"
        message_iter.input_tokens = 10
        message_iter.output_tokens = 5
        usage = MagicMock(spec_set=SPEC["beta_usage"], input_tokens=10, output_tokens=5)
        usage.cache_read_input_tokens = 0
        usage.cache_creation_input_tokens = 0
        usage.iterations = [compaction_iter, message_iter]
        parsed = client._parse_response(
            self._response(
                [MagicMock(spec_set=SPEC["text"], type="text", text="ok")], usage=usage
            )
        )
        assert parsed.usage.input_tokens == 110
        assert parsed.usage.output_tokens == 55
        assert parsed.usage.cache_read_tokens == 7
        assert parsed.usage.cache_write_tokens == 3

    def test_assistant_block_list_round_trips_in_order(
        self, client: AnthropicClient
    ) -> None:
        message = Message(
            role=Role.ASSISTANT,
            content=[
                CompactionBlock(content="summary", encrypted_content="enc"),
                TextBlock(text="continuing"),
            ],
            tool_calls=[
                ToolCall(
                    id=ToolCallId("c1"),
                    name=ToolName("f"),
                    arguments={},
                )
            ],
        )
        _, converted = client._convert_messages([message])
        assert converted[0]["content"] == [
            {"type": "compaction", "content": "summary", "encrypted_content": "enc"},
            {"type": "text", "text": "continuing"},
            {"type": "tool_use", "id": "c1", "name": "f", "input": {}},
        ]

    def test_encrypted_content_key_omitted_when_absent(
        self, client: AnthropicClient
    ) -> None:
        message = Message(
            role=Role.ASSISTANT, content=[CompactionBlock(content="summary")]
        )
        _, converted = client._convert_messages([message])
        assert converted[0]["content"] == [{"type": "compaction", "content": "summary"}]

    def test_compaction_block_in_user_content_raises(
        self, client: AnthropicClient
    ) -> None:
        """Never silently dropped — the media-block rule."""
        with pytest.raises(UnsupportedContentError):
            client._convert_messages(
                [Message(role=Role.USER, content=[CompactionBlock(content="x")])]
            )

    @pytest.mark.asyncio
    async def test_stream_compaction_delta_is_assignment(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """The delta carries the full value — the second delta wins whole,
        never concatenates onto the first."""
        start = MagicMock(spec_set=SPEC["content_block_start"])
        start.type = "content_block_start"
        start.content_block = MagicMock(spec_set=SPEC["compaction"])
        start.content_block.type = "compaction"
        start.content_block.content = None
        start.content_block.encrypted_content = None
        delta_one = MagicMock(spec_set=SPEC["content_block_delta"])
        delta_one.type = "content_block_delta"
        delta_one.delta = MagicMock(spec_set=SPEC["compaction_delta"])
        delta_one.delta.type = "compaction_delta"
        delta_one.delta.content = "partial"
        delta_two = MagicMock(spec_set=SPEC["content_block_delta"])
        delta_two.type = "content_block_delta"
        delta_two.delta = MagicMock(spec_set=SPEC["compaction_delta"])
        delta_two.delta.type = "compaction_delta"
        delta_two.delta.content = "the full summary"
        stop = MagicMock(spec_set=SPEC["content_block_stop"])
        stop.type = "content_block_stop"
        message_stop = MagicMock(spec_set=SPEC["message_stop"])
        message_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield start
            yield delta_one
            yield delta_two
            yield stop
            yield message_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()
        beta_stream = MagicMock(return_value=mock_stream)
        _sdk(client).beta.messages.stream = beta_stream

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            server_compaction=True,
        ):
            chunks.append(chunk)

        assert beta_stream.call_args.kwargs["betas"] == ["compact-2026-01-12"]
        assert chunks[-1].compaction == (CompactionBlock(content="the full summary"),)

    @pytest.mark.asyncio
    async def test_flag_on_haiku_raises(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        with pytest.raises(UnsupportedParameterError):
            await client.complete(
                messages=sample_messages,
                model=Model.CLAUDE_HAIKU_4_5,
                server_compaction=True,
            )

    @pytest.mark.asyncio
    async def test_compaction_history_on_haiku_raises(
        self, client: AnthropicClient
    ) -> None:
        messages = [
            Message(role=Role.USER, content="hi"),
            Message(role=Role.ASSISTANT, content=[CompactionBlock(content="summary")]),
        ]
        with pytest.raises(UnsupportedContentError):
            await client.complete(messages=messages, model=Model.CLAUDE_HAIKU_4_5)


def _block(kind: str, **fields: Any) -> MagicMock:
    block = MagicMock(spec_set=SPEC[kind])
    block.type = kind
    for name, value in fields.items():
        setattr(block, name, value)
    return block


def _event(kind: str, **fields: Any) -> MagicMock:
    event = MagicMock(spec_set=SPEC[kind])
    event.type = kind
    for name, value in fields.items():
        setattr(event, name, value)
    return event


@pytest.mark.unit
class TestThinkingEcho:
    """Thinking blocks survive a turn whole, signatures included, under
    `Message.extra["anthropic"]`, and lead the next assistant message
    (LL-1, NF #172)."""

    _EXTRA = {
        "anthropic": {
            "thinking_blocks": [
                {"type": "thinking", "thinking": "Let me see.", "signature": "sig-1"},
                {"type": "redacted_thinking", "data": "enc"},
            ]
        }
    }

    async def test_complete_keeps_blocks_with_signatures(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            _block("thinking", thinking="Let me see.", signature="sig-1"),
            _block("redacted_thinking", data="enc"),
            _block("text", text="42."),
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=1, output_tokens=1
        )
        mock_response.model = "claude-opus-5"
        _mock_complete(client, mock_response)

        response = await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        assert response.message.reasoning == "Let me see."
        assert response.message.extra == self._EXTRA

    async def test_a_turn_without_thinking_sets_nothing(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [_block("text", text="42.")]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=1, output_tokens=1
        )
        mock_response.model = "claude-opus-5"
        _mock_complete(client, mock_response)
        response = await client.complete(
            messages=sample_messages, model=Model.CLAUDE_OPUS_5
        )
        assert response.message.extra is None

    async def test_stream_assembles_the_block_on_the_terminal_chunk(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        events = [
            _event("content_block_start", content_block=_block("thinking")),
            _event(
                "content_block_delta",
                delta=MagicMock(
                    spec=SPEC["thinking_delta"], type="thinking_delta", thinking="Let "
                ),
            ),
            _event(
                "content_block_delta",
                delta=MagicMock(
                    spec=SPEC["thinking_delta"], type="thinking_delta", thinking="me."
                ),
            ),
            _event(
                "content_block_delta",
                delta=MagicMock(
                    spec=SPEC["signature_delta"],
                    type="signature_delta",
                    signature="sig-1",
                ),
            ),
            _event("content_block_stop"),
            _event(
                "content_block_start",
                content_block=_block("redacted_thinking", data="enc"),
            ),
            _event("content_block_stop"),
            _event(
                "content_block_delta",
                delta=MagicMock(spec=SPEC["text_delta"], type="text_delta", text="42."),
            ),
            _event("message_stop"),
        ]

        async def mock_stream_events() -> AsyncIterator[Any]:
            for event in events:
                yield event

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()
        _stub_stream(client, mock_stream)

        chunks = [
            chunk
            async for chunk in client.stream(
                messages=sample_messages,
                model=Model.CLAUDE_OPUS_5,
                reasoning_effort=ReasoningEffort.HIGH,
            )
        ]

        assert "".join(c.reasoning or "" for c in chunks) == "Let me."
        assert all(c.extra is None for c in chunks[:-1])
        assert chunks[-1].extra == {
            "anthropic": {
                "thinking_blocks": [
                    {"type": "thinking", "thinking": "Let me.", "signature": "sig-1"},
                    {"type": "redacted_thinking", "data": "enc"},
                ]
            }
        }

    def test_convert_echoes_the_blocks_first(self, client: AnthropicClient) -> None:
        call = ToolCall(id=ToolCallId("c1"), name=ToolName("look"), arguments={})
        history = [
            Message(role=Role.USER, content="hi"),
            Message(role=Role.ASSISTANT, content="thinking done", extra=self._EXTRA),
            Message(role=Role.USER, content="more"),
            Message(role=Role.ASSISTANT, tool_calls=[call], extra=self._EXTRA),
        ]
        _, converted = client._convert_messages(history)
        blocks = self._EXTRA["anthropic"]["thinking_blocks"]
        assert converted[1]["content"] == [
            *blocks,
            {"type": "text", "text": "thinking done"},
        ]
        assert converted[3]["content"] == [
            *blocks,
            {"type": "tool_use", "id": "c1", "name": "look", "input": {}},
        ]

    def test_convert_without_extra_is_unchanged(self, client: AnthropicClient) -> None:
        _, converted = client._convert_messages(
            [Message(role=Role.ASSISTANT, content="plain")]
        )
        assert converted == [{"role": "assistant", "content": "plain"}]
