"""Capability gating for fallback attempts (DESIGN §2)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from neosian._foundation.llm.base import Message, required_content_types
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    ModelFailedError,
    UnsupportedContentError,
)
from neosian._foundation.shared.types import Model

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.context import Attempt

logger = logging.getLogger(__name__)


def unsupported_content_types(model: Model, messages: list[Message]) -> list[str]:
    """Content block types in messages that the model cannot handle.

    Returns:
        Subset of ["image", "document"]; empty when the model supports
        everything the conversation carries (including all-text).
    """
    needs_images, needs_documents = required_content_types(messages)
    missing: list[str] = []
    if needs_images and not model.supports_images:
        missing.append("image")
    if needs_documents and not model.supports_documents:
        missing.append("document")
    return missing


def ensure_fallback_viable(
    agent: Agent,
    error: Exception,
    messages: list[Message],
    attempt: Attempt,
) -> None:
    """Gate a fallback attempt on the fallback model's content capabilities.

    Called inside a main-model except block once a fallback is
    configured. Returns normally when the fallback model can handle the
    conversation's content. Otherwise logs the skip and raises — the
    original UnsupportedContentError as-is, anything else wrapped in
    ModelFailedError carrying the failed attempt's billed usage — so
    media is never downgraded onto a model that can't handle it.
    """
    assert agent._fallback is not None  # Callers check before invoking
    missing = unsupported_content_types(agent._fallback.model, messages)
    if not missing:
        return
    logger.warning(
        ErrorMessages.FALLBACK_SKIPPED_UNSUPPORTED_CONTENT.format(
            model=agent._fallback.model.value,
            block_type="/".join(missing),
        )
    )
    if isinstance(error, UnsupportedContentError):
        raise error
    raise ModelFailedError(
        model=agent._model.value,
        error=str(error),
        has_fallback=False,
        usage=attempt.usage,
        usage_by_model=attempt.usage_by_model,
    ) from error
