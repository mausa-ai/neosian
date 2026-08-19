"""Message ⇄ JSON codec for persistence (N0 slice B).

The inverse of `content_to_json` plus a full-fidelity Message round-trip:
role, content (plain string or content blocks), reasoning, tool_calls,
tool_call_id. Library-side so the CLI (and later the N1 FileStore turn
log) share one codec instead of hand-rolling halves of it.

Not exported from the package root yet — the public-surface decision
belongs to N1/N2 when the storage layer lands.
"""

from __future__ import annotations

from typing import Any

from neosian._foundation.llm.base import (
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolCall,
    content_to_json,
)
from neosian._foundation.shared.types import ToolCallId, ToolName


def content_from_json(
    data: str | list[dict[str, Any]] | None,
) -> str | list[ContentBlock] | None:
    """Inverse of `content_to_json`.

    Raises:
        ValueError: On an unknown block type. Block invariants (data xor
            url, media_type with base64) re-validate in `__post_init__`.
    """
    if data is None or isinstance(data, str):
        return data
    blocks: list[ContentBlock] = []
    for encoded in data:
        block_type = encoded.get("type")
        if block_type == "text":
            blocks.append(TextBlock(text=encoded["text"]))
        elif block_type == "image":
            blocks.append(
                ImageBlock(
                    media_type=encoded.get("media_type"),
                    data=encoded.get("data"),
                    url=encoded.get("url"),
                )
            )
        elif block_type == "document":
            blocks.append(
                DocumentBlock(
                    media_type=encoded.get("media_type"),
                    data=encoded.get("data"),
                    url=encoded.get("url"),
                )
            )
        else:
            raise ValueError(f"Unknown content block type: {block_type!r}")
    return blocks


def message_to_json(message: Message) -> dict[str, Any]:
    """Full-fidelity JSON-safe encoding of one message."""
    return {
        "role": message.role.value,
        "content": content_to_json(message.content),
        "reasoning": message.reasoning,
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
    }


def message_from_json(data: dict[str, Any]) -> Message:
    """Inverse of `message_to_json`; absent optional keys decode to defaults."""
    tool_call_id = data.get("tool_call_id")
    return Message(
        role=Role(data["role"]),
        content=content_from_json(data.get("content")),
        reasoning=data.get("reasoning"),
        tool_calls=[
            ToolCall(
                id=ToolCallId(tc["id"]),
                name=ToolName(tc["name"]),
                arguments=tc.get("arguments") or {},
            )
            for tc in data.get("tool_calls") or []
        ],
        tool_call_id=ToolCallId(tool_call_id) if tool_call_id else None,
    )
