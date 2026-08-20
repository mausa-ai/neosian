"""Message content blocks and their helpers.

Extracted from llm/base.py at N4 slice B so the base module stays under
the size gate; base re-exports everything here, so existing imports keep
working. Functions taking a Message annotate it type-only — the runtime
dependency direction stays blocks ← base.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neosian._foundation.llm.base import Message


@dataclass
class TextBlock:
    """A text segment in multimodal message content."""

    text: str


@dataclass
class ImageBlock:
    """An image input for vision-capable models.

    Exactly one of `data` (base64) or `url` must be set. `media_type`
    (e.g. "image/png", "image/jpeg", "image/webp", "image/gif") is required
    with base64 data and unused for url sources.

    Anthropic recommends placing media blocks before text blocks in a
    message for best results; neosian preserves caller order.
    """

    media_type: str | None = None
    data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        _validate_media_source(
            type(self).__name__, self.media_type, self.data, self.url
        )
        if self.data is not None:
            # Anthropic rejects base64 containing newlines/whitespace.
            self.data = "".join(self.data.split())


@dataclass
class DocumentBlock:
    """A document input (e.g. PDF) for document-capable models.

    Exactly one of `data` (base64) or `url` must be set. `media_type`
    (e.g. "application/pdf") is required with base64 data and unused for
    url sources.

    Note: base64 inflates bytes by ~33% against Anthropic's 32 MB request
    cap (roughly a 24 MB raw-PDF ceiling; 100-page limit on 200K-context
    models).
    """

    media_type: str | None = None
    data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        _validate_media_source(
            type(self).__name__, self.media_type, self.data, self.url
        )
        if self.data is not None:
            self.data = "".join(self.data.split())


def _validate_media_source(
    block_name: str, media_type: str | None, data: str | None, url: str | None
) -> None:
    """Validate the data/url/media_type invariants shared by media blocks."""
    if (data is None) == (url is None):
        raise ValueError(f"{block_name} requires exactly one of 'data' or 'url'")
    if data is not None and media_type is None:
        raise ValueError(f"{block_name} requires media_type with base64 data")


ContentBlock = TextBlock | ImageBlock | DocumentBlock


def text_of(message: Message) -> str:
    """Text content of a message, regardless of content shape.

    Plain-str content is returned as-is; block-list content returns the
    TextBlock texts joined with newlines (media blocks contribute nothing);
    None returns "".
    """
    if message.content is None:
        return ""
    if isinstance(message.content, str):
        return message.content
    return "\n".join(
        block.text for block in message.content if isinstance(block, TextBlock)
    )


def content_to_json(
    content: str | list[ContentBlock] | None,
) -> str | list[dict[str, object]] | None:
    """JSON-safe encoding of message content for persistence.

    Plain strings and None pass through; block lists encode as typed dicts
    ({"type": "text" | "image" | "document", ...}) that survive json.dumps.
    """
    if content is None or isinstance(content, str):
        return content
    encoded: list[dict[str, object]] = []
    for block in content:
        if isinstance(block, TextBlock):
            encoded.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            encoded.append(
                {
                    "type": "image",
                    "media_type": block.media_type,
                    "data": block.data,
                    "url": block.url,
                }
            )
        else:
            encoded.append(
                {
                    "type": "document",
                    "media_type": block.media_type,
                    "data": block.data,
                    "url": block.url,
                }
            )
    return encoded


def required_content_types(messages: list[Message]) -> tuple[bool, bool]:
    """Content capabilities required by a conversation.

    Returns:
        (needs_images, needs_documents) — True when any message carries an
        ImageBlock / DocumentBlock. Used for model capability gating and
        capability-aware fallback.
    """
    needs_images = False
    needs_documents = False
    for message in messages:
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ImageBlock):
                    needs_images = True
                elif isinstance(block, DocumentBlock):
                    needs_documents = True
    return needs_images, needs_documents
