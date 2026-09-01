"""Capability gating for fallback attempts (DESIGN §2)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from neosian._foundation.llm.base import (
    Message,
    required_content_types,
    requires_compaction_support,
)
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    ModelFailedError,
    UnsupportedContentError,
)
from neosian._foundation.shared.types import AnyModel

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.context import Attempt

logger = logging.getLogger(__name__)


def unsupported_content_types(model: AnyModel, messages: list[Message]) -> list[str]:
    """Content block types in messages that the model cannot handle.

    Returns:
        Subset of ["image", "document", "compaction"]; empty when the
        model supports everything the conversation carries (including
        all-text). A compaction-bearing history cannot move to a model
        outside Anthropic's compact support set — every other converter
        rejects block content, so the gate turns a guaranteed 400 into a
        logged skip.
    """
    needs_images, needs_documents = required_content_types(messages)
    missing: list[str] = []
    if needs_images and not model.supports_images:
        missing.append("image")
    if needs_documents and not model.supports_documents:
        missing.append("document")
    if requires_compaction_support(messages) and not model.supports_compaction_blocks:
        missing.append("compaction")
    return missing


def reraise_caller_errors(error: Exception, attempt: Attempt) -> None:
    """Re-raise caller-input errors as-is instead of wrapping them.

    Unsupported content and context overflow are prompt problems, not
    model failures — ModelFailedError would bury the structure hosts key
    on. Billed usage is attached so the error path keeps the ledger
    (DESIGN §3 register #4). Returns normally for every other error.
    """
    if not isinstance(error, (UnsupportedContentError, ContextWindowExceededError)):
        return
    if error.usage is None:
        error.usage = attempt.usage
        error.usage_by_model = attempt.usage_by_model
    raise error


def ensure_fallback_viable(
    agent: Agent,
    error: Exception,
    messages: list[Message],
    attempt: Attempt,
) -> None:
    """Gate a fallback attempt on the fallback model's capabilities.

    Called inside a main-model except block once a fallback is
    configured. Returns normally when the fallback model can handle the
    conversation. Otherwise logs the skip and raises — the original
    UnsupportedContentError/ContextWindowExceededError as-is, anything
    else wrapped in ModelFailedError carrying the failed attempt's
    billed usage. Two gates:

    - Content: media is never downgraded onto a model that can't
      handle it (DESIGN §2).
    - Window: a context overflow falls back only onto a strictly
      larger window — falling back smaller is a guaranteed second
      failure and a doubled bill (DESIGN §5).
    """
    assert agent._fallback is not None  # Callers check before invoking
    if isinstance(error, ContextWindowExceededError):
        overflowed = error.context_window or agent._model.context_window
        if agent._fallback.model.context_window <= overflowed:
            logger.warning(
                "Fallback to %s skipped: its context window (%d) is not "
                "larger than the overflowed one (%d)",
                agent._fallback.model.value,
                agent._fallback.model.context_window,
                overflowed,
            )
            if error.usage is None:  # keep billed usage on the error path
                error.usage = attempt.usage
                error.usage_by_model = attempt.usage_by_model
            raise error
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
