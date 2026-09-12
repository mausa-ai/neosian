"""The client: complete() and stream() over the SDK, reasoning effort, server compaction."""

import dataclasses
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import (
    CompactionBlock,
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
    ToolName,
)
from neosian._foundation.tools.result import ToolResult
from tests.unit.llm.anthropic.mocks import mock_complete, sdk, stub_stream
from tests.unit.llm.sdk_specs import ANTHROPIC as SPEC


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

        mock_complete(client, mock_response)

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

        mock_complete(client, mock_response)

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

        stub_stream(client, mock_stream)

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
        stub_stream(client, mock_stream)

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
        assert sdk(client).messages.stream.call_count == 1


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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        sdk(client).messages.stream.assert_called_once()
        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_HAIKU_4_5,
            temperature=0.3,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_HAIKU_4_5,
            temperature=0.3,
        ):
            pass

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.MEDIUM,
        ):
            chunks.append(chunk)

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            reasoning_effort=ReasoningEffort.HIGH,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

        with patch.dict(
            models_module._MODEL_SPECS, {Model.CLAUDE_SONNET_5.value: patched}
        ):
            await client.complete(
                messages=sample_messages,
                model=Model.CLAUDE_SONNET_5,
                reasoning_effort=ReasoningEffort.MAX,
            )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            reasoning_effort=ReasoningEffort.MAX,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            reasoning_effort=ReasoningEffort.MAX,
        ):
            pass

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["output_config"] == {"effort": "max"}


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
        sdk(client).beta.messages.stream = beta_stream
        return beta_stream

    @pytest.mark.asyncio
    async def test_flag_rides_the_beta_namespace(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        ga_stream = MagicMock()
        sdk(client).messages.stream = ga_stream
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
        mock_complete(client, mock_response)
        beta_stream = MagicMock()
        sdk(client).beta.messages.stream = beta_stream
        await client.complete(messages=sample_messages, model=Model.CLAUDE_SONNET_5)
        assert not beta_stream.called
        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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
        sdk(client).beta.messages.stream = beta_stream

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
