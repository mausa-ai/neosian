"""Message ⇄ JSON codec round-trips (N0 slice B)."""

import json

import pytest

from neosian._foundation.llm.base import (
    DocumentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolCall,
)
from neosian._foundation.llm.codec import (
    content_from_json,
    message_from_json,
    message_to_json,
)
from neosian._foundation.shared.types import ToolCallId, ToolName


def _round_trip(message: Message) -> Message:
    encoded = json.loads(json.dumps(message_to_json(message)))  # via real JSON
    return message_from_json(encoded)


@pytest.mark.unit
class TestMessageRoundTrip:
    def test_plain_user_message(self) -> None:
        message = Message(role=Role.USER, content="Hello")
        assert _round_trip(message) == message

    def test_assistant_with_reasoning_and_tool_calls(self) -> None:
        message = Message(
            role=Role.ASSISTANT,
            content=None,
            reasoning="thinking...",
            tool_calls=[
                ToolCall(
                    id=ToolCallId("call_1"),
                    name=ToolName("lookup"),
                    arguments={"q": "x", "n": 2},
                )
            ],
        )
        assert _round_trip(message) == message

    def test_tool_message_keeps_tool_call_id(self) -> None:
        message = Message(
            role=Role.TOOL,
            content='{"success": true}',
            tool_call_id=ToolCallId("call_1"),
        )
        assert _round_trip(message) == message

    def test_block_content_round_trips(self) -> None:
        message = Message(
            role=Role.USER,
            content=[
                TextBlock(text="Transcribe this."),
                ImageBlock(media_type="image/png", data="aWJt"),
                DocumentBlock(url="https://example.com/x.pdf"),
            ],
        )
        assert _round_trip(message) == message

    def test_pre_slice_b_payload_without_reasoning_decodes(self) -> None:
        decoded = message_from_json(
            {"role": "assistant", "content": "hi", "tool_calls": []}
        )
        assert decoded == Message(role=Role.ASSISTANT, content="hi")


@pytest.mark.unit
class TestContentFromJson:
    def test_str_and_none_pass_through(self) -> None:
        assert content_from_json("plain") == "plain"
        assert content_from_json(None) is None

    def test_unknown_block_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown content block type"):
            content_from_json([{"type": "audio"}])
