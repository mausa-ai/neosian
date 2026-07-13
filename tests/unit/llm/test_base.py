"""Tests for LLM base protocol."""

import json

import pytest

from neosian._foundation.llm.base import (
    CompletionResponse,
    DocumentBlock,
    ImageBlock,
    Message,
    Role,
    StreamChunk,
    TextBlock,
    ToolCall,
    ToolDefinition,
    Usage,
    content_to_json,
    required_content_types,
    text_of,
)
from neosian._foundation.shared.types import ToolCallId, ToolName


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

    def test_add_sums_all_fields(self) -> None:
        """Usage + Usage should sum all four token fields."""
        a = Usage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=300,
        )
        b = Usage(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=30,
        )

        total = a + b

        assert total.input_tokens == 110
        assert total.output_tokens == 55
        assert total.cache_creation_input_tokens == 220
        assert total.cache_read_input_tokens == 330
        assert total.total_tokens == 715


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
            model="llama-3.3-70b-versatile",
        )
        assert response.message.content == "Hi!"
        assert response.usage.total_tokens == 15
        assert response.model == "llama-3.3-70b-versatile"

    def test_stop_reason_defaults_to_none(self) -> None:
        """stop_reason should default to None for backward compatibility."""
        response = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hi!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="claude-sonnet-5",
        )
        assert response.stop_reason is None

    def test_stop_reason_passthrough(self) -> None:
        """stop_reason should carry the provider-native value."""
        response = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hi!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="claude-sonnet-5",
            stop_reason="max_tokens",
        )
        assert response.stop_reason == "max_tokens"


@pytest.mark.unit
class TestContentBlocks:
    """Test TextBlock, ImageBlock, and DocumentBlock validation."""

    def test_image_block_base64(self) -> None:
        """ImageBlock with media_type and data is valid."""
        block = ImageBlock(media_type="image/png", data="aGVsbG8=")
        assert block.media_type == "image/png"
        assert block.data == "aGVsbG8="
        assert block.url is None

    def test_image_block_url(self) -> None:
        """ImageBlock with url only is valid (no media_type needed)."""
        block = ImageBlock(url="https://example.com/cat.png")
        assert block.url == "https://example.com/cat.png"
        assert block.data is None

    def test_image_block_requires_exactly_one_source(self) -> None:
        """Both or neither of data/url must raise."""
        with pytest.raises(ValueError, match="exactly one"):
            ImageBlock(media_type="image/png")
        with pytest.raises(ValueError, match="exactly one"):
            ImageBlock(
                media_type="image/png",
                data="aGVsbG8=",
                url="https://example.com/cat.png",
            )

    def test_image_block_data_requires_media_type(self) -> None:
        """Base64 data without media_type must raise."""
        with pytest.raises(ValueError, match="media_type"):
            ImageBlock(data="aGVsbG8=")

    def test_image_block_strips_base64_whitespace(self) -> None:
        """Newlines/whitespace in base64 are normalized (Anthropic rejects them)."""
        block = ImageBlock(media_type="image/png", data="aGVs\nbG8=\n")
        assert block.data == "aGVsbG8="

    def test_document_block_base64(self) -> None:
        """DocumentBlock with media_type and data is valid."""
        block = DocumentBlock(media_type="application/pdf", data="JVBERi0=")
        assert block.media_type == "application/pdf"
        assert block.data == "JVBERi0="

    def test_document_block_requires_exactly_one_source(self) -> None:
        """Both or neither of data/url must raise."""
        with pytest.raises(ValueError, match="exactly one"):
            DocumentBlock(media_type="application/pdf")

    def test_document_block_data_requires_media_type(self) -> None:
        """Base64 data without media_type must raise."""
        with pytest.raises(ValueError, match="media_type"):
            DocumentBlock(data="JVBERi0=")

    def test_document_block_strips_base64_whitespace(self) -> None:
        """Newlines/whitespace in base64 are normalized."""
        block = DocumentBlock(media_type="application/pdf", data="JVBE\nRi0=\n")
        assert block.data == "JVBERi0="


@pytest.mark.unit
class TestTextOf:
    """Test text_of helper."""

    def test_plain_str(self) -> None:
        assert text_of(Message(role=Role.USER, content="Hello")) == "Hello"

    def test_none(self) -> None:
        assert text_of(Message(role=Role.USER, content=None)) == ""

    def test_blocks_joins_text(self) -> None:
        msg = Message(
            role=Role.USER,
            content=[TextBlock(text="First"), TextBlock(text="Second")],
        )
        assert text_of(msg) == "First\nSecond"

    def test_media_only_returns_empty(self) -> None:
        msg = Message(
            role=Role.USER,
            content=[DocumentBlock(media_type="application/pdf", data="JVBERi0=")],
        )
        assert text_of(msg) == ""

    def test_mixed_blocks_skip_media(self) -> None:
        msg = Message(
            role=Role.USER,
            content=[
                DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                TextBlock(text="Transcribe this."),
            ],
        )
        assert text_of(msg) == "Transcribe this."


@pytest.mark.unit
class TestContentToJson:
    """Test content_to_json helper."""

    def test_str_and_none_pass_through(self) -> None:
        assert content_to_json("Hello") == "Hello"
        assert content_to_json(None) is None

    def test_blocks_encode_as_typed_dicts(self) -> None:
        encoded = content_to_json(
            [
                TextBlock(text="Look:"),
                ImageBlock(url="https://example.com/cat.png"),
                DocumentBlock(media_type="application/pdf", data="JVBERi0="),
            ]
        )
        assert isinstance(encoded, list)
        assert encoded[0] == {"type": "text", "text": "Look:"}
        assert encoded[1]["type"] == "image"
        assert encoded[1]["url"] == "https://example.com/cat.png"
        assert encoded[2]["type"] == "document"
        assert encoded[2]["data"] == "JVBERi0="

    def test_blocks_survive_json_dumps(self) -> None:
        """The encoding must be json.dumps-safe (raw dataclasses are not)."""
        encoded = content_to_json(
            [
                TextBlock(text="hi"),
                ImageBlock(media_type="image/png", data="aGVsbG8="),
            ]
        )
        json.dumps(encoded)  # Must not raise


@pytest.mark.unit
class TestRequiredContentTypes:
    """Test required_content_types helper."""

    def test_all_text(self) -> None:
        messages = [
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.USER, content=[TextBlock(text="Hi")]),
        ]
        assert required_content_types(messages) == (False, False)

    def test_images_and_documents(self) -> None:
        messages = [
            Message(
                role=Role.USER,
                content=[
                    ImageBlock(media_type="image/png", data="aGVsbG8="),
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                ],
            ),
        ]
        assert required_content_types(messages) == (True, True)

    def test_document_only(self) -> None:
        messages = [
            Message(role=Role.USER, content="Earlier turn"),
            Message(
                role=Role.USER,
                content=[DocumentBlock(media_type="application/pdf", data="JVBERi0=")],
            ),
        ]
        assert required_content_types(messages) == (False, True)
