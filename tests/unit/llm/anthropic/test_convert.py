"""The converter (`anthropic_convert.py`): messages, media and cache breakpoints onto the wire."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.anthropic_tools import cache_control
from neosian._foundation.llm.base import (
    DocumentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolDefinition,
)
from neosian._foundation.shared.exceptions import UnsupportedContentError
from neosian._foundation.shared.types import (
    Model,
    ToolCallId,
    ToolName,
)
from tests.unit.llm.anthropic.mocks import mock_complete, sdk, stub_stream
from tests.unit.llm.sdk_specs import ANTHROPIC as SPEC


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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        mock_complete(client, mock_response)

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

        mock_complete(client, mock_response)

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

        mock_complete(client, mock_response)

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

        stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            pass

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        stub_stream(client, mock_stream)

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

        stub_stream(client, mock_stream)

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

        stub_stream(client, mock_stream)

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

        stub_stream(client, mock_stream)

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

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        tools = call_kwargs["tools"]
        assert tools[-1]["cache_control"] == {"type": "ephemeral"}


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
        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[sample_tool],
            cache_ttl="1h",
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["system"][0]["cache_control"]["ttl"] == "1h"
        assert call_kwargs["tools"][-1]["cache_control"]["ttl"] == "1h"
        assert "betas" not in call_kwargs


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
        sdk(client).messages.stream = MagicMock()

        messages = [
            Message(
                role=Role.USER,
                content=[DocumentBlock(media_type="application/pdf", data="JVBERi0=")],
            ),
        ]

        with pytest.raises(UnsupportedContentError, match="document"):
            await client.complete(messages=messages, model=Model.CLAUDE_HAIKU_4_5)

        sdk(client).messages.stream.assert_not_called()

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

        mock_complete(client, mock_response)

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

        mock_complete(client, mock_response)
        sdk(client).messages.create = AsyncMock()

        await client.complete(messages=sample_messages, model=Model.CLAUDE_SONNET_5)

        sdk(client).messages.create.assert_not_called()
        sdk(client).messages.stream.assert_called_once()

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

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            cache_conversation=False,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
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

        stub_stream(client, mock_stream)

        chunks = []
        async for chunk in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
        ):
            chunks.append(chunk)

        assert chunks[-1].finish_reason == "max_tokens"
