"""Streaming orchestration: the guard watch and the event sequencer over
the fallback plan (DESIGN §3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from neosian._foundation.agent.context import Attempt
from neosian._foundation.agent.emit import emit_fallback
from neosian._foundation.agent.events import AgentEvent, EventSequencer, ReadyEvent
from neosian._foundation.agent.lifetimes import GuardWatch, closing
from neosian._foundation.agent.plan import (
    Leg,
    build_plan,
    give_up,
    record_success,
    switch_after,
)
from neosian._foundation.agent.stream_loop import stream_with_client
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.registry import provider_label

if TYPE_CHECKING:
    from neosian._foundation.agent.context import RunContext


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
    """Stream the plan's legs in order while monitoring the guard task —
    the streaming driver; `execute_agent_core` is its blocking twin.

    Args:
        ctx: Per-run context (client acquisition, sticky fallback state).
        messages: Conversation history (without system message).
        guard: The run's guard watch.

    Yields:
        Unstamped AgentEvent values — run_streaming assigns sequence.

    Raises:
        ModelFailedError: If the model fails and no leg is left.
        FallbackExhaustedError: If every leg fails.
    """
    agent = ctx.agent
    base_messages = [
        Message(role=Role.SYSTEM, content=agent._system_prompt),
        *messages,
    ]
    plan = build_plan(ctx, base_messages)
    if plan.preamble is not None:
        await emit_fallback(ctx, plan.preamble, streamed=True)
    failures: list[tuple[Leg, Exception]] = []
    for leg, next_leg in zip(plan.legs, (*plan.legs[1:], None), strict=True):
        # A fresh snapshot per leg: a failed leg's partial tool rounds
        # never reach the next one; the billed usage carries on the ledger.
        attempt = Attempt.start(leg.model, base_messages, ctx.ledger)
        try:
            async with closing(
                stream_with_client(
                    ctx,
                    client=ctx.acquire(leg.model),
                    model=leg.model,
                    attempt=attempt,
                    guard=guard,
                )
            ) as events:
                async for event in events:
                    yield event
        except Exception as e:
            failures.append((leg, e))
            if next_leg is not None:
                switch = switch_after(agent, leg, next_leg, e, base_messages, attempt)
                await emit_fallback(ctx, switch, streamed=True)
            continue
        record_success(leg, ctx.fallback_state)
        return
    give_up(agent, failures, attempt)
