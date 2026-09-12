"""The reader (`anthropic_stream.py`): final messages and stream events into chunks, thinking kept whole."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import (
    Message,
    Role,
    ToolCall,
    ToolDefinition,
)
from neosian._foundation.shared.exceptions import ProviderError
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ToolCallId,
    ToolName,
)
from tests.unit.llm.anthropic.mocks import mock_complete, sdk, stub_stream
from tests.unit.llm.sdk_specs import ANTHROPIC as SPEC


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

        mock_complete(client, mock_response)

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

        mock_complete(client, mock_response)

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

        mock_complete(client, mock_response)

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

        stub_stream(client, mock_stream)

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

        stub_stream(client, mock_stream)

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
        stub_stream(client, mock_stream)

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

        stub_stream(client, mock_stream)

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

        stub_stream(client, mock_stream)

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

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        stub_stream(client, mock_stream)

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
        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        stub_stream(client, mock_stream)

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
        stub_stream(client, mock_stream)

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
        stub_stream(client, mock_stream)

        chunks = [
            chunk
            async for chunk in client.stream(
                messages=sample_messages, model=Model.CLAUDE_SONNET_5
            )
        ]
        assert all(chunk.tool_call_fragments == () for chunk in chunks)


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
        mock_complete(client, mock_response)

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
        mock_complete(client, mock_response)
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
        stub_stream(client, mock_stream)

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
