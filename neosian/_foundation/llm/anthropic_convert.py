"""Anthropic message wire-format converters.

Extracted from `anthropic.py` for size-gate headroom (NC7, REVIEW
LL-31); the tool and schema half left for `anthropic_tools.py` at NC9.
Every function is self-free — the wire format is a function of the
message, never of the client.
"""

from typing import Any

from neosian._foundation.llm.base import (
    CompactionBlock,
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    required_content_types,
    requires_compaction_support,
)
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import AnyModel
from neosian._foundation.tools.result import FAILED_ENVELOPE_PREFIX


def _is_tool_results(message: dict[str, Any]) -> bool:
    content = message["content"]
    return (
        message["role"] == "user"
        and isinstance(content, list)
        and bool(content)
        and content[-1].get("type") == "tool_result"
    )


def _echoed_thinking(message: Message) -> list[dict[str, Any]]:
    """The thinking blocks a prior turn stored, or nothing."""
    channel = (message.extra or {}).get("anthropic")
    blocks = channel.get("thinking_blocks") if isinstance(channel, dict) else None
    return list(blocks) if isinstance(blocks, list) else []


def convert_messages(
    messages: list[Message],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert internal messages to Anthropic format.

    Anthropic takes the system prompt separately: every SYSTEM message
    joins it in order, none overwrites another (LL-3). Consecutive tool
    results coalesce into the one user message the API expects after a
    tool-use turn (LL-2).

    Args:
        messages: Internal Message objects.

    Returns:
        Tuple of (system_prompt, messages_list).
    """
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            if isinstance(msg.content, list):
                raise UnsupportedContentError(
                    ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                        provider="anthropic (system role)",
                        block_type="system-message",
                    )
                )
            if msg.content:
                system_parts.append(msg.content)
        elif msg.role == Role.USER:
            if isinstance(msg.content, list):
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": convert_content_blocks(msg.content),
                    }
                )
            else:
                anthropic_messages.append(
                    {"role": "user", "content": msg.content or ""}
                )
        elif msg.role == Role.ASSISTANT:
            # A prior turn's thinking blocks lead the content — the
            # API requires them verbatim, signatures included (LL-1).
            echoed = _echoed_thinking(msg)
            if isinstance(msg.content, list):
                # Text + compaction blocks (server-compaction echo),
                # in provider order, with tool_use appended after —
                # media on the assistant role still raises.
                content = echoed + convert_assistant_blocks(msg.content)
                for tc in msg.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": content})
            elif msg.tool_calls or echoed:
                content = echoed
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": content})
            else:
                anthropic_messages.append(
                    {"role": "assistant", "content": msg.content or ""}
                )
        elif msg.role == Role.TOOL:
            if isinstance(msg.content, list):
                raise UnsupportedContentError(
                    ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                        provider="anthropic (tool role)",
                        block_type="tool-result",
                    )
                )
            # Tool results are user-role tool_result blocks; a batch
            # shares one message. A failed envelope is flagged in the
            # provider's own vocabulary (LL-10).
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": msg.content or "",
            }
            if (msg.content or "").startswith(FAILED_ENVELOPE_PREFIX):
                result["is_error"] = True
            if anthropic_messages and _is_tool_results(anthropic_messages[-1]):
                anthropic_messages[-1]["content"].append(result)
            else:
                anthropic_messages.append({"role": "user", "content": [result]})

    return "\n\n".join(system_parts) or None, anthropic_messages


def convert_content_blocks(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    """Convert internal content blocks to Anthropic content-block dicts.

    Caller block order is preserved. Anthropic recommends placing media
    blocks before text blocks for best results — callers control this.
    """
    result: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, (ImageBlock, DocumentBlock)):
            block_type = "image" if isinstance(block, ImageBlock) else "document"
            source: dict[str, Any]
            if block.data is not None:
                source = {
                    "type": "base64",
                    "media_type": block.media_type,
                    "data": block.data,
                }
            else:
                source = {"type": "url", "url": block.url}
            result.append({"type": block_type, "source": source})
        else:
            # A CompactionBlock belongs to assistant content only —
            # never silently dropped (the media-block rule).
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                    provider="anthropic (user role)",
                    block_type="compaction",
                )
            )
    return result


def convert_assistant_blocks(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    """Convert assistant content blocks — text and compaction only.

    Compaction blocks are echoed verbatim in provider order; the API
    replaces everything before the block with it. Media blocks on the
    assistant role raise, as they always have.
    """
    result: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, CompactionBlock):
            entry: dict[str, Any] = {
                "type": "compaction",
                "content": block.content,
            }
            if block.encrypted_content is not None:
                entry["encrypted_content"] = block.encrypted_content
            result.append(entry)
        else:
            raise UnsupportedContentError(
                ErrorMessages.CONTENT_BLOCKS_NOT_SUPPORTED.format(
                    provider="anthropic (assistant role)",
                    block_type="assistant-message",
                )
            )
    return result


def validate_content_support(
    messages: list[Message],
    model: AnyModel,
    *,
    server_compaction: bool = False,
) -> None:
    """Raise if messages carry content blocks the model cannot handle.

    All currently registered Claude models support both images and
    documents; this gate future-proofs against text-only entries. The
    compaction gates are live today: Haiku 4.5 is Anthropic and
    outside the compact-2026-01-12 support set.

    Raises:
        UnsupportedContentError: If a required capability is missing.
        UnsupportedParameterError: If server_compaction is requested
            on a model outside the beta's support set.
    """
    needs_images, needs_documents = required_content_types(messages)
    if needs_images and not model.supports_images:
        raise UnsupportedContentError(
            ErrorMessages.CONTENT_TYPE_NOT_SUPPORTED_BY_MODEL.format(
                model=model.value, block_type="image"
            )
        )
    if needs_documents and not model.supports_documents:
        raise UnsupportedContentError(
            ErrorMessages.CONTENT_TYPE_NOT_SUPPORTED_BY_MODEL.format(
                model=model.value, block_type="document"
            )
        )
    if server_compaction and not model.supports_compaction_blocks:
        raise UnsupportedParameterError(
            f"server_compaction is not supported by {model.value}"
        )
    if requires_compaction_support(messages) and not model.supports_compaction_blocks:
        raise UnsupportedContentError(
            ErrorMessages.CONTENT_TYPE_NOT_SUPPORTED_BY_MODEL.format(
                model=model.value, block_type="compaction"
            )
        )


def apply_cache_control(
    system_prompt: str | None,
    anthropic_messages: list[dict[str, Any]],
    cache_last_message: bool = True,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Apply cache_control breakpoints for Anthropic prompt caching.

    Adds ephemeral cache breakpoints to the system prompt and the last
    message in the conversation. This enables Anthropic to cache the
    prefix (tools + system + conversation history) across API calls,
    reducing input token costs by up to 90% on cache reads.

    Cache order: tools (handled by _convert_tools) → system → messages.

    The last-message breakpoint lands on the final content block
    whatever its type — cache_control is valid on image/document blocks,
    and media tokens are exactly the expensive prefix worth caching in
    multi-turn conversations. For one-shot calls whose conversation is
    never re-sent, pass cache_last_message=False to skip the breakpoint
    (avoids paying the 1.25x cache-write premium for nothing); the
    system-prompt breakpoint is unaffected.

    Args:
        system_prompt: The system prompt string (or None).
        anthropic_messages: Converted Anthropic-format messages.
        cache_last_message: Whether to place the last-message breakpoint.

    Returns:
        Tuple of (cached_system, cached_messages).
        cached_system is a structured list with cache_control, or None.
        cached_messages has cache_control on the last message's content.
    """
    # System: plain string → structured list with cache_control
    cached_system: list[dict[str, Any]] | None = None
    if system_prompt:
        cached_system = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    # Last message: add cache_control to the last content block.
    # This caches the entire prefix (tools + system + all messages up
    # to this point) so subsequent calls only process new messages.
    if cache_last_message and anthropic_messages:
        last_msg = anthropic_messages[-1]
        content = last_msg["content"]
        if isinstance(content, str):
            # Convert string content to structured format with cache_control
            last_msg["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list) and content:
            # Add cache_control to the last block in the list
            content[-1]["cache_control"] = {"type": "ephemeral"}

    return cached_system, anthropic_messages
