"""Streaming orchestration: guard task, model fallback, sticky
fallback (DESIGN §3)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from neosian._foundation.agent.context import Attempt
from neosian._foundation.agent.emit import emit_fallback
from neosian._foundation.agent.fallback import (
    ensure_fallback_viable,
    unsupported_content_types,
)
from neosian._foundation.agent.guards import check_guardrails, extract_user_content
from neosian._foundation.agent.stream_loop import stream_with_client
from neosian._foundation.agent.streaming import SSEEventEmitter
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    FallbackExhaustedError,
    ModelFailedError,
    UnsupportedContentError,
)
from neosian._foundation.shared.types import GuardrailMode, PolicyResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import RunContext

logger = logging.getLogger(__name__)


async def run_streaming(ctx: RunContext, messages: list[Message]) -> AsyncIterator[str]:
    """Execute agent with streaming (SSE mode).

    Input guardrails run in parallel with streaming. If guard flags and
    block_on_input is True, emits BLOCKED event to interrupt the stream.
    Safe users experience no guardrail overhead.

    Args:
        messages: Conversation history (without system message).

    Yields:
        SSE-formatted strings for tool calls, tool results, content, blocked, and done.
    """
    agent = ctx.agent
    # Determine if we need to run input guardrails
    has_input_guard = (
        agent._guardrails is not None
        and agent._guardrails.input_mode != GuardrailMode.NONE
    )
    user_content = extract_user_content(messages) if has_input_guard else ""

    # Start guard task in background if needed
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]] | None = None
    if has_input_guard and user_content:
        guard_task = asyncio.create_task(check_guardrails(agent, user_content, "input"))

    # Stream the agent response, checking guard status periodically
    async for sse in stream_agent_with_guard(ctx, messages, guard_task):
        yield sse


async def stream_agent_with_guard(
    ctx: RunContext,
    messages: list[Message],
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]] | None,
) -> AsyncIterator[str]:
    """Stream agent response while monitoring guard task.

    Args:
        ctx: Per-run context (client acquisition, sticky fallback state).
        messages: Conversation history (without system message).
        guard_task: Background guard task to monitor (or None if no guard).

    Yields:
        SSE-formatted strings with sequence and created_at metadata.

    Raises:
        ModelFailedError: If model fails and no fallback is configured.
        FallbackExhaustedError: If both main and fallback models fail.
    """
    agent = ctx.agent
    # Prepend system message
    base_messages = [
        Message(role=Role.SYSTEM, content=agent._system_prompt),
        *messages,
    ]

    # Create emitter once for the entire streaming session
    emitter = SSEEventEmitter()

    # Determine which model to try first - check if we should retry main
    fallback_state = ctx.fallback_state
    if (
        fallback_state is not None
        and fallback_state.using_fallback
        and agent._should_retry_main(fallback_state)
    ):
        logger.info(
            ErrorMessages.FALLBACK_RETRY_MAIN.format(
                main_model=agent._model.value,
                successful_calls=fallback_state.successful_fallback_calls,
            )
        )
        fallback_state.using_fallback = False
        fallback_state.successful_fallback_calls = 0
        assert agent._fallback is not None  # _should_retry_main checked
        await emit_fallback(
            ctx,
            from_model=agent._fallback.model.value,
            to_model=agent._model.value,
            reason="retry_main_after threshold reached",
            cause=None,
            sticky=True,
            streamed=True,
        )

    # If using fallback (sticky), try fallback first
    if fallback_state is not None and fallback_state.using_fallback:
        async for sse in stream_with_fallback_model(
            ctx, base_messages, guard_task, emitter
        ):
            yield sse
        return

    # Try main model
    attempt = Attempt.start(agent._model, base_messages)
    try:
        client = ctx.acquire(agent._model.provider)
        async for sse in stream_with_client(
            ctx,
            client=client,
            model=agent._model,
            attempt=attempt,
            guard_task=guard_task,
            emitter=emitter,
        ):
            yield sse
        # Success on main - reset fallback state if present
        if fallback_state is not None:
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
        return
    except Exception as e:
        main_error = str(e)
        # No fallback configured - raise immediately
        if agent._fallback is None:
            if isinstance(e, UnsupportedContentError):
                raise
            raise ModelFailedError(
                model=agent._model.value,
                error=main_error,
                has_fallback=False,
                usage=attempt.usage,
                usage_by_model=attempt.usage_by_model,
            ) from e

        # Capability-aware: never downgrade media onto a fallback model
        # that can't handle it.
        ensure_fallback_viable(agent, e, base_messages, attempt=attempt)

        # Try fallback
        logger.warning(
            ErrorMessages.FALLBACK_TRIGGERED.format(
                from_model=agent._model.value,
                to_model=agent._fallback.model.value,
                reason=main_error,
            )
        )
        await emit_fallback(
            ctx,
            from_model=agent._model.value,
            to_model=agent._fallback.model.value,
            reason=main_error,
            cause=e,
            sticky=False,
            streamed=True,
        )
        # Fresh message snapshot (a failed attempt's partial tool rounds
        # must not leak into the fallback's history); billed usage carries.
        fallback_attempt = Attempt.start(
            agent._fallback.model, base_messages, prior=attempt
        )
        try:
            fallback_client = ctx.acquire(agent._fallback.model.provider)
            async for sse in stream_with_client(
                ctx,
                client=fallback_client,
                model=agent._fallback.model,
                attempt=fallback_attempt,
                guard_task=guard_task,
                emitter=emitter,
            ):
                yield sse
            # Success on fallback - update state
            if fallback_state is not None:
                fallback_state.using_fallback = True
                fallback_state.successful_fallback_calls = 1
            return
        except Exception as fallback_e:
            raise FallbackExhaustedError(
                main_model=agent._model.value,
                main_error=main_error,
                fallback_model=agent._fallback.model.value,
                fallback_error=str(fallback_e),
                usage=fallback_attempt.usage,
                usage_by_model=fallback_attempt.usage_by_model,
            ) from fallback_e


async def stream_with_fallback_model(
    ctx: RunContext,
    base_messages: list[Message],
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]] | None,
    emitter: SSEEventEmitter,
) -> AsyncIterator[str]:
    """Stream with fallback model (sticky mode).

    Args:
        ctx: Per-run context; its fallback_state is non-None here —
            callers dispatch to this method only when sticky.
        base_messages: Full conversation with system message prepended.
        guard_task: Background guard task to monitor (or None).
        emitter: SSE event emitter for metadata.

    Yields:
        SSE-formatted strings.

    Raises:
        FallbackExhaustedError: If both models fail.
    """
    agent = ctx.agent
    fallback_state = ctx.fallback_state
    assert fallback_state is not None  # Callers check using_fallback first
    if agent._fallback is None:
        raise ModelFailedError(
            model=agent._model.value,
            error="No fallback configured but fallback_state.using_fallback=True",
            has_fallback=False,
        )

    # Capability-aware: media the sticky fallback model can't handle
    # routes straight to the main model.
    if unsupported_content_types(agent._fallback.model, base_messages):
        attempt = Attempt.start(agent._model, base_messages)
        try:
            main_client = ctx.acquire(agent._model.provider)
            async for sse in stream_with_client(
                ctx,
                client=main_client,
                model=agent._model,
                attempt=attempt,
                guard_task=guard_task,
                emitter=emitter,
            ):
                yield sse
            # Main handled it - reset sticky state
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
            return
        except Exception as e:
            if isinstance(e, UnsupportedContentError):
                raise
            raise ModelFailedError(
                model=agent._model.value,
                error=str(e),
                has_fallback=False,
                usage=attempt.usage,
                usage_by_model=attempt.usage_by_model,
            ) from e

    attempt = Attempt.start(agent._fallback.model, base_messages)
    try:
        client = ctx.acquire(agent._fallback.model.provider)
        async for sse in stream_with_client(
            ctx,
            client=client,
            model=agent._fallback.model,
            attempt=attempt,
            guard_task=guard_task,
            emitter=emitter,
        ):
            yield sse
        # Success - increment counter
        fallback_state.successful_fallback_calls += 1
        return
    except Exception as e:
        # Fallback failed - try main as last resort
        logger.warning(
            ErrorMessages.FALLBACK_TRIGGERED.format(
                from_model=agent._fallback.model.value,
                to_model=agent._model.value,
                reason=str(e),
            )
        )
        await emit_fallback(
            ctx,
            from_model=agent._fallback.model.value,
            to_model=agent._model.value,
            reason=str(e),
            cause=e,
            sticky=False,
            streamed=True,
        )
        fallback_error = str(e)
        main_attempt = Attempt.start(agent._model, base_messages, prior=attempt)
        try:
            main_client = ctx.acquire(agent._model.provider)
            async for sse in stream_with_client(
                ctx,
                client=main_client,
                model=agent._model,
                attempt=main_attempt,
                guard_task=guard_task,
                emitter=emitter,
            ):
                yield sse
            # Main recovered - reset state
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
            return
        except Exception as main_e:
            raise FallbackExhaustedError(
                main_model=agent._model.value,
                main_error=str(main_e),
                fallback_model=agent._fallback.model.value,
                fallback_error=fallback_error,
                usage=main_attempt.usage,
                usage_by_model=main_attempt.usage_by_model,
            ) from main_e
