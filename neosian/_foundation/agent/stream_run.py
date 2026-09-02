"""Streaming orchestration: guard task, model fallback, sticky
fallback (DESIGN §3)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from neosian._foundation.agent.context import Attempt
from neosian._foundation.agent.emit import emit_fallback
from neosian._foundation.agent.events import AgentEvent, EventSequencer, ReadyEvent
from neosian._foundation.agent.fallback import (
    ensure_fallback_viable,
    reraise_caller_errors,
    unsupported_content_types,
)
from neosian._foundation.agent.lifetimes import GuardWatch, closing
from neosian._foundation.agent.stream_loop import stream_with_client
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    FallbackExhaustedError,
    ModelFailedError,
)
from neosian._foundation.shared.registry import provider_label

if TYPE_CHECKING:
    from neosian._foundation.agent.context import RunContext

logger = logging.getLogger(__name__)


async def run_streaming(
    ctx: RunContext, messages: list[Message]
) -> AsyncIterator[AgentEvent]:
    """Execute agent with streaming: typed events (DESIGN §6).

    Input guardrails run in parallel with streaming. If guard flags and
    block_on_input is True, a BlockedEvent terminates the stream. Safe
    users experience no guardrail overhead.

    The one EventSequencer lives here: inner generators yield unstamped
    events, this wrapper stamps every event exactly once — sequence
    continuity across fallback attempts is automatic.

    Args:
        messages: Conversation history (without system message).

    Yields:
        AgentEvent values, ReadyEvent first, sequence starting at 1.
    """
    agent = ctx.agent
    sequencer = EventSequencer()
    # The watch owns the guard task for the whole run: a consumer that
    # stops iterating, an error, a cancellation — each leaves the `async
    # with`, which reaps whatever is still pending (AG-3).
    async with GuardWatch.start(ctx, messages) as guard:
        yield sequencer.stamp(
            ReadyEvent(
                requested_model=agent._model.value,
                provider=provider_label(agent._model),
            )
        )
        # Every nested stream is closed with its consumer (AG-14): a
        # consumer's aclose() reaches the provider stream and the tool
        # batch synchronously, never on the garbage collector's schedule.
        async with closing(stream_agent_with_guard(ctx, messages, guard)) as events:
            async for event in events:
                yield sequencer.stamp(event)


async def stream_agent_with_guard(
    ctx: RunContext,
    messages: list[Message],
    guard: GuardWatch,
) -> AsyncIterator[AgentEvent]:
    """Stream agent response while monitoring guard task.

    Args:
        ctx: Per-run context (client acquisition, sticky fallback state).
        messages: Conversation history (without system message).
        guard: The run's guard watch.

    Yields:
        Unstamped AgentEvent values — run_streaming assigns sequence.

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
        async with closing(
            stream_with_fallback_model(ctx, base_messages, guard)
        ) as events:
            async for event in events:
                yield event
        return

    # Try main model
    attempt = Attempt.start(agent._model, base_messages, ctx.ledger)
    try:
        client = ctx.acquire(agent._model)
        async with closing(
            stream_with_client(
                ctx,
                client=client,
                model=agent._model,
                attempt=attempt,
                guard=guard,
            )
        ) as events:
            async for event in events:
                yield event
        # Success on main - reset fallback state if present
        if fallback_state is not None:
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
        return
    except Exception as e:
        main_error = str(e)
        # No fallback configured - raise immediately
        if agent._fallback is None:
            reraise_caller_errors(e, attempt)
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
            agent._fallback.model, base_messages, ctx.ledger
        )
        try:
            fallback_client = ctx.acquire(agent._fallback.model)
            async with closing(
                stream_with_client(
                    ctx,
                    client=fallback_client,
                    model=agent._fallback.model,
                    attempt=fallback_attempt,
                    guard=guard,
                )
            ) as events:
                async for event in events:
                    yield event
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
    guard: GuardWatch,
) -> AsyncIterator[AgentEvent]:
    """Stream with fallback model (sticky mode).

    Args:
        ctx: Per-run context; its fallback_state is non-None here —
            callers dispatch to this method only when sticky.
        base_messages: Full conversation with system message prepended.
        guard: The run's guard watch.

    Yields:
        Unstamped AgentEvent values — run_streaming assigns sequence.

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
        attempt = Attempt.start(agent._model, base_messages, ctx.ledger)
        try:
            main_client = ctx.acquire(agent._model)
            async with closing(
                stream_with_client(
                    ctx,
                    client=main_client,
                    model=agent._model,
                    attempt=attempt,
                    guard=guard,
                )
            ) as events:
                async for event in events:
                    yield event
            # Main handled it - reset sticky state
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
            return
        except Exception as e:
            reraise_caller_errors(e, attempt)
            raise ModelFailedError(
                model=agent._model.value,
                error=str(e),
                has_fallback=False,
                usage=attempt.usage,
                usage_by_model=attempt.usage_by_model,
            ) from e

    attempt = Attempt.start(agent._fallback.model, base_messages, ctx.ledger)
    try:
        client = ctx.acquire(agent._fallback.model)
        async with closing(
            stream_with_client(
                ctx,
                client=client,
                model=agent._fallback.model,
                attempt=attempt,
                guard=guard,
            )
        ) as events:
            async for event in events:
                yield event
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
        main_attempt = Attempt.start(agent._model, base_messages, ctx.ledger)
        try:
            main_client = ctx.acquire(agent._model)
            async with closing(
                stream_with_client(
                    ctx,
                    client=main_client,
                    model=agent._model,
                    attempt=main_attempt,
                    guard=guard,
                )
            ) as events:
                async for event in events:
                    yield event
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
