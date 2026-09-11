"""Blocking orchestration: the input-guard race over the fallback plan
(DESIGN §3)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from neosian._foundation.agent.context import Attempt
from neosian._foundation.agent.emit import blocked_response, emit_fallback
from neosian._foundation.agent.guards import (
    attach_input_guard_results,
    get_guard_result_safe,
)
from neosian._foundation.agent.lifetimes import GuardWatch, reap
from neosian._foundation.agent.loop import execute_with_client
from neosian._foundation.agent.plan import (
    Leg,
    build_plan,
    give_up,
    record_success,
    switch_after,
)
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.types import ResponseFormat

if TYPE_CHECKING:
    from neosian._foundation.agent.context import RunContext


async def run_blocking(
    ctx: RunContext,
    messages: list[Message],
    response_format: ResponseFormat | None = None,
) -> AgentResponse:
    """Execute agent without streaming.

    Input guardrails run in parallel with agent execution for optimal latency.
    Safe users experience no guardrail overhead. If guard flags and block_on_input
    is True, agent response is discarded.

    Args:
        messages: Conversation history (without system message).
        response_format: Optional structured output configuration.

    Returns:
        AgentResponse with the final message and execution details.
    """
    agent = ctx.agent
    guardrails = agent._guardrails
    block_on_input = guardrails is not None and guardrails.block_on_input
    # The watch owns the guard task for the whole run: every exit below —
    # a return, an agent error, a cancellation — leaves the `async with`,
    # which reaps whatever is still pending (AG-2).
    async with GuardWatch.start(ctx, messages) as guard:
        guard_task = guard.task
        if guard_task is None:
            return await execute_agent_core(
                ctx, messages, response_format=response_format
            )

        # Run guard and agent in parallel
        agent_task = asyncio.create_task(
            execute_agent_core(ctx, messages, response_format=response_format)
        )
        try:
            done, _pending = await asyncio.wait(
                [guard_task, agent_task], return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            await reap(agent_task)
            raise

        # Case 1: Guard finished first
        if agent_task not in done:
            is_safe, input_policy = get_guard_result_safe(agent, guard_task)

            if not is_safe and block_on_input:
                # Cancel the agent; what it billed before the cancel is on
                # the run's ledger and rides the blocked response (AG-13).
                await reap(agent_task)
                return blocked_response(
                    input_policy, ctx.ledger.usage, ctx.ledger.usage_by_model
                )

            # Safe or block_on_input=False - wait for agent and attach guard results
            agent_response = await agent_task
            return attach_input_guard_results(agent_response, input_policy, ctx.ledger)

        # Case 2: Agent finished first
        agent_response = agent_task.result()

        # Still need guard verdict (with error handling)
        is_safe, input_policy = await guard.verdict()

        if not is_safe and block_on_input:
            # Agent ran but we must block - discard the response content;
            # the tokens were still billed, so the ledger rides along.
            return blocked_response(
                input_policy, ctx.ledger.usage, ctx.ledger.usage_by_model
            )

        # Safe or block_on_input=False - return with guard results
        return attach_input_guard_results(agent_response, input_policy, ctx.ledger)


async def execute_agent_core(
    ctx: RunContext,
    messages: list[Message],
    response_format: ResponseFormat | None = None,
) -> AgentResponse:
    """Run the plan's legs in order without input-guard checks — the
    blocking driver; `stream_agent_with_guard` is its streamed twin.

    Args:
        ctx: Per-run context (client acquisition, sticky fallback state).
        messages: Conversation history (without system message).
        response_format: Optional structured output configuration.

    Returns:
        AgentResponse with the final message and execution details.

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
        await emit_fallback(ctx, plan.preamble, streamed=False)
    failures: list[tuple[Leg, Exception]] = []
    for leg, next_leg in zip(plan.legs, (*plan.legs[1:], None), strict=True):
        # A fresh snapshot per leg: a failed leg's partial tool rounds
        # never reach the next one; the billed usage carries on the ledger.
        attempt = Attempt.start(leg.model, base_messages, ctx.ledger)
        try:
            response = await execute_with_client(
                ctx,
                client=ctx.acquire(leg.model),
                model=leg.model,
                attempt=attempt,
                response_format=response_format,
            )
        except Exception as e:
            failures.append((leg, e))
            if next_leg is not None:
                switch = switch_after(agent, leg, next_leg, e, base_messages, attempt)
                await emit_fallback(ctx, switch, streamed=False)
            continue
        record_success(leg, ctx.fallback_state)
        return response
    give_up(agent, failures, attempt)
