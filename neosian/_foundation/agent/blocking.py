"""Blocking orchestration: input-guard race, model fallback, sticky
fallback (DESIGN §3)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from neosian._foundation.agent.context import Attempt
from neosian._foundation.agent.emit import emit_fallback
from neosian._foundation.agent.fallback import (
    ensure_fallback_viable,
    reraise_caller_errors,
    unsupported_content_types,
)
from neosian._foundation.agent.guards import (
    attach_input_guard_results,
    await_guard_result_safe,
    check_guardrails,
    extract_user_content,
    get_guard_result_safe,
)
from neosian._foundation.agent.loop import execute_with_client
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    FallbackExhaustedError,
    ModelFailedError,
)
from neosian._foundation.shared.types import (
    GuardrailMode,
    GuardrailResult,
    ResponseFormat,
)

if TYPE_CHECKING:
    from neosian._foundation.agent.context import RunContext

logger = logging.getLogger(__name__)


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
    # No input guardrails configured - run agent directly
    if agent._guardrails is None or agent._guardrails.input_mode == GuardrailMode.NONE:
        return await execute_agent_core(ctx, messages, response_format=response_format)

    # Extract user content for guardrail check
    user_content = extract_user_content(messages)
    if not user_content:
        return await execute_agent_core(ctx, messages, response_format=response_format)

    # Run guard and agent in parallel
    guard_task = asyncio.create_task(check_guardrails(agent, user_content, "input"))
    agent_task = asyncio.create_task(
        execute_agent_core(ctx, messages, response_format=response_format)
    )

    # Wait for first to complete
    done, pending = await asyncio.wait(
        [guard_task, agent_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Case 1: Guard finished first
    if guard_task in done and agent_task in pending:
        is_safe, input_policy = get_guard_result_safe(agent, guard_task)

        if not is_safe and agent._guardrails.block_on_input:
            # Cancel agent to save resources
            agent_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await agent_task
            return AgentResponse(
                message=Message(role=Role.ASSISTANT, content=""),
                blocked=True,
                guardrail_result=GuardrailResult(
                    safe=False,
                    flagged_at="input",
                    input_policy=input_policy,
                ),
            )

        # Safe or block_on_input=False - wait for agent and attach guard results
        agent_response = await agent_task
        return attach_input_guard_results(agent_response, input_policy)

    # Case 2: Agent finished first
    agent_response = agent_task.result()

    # Still need guard verdict (with error handling)
    is_safe, input_policy = await await_guard_result_safe(agent, guard_task)

    if not is_safe and agent._guardrails.block_on_input:
        # Agent ran but we must block - discard the response content.
        # The tokens were still billed, so usage survives the discard;
        # turn_messages stays empty (a blocked turn is not replayable).
        return AgentResponse(
            message=Message(role=Role.ASSISTANT, content=""),
            usage=agent_response.usage,
            blocked=True,
            guardrail_result=GuardrailResult(
                safe=False,
                flagged_at="input",
                input_policy=input_policy,
            ),
            usage_by_model=agent_response.usage_by_model,
        )

    # Safe or block_on_input=False - return with guard results
    return attach_input_guard_results(agent_response, input_policy)


async def execute_agent_core(
    ctx: RunContext,
    messages: list[Message],
    response_format: ResponseFormat | None = None,
) -> AgentResponse:
    """Execute the agent LLM and tool loop with optional fallback.

    This is the core agent execution without input guardrail checks.
    Used by both blocking and streaming modes.

    Args:
        ctx: Per-run context (client acquisition, sticky fallback state).
        messages: Conversation history (without system message).
        response_format: Optional structured output configuration.

    Returns:
        AgentResponse with the final message and execution details.

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
            streamed=False,
        )

    # If using fallback (sticky), try fallback first
    if fallback_state is not None and fallback_state.using_fallback:
        return await execute_with_fallback_model(ctx, base_messages, response_format)

    # Try main model
    attempt = Attempt.start(agent._model, base_messages)
    try:
        client = ctx.acquire(agent._model.provider)
        response = await execute_with_client(
            ctx,
            client=client,
            model=agent._model,
            attempt=attempt,
            response_format=response_format,
        )
        # Success on main - reset fallback state if present
        if fallback_state is not None:
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
        return response
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
            streamed=False,
        )
        # Fresh message snapshot (a failed attempt's partial tool rounds
        # must not leak into the fallback's history); billed usage carries.
        fallback_attempt = Attempt.start(
            agent._fallback.model, base_messages, prior=attempt
        )
        try:
            fallback_client = ctx.acquire(agent._fallback.model.provider)
            response = await execute_with_client(
                ctx,
                client=fallback_client,
                model=agent._fallback.model,
                attempt=fallback_attempt,
                response_format=response_format,
            )
            # Success on fallback - update state
            if fallback_state is not None:
                fallback_state.using_fallback = True
                fallback_state.successful_fallback_calls = 1
            return response
        except Exception as fallback_e:
            raise FallbackExhaustedError(
                main_model=agent._model.value,
                main_error=main_error,
                fallback_model=agent._fallback.model.value,
                fallback_error=str(fallback_e),
                usage=fallback_attempt.usage,
                usage_by_model=fallback_attempt.usage_by_model,
            ) from fallback_e


async def execute_with_fallback_model(
    ctx: RunContext,
    base_messages: list[Message],
    response_format: ResponseFormat | None = None,
) -> AgentResponse:
    """Execute with fallback model (sticky mode).

    Args:
        ctx: Per-run context; its fallback_state is non-None here —
            callers dispatch to this method only when sticky.
        base_messages: Full conversation with system message prepended.
        response_format: Optional structured output configuration.

    Returns:
        AgentResponse with the final message and execution details.

    Raises:
        FallbackExhaustedError: If fallback model fails.
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
            response = await execute_with_client(
                ctx,
                client=main_client,
                model=agent._model,
                attempt=attempt,
                response_format=response_format,
            )
            # Main handled it - reset sticky state
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
            return response
        except Exception as e:
            reraise_caller_errors(e, attempt)
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
        response = await execute_with_client(
            ctx,
            client=client,
            model=agent._fallback.model,
            attempt=attempt,
            response_format=response_format,
        )
        # Success - increment counter
        fallback_state.successful_fallback_calls += 1
        return response
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
            streamed=False,
        )
        fallback_error = str(e)
        main_attempt = Attempt.start(agent._model, base_messages, prior=attempt)
        try:
            main_client = ctx.acquire(agent._model.provider)
            response = await execute_with_client(
                ctx,
                client=main_client,
                model=agent._model,
                attempt=main_attempt,
                response_format=response_format,
            )
            # Main recovered - reset state
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
            return response
        except Exception as main_e:
            raise FallbackExhaustedError(
                main_model=agent._model.value,
                main_error=str(main_e),
                fallback_model=agent._fallback.model.value,
                fallback_error=fallback_error,
                usage=main_attempt.usage,
                usage_by_model=main_attempt.usage_by_model,
            ) from main_e
