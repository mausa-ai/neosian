"""Message ⇄ JSON codec for persistence (N0 slice B).

The inverse of `content_to_json` plus a full-fidelity Message round-trip:
role, content (plain string or content blocks), reasoning, tool_calls,
tool_call_id. One codec shared by the CLI, the FileStore turn log, and any
host-implemented `ConversationStore` — `message_to_json`/`message_from_json`
are public API since N2 (DESIGN §9.9, ledger #22): a store must encode
messages with the same codec the library reads back (CS5).
"""

from __future__ import annotations

from typing import Any

from neosian._foundation.llm.base import (
    CompactionBlock,
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
        elif block_type == "compaction":
            blocks.append(
                CompactionBlock(
                    content=encoded.get("content"),
                    encrypted_content=encoded.get("encrypted_content"),
                )
            )
        else:
            raise ValueError(f"Unknown content block type: {block_type!r}")
    return blocks


def _tool_call_to_json(tc: ToolCall) -> dict[str, Any]:
    """`extra` appears only when the provider set it — older lines and
    every other wire's calls encode exactly as before."""
    encoded: dict[str, Any] = {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
    if tc.extra:
        encoded["extra"] = tc.extra
    return encoded


def message_to_json(message: Message) -> dict[str, Any]:
    """Full-fidelity JSON-safe encoding of one message; `extra` appears
    only when a provider set it, so older lines encode exactly as before."""
    encoded: dict[str, Any] = {
        "role": message.role.value,
        "content": content_to_json(message.content),
        "reasoning": message.reasoning,
        "tool_calls": [_tool_call_to_json(tc) for tc in message.tool_calls],
        "tool_call_id": message.tool_call_id,
    }
    if message.extra:
        encoded["extra"] = message.extra
    return encoded


def _tool_call_from_json(tc: dict[str, Any]) -> ToolCall:
    """`id` and `name` are strings and `arguments` an object or absent:
    the JSON text OpenAI's wire carries is parsed before it gets here,
    and a call that slipped through as a string would be stored as one.

    Raises:
        ValueError: On a mis-typed field, naming it.
    """
    arguments = tc.get("arguments")
    if not isinstance(tc["id"], str):
        raise ValueError("tool call parameter 'id' must be a string")
    if not isinstance(tc["name"], str):
        raise ValueError("tool call parameter 'name' must be a string")
    if arguments is not None and not isinstance(arguments, dict):
        raise ValueError("tool call parameter 'arguments' must be an object")
    return ToolCall(
        id=ToolCallId(tc["id"]),
        name=ToolName(tc["name"]),
        arguments=arguments or {},
        extra=tc.get("extra"),
    )


def message_from_json(data: dict[str, Any]) -> Message:
    """Inverse of `message_to_json`; absent optional keys decode to defaults.

    Raises:
        ValueError: On an unknown content block type or a mis-typed tool
            call field (`id`, `name`, `arguments`).
    """
    tool_call_id = data.get("tool_call_id")
    return Message(
        role=Role(data["role"]),
        content=content_from_json(data.get("content")),
        reasoning=data.get("reasoning"),
        tool_calls=[_tool_call_from_json(tc) for tc in data.get("tool_calls") or []],
        tool_call_id=ToolCallId(tool_call_id) if tool_call_id else None,
        extra=data.get("extra"),
    )
