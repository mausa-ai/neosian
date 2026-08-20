"""The blocking tool loop and response finalization."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from neosian._foundation.agent.emit import emit_llm_call
from neosian._foundation.agent.guards import check_guardrails
from neosian._foundation.agent.hooks import ToolEvent
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.agent.tool_exec import execute_tool, format_tool_result
from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    ToolCall,
    Usage,
    text_of,
)
from neosian._foundation.shared.types import (
    GuardrailResult,
    Model,
    PolicyResult,
    ResponseFormat,
)
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.context import Attempt, RunContext


async def execute_with_client(
    ctx: RunContext,
    client: BaseLLMClient,
    model: Model,
    attempt: Attempt,
    response_format: ResponseFormat | None = None,
) -> AgentResponse:
    """Execute the agent with a specific client and model.

    Args:
        ctx: Per-run context (hook dispatch).
        client: LLM client to use.
        model: Model identifier.
        attempt: This try's message snapshot and usage ledger; the
            ledger outlives an exception, so the caller reads billed
            usage off the attempt when this method raises.
        response_format: Optional structured output configuration.

    Returns:
        AgentResponse with the final message and execution details.
    """
    agent = ctx.agent
    all_tool_calls: list[ToolCall] = []
    all_tool_results: list[ToolResult[Any]] = []

    # Silently drop reasoning_effort if model doesn't support it (graceful fallback)
    effective_reasoning = agent._reasoning_effort if model.supports_reasoning else None

    # Proactive window check, once per attempt before any spend; raised
    # here (not inside the call try) so no on_llm_call fires for a call
    # never made. Mid-run growth falls to the reactive wrap (DESIGN §5).
    if agent._context_policy is not None:
        agent._context_policy.ensure_fits(model, attempt.messages)

    for iteration in range(agent._max_tool_iterations):
        # Get completion from LLM
        call_started = time.monotonic()
        try:
            response = await client.complete(
                messages=attempt.messages,
                model=model,
                tools=agent._tool_definitions if agent._tool_definitions else None,
                response_format=response_format,
                reasoning_effort=effective_reasoning,
                max_tokens=agent._max_output_tokens,
                cache_conversation=agent._cache_conversation,
                server_compaction=agent._server_compaction,
            )
        except Exception as exc:
            # A failed call is still a call (input tokens may have been
            # billed); error_code distinguishes it for metering.
            await emit_llm_call(
                ctx,
                model=model,
                iteration=iteration,
                streamed=False,
                started=call_started,
                api_model=None,
                usage=None,
                stop_reason=None,
                error=exc,
            )
            raise

        await emit_llm_call(
            ctx,
            model=model,
            iteration=iteration,
            streamed=False,
            started=call_started,
            api_model=response.model,
            usage=response.usage,
            stop_reason=response.stop_reason,
        )

        # Ledger the usage under the API-reported model
        attempt.record(response.model, response.usage)

        # If no tool calls, we're done - check output guardrails
        if not response.message.tool_calls:
            return await finalize_response(
                agent,
                attempt,
                message=response.message,
                tool_calls_made=all_tool_calls,
                tool_results=all_tool_results,
                response_format=response_format,
                stop_reason=response.stop_reason,
                model=response.model,
            )

        # Add assistant message with tool calls to history
        attempt.messages.append(response.message)

        # Execute tool calls in parallel, capped by max_parallel_tools.
        # _execute_tool wraps all failures in ToolResult.fail (see below),
        # so plain asyncio.gather (no return_exceptions=True) is correct —
        # any leaked exception surfaces as a real bug.
        tool_calls = response.message.tool_calls
        all_tool_calls.extend(tool_calls)

        semaphore = asyncio.Semaphore(agent._max_parallel_tools)

        async def _gated_execute(
            tc: ToolCall, *, sem: asyncio.Semaphore = semaphore
        ) -> tuple[ToolResult[Any], int]:
            async with sem:
                tool_started = time.monotonic()
                result = await execute_tool(agent, tc)
                return result, int((time.monotonic() - tool_started) * 1000)

        timed_results = await asyncio.gather(*(_gated_execute(tc) for tc in tool_calls))

        # Append in submission order — Anthropic requires tool_result
        # blocks to match the order of tool_use blocks in the preceding
        # assistant message. on_tool fires in the same order so blocking
        # and streaming runs emit identical hook sequences.
        for tool_call, (result, duration_ms) in zip(
            tool_calls, timed_results, strict=True
        ):
            all_tool_results.append(result)
            tool_result_content = format_tool_result(result)
            attempt.messages.append(
                Message(
                    role=Role.TOOL,
                    content=tool_result_content,
                    tool_call_id=tool_call.id,
                )
            )
            await ctx.hooks.tool(
                ToolEvent(
                    call_id=tool_call.id,
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                    result=result,
                    duration_ms=duration_ms,
                    iteration=iteration,
                )
            )

    # Max iterations reached - return last response
    call_started = time.monotonic()
    try:
        final_response = await client.complete(
            messages=attempt.messages,
            model=model,
            tools=None,  # No tools on final call to force text response
            response_format=response_format,
            reasoning_effort=effective_reasoning,
            max_tokens=agent._max_output_tokens,
            cache_conversation=agent._cache_conversation,
            server_compaction=agent._server_compaction,
        )
    except Exception as exc:
        await emit_llm_call(
            ctx,
            model=model,
            iteration=agent._max_tool_iterations,
            streamed=False,
            started=call_started,
            api_model=None,
            usage=None,
            stop_reason=None,
            error=exc,
        )
        raise

    await emit_llm_call(
        ctx,
        model=model,
        iteration=agent._max_tool_iterations,
        streamed=False,
        started=call_started,
        api_model=final_response.model,
        usage=final_response.usage,
        stop_reason=final_response.stop_reason,
    )

    attempt.record(final_response.model, final_response.usage)

    return await finalize_response(
        agent,
        attempt,
        message=final_response.message,
        tool_calls_made=all_tool_calls,
        tool_results=all_tool_results,
        response_format=response_format,
        stop_reason=final_response.stop_reason,
        model=final_response.model,
    )


async def finalize_response(
    agent: Agent,
    attempt: Attempt,
    *,
    message: Message,
    tool_calls_made: list[ToolCall],
    tool_results: list[ToolResult[Any]],
    response_format: ResponseFormat | None = None,
    stop_reason: str | None = None,
    model: str | None = None,
) -> AgentResponse:
    """Finalize response with output guardrails check and structured output parsing.

    Checks output guardrails if configured and returns the final AgentResponse.
    Input guardrail results are attached separately via _attach_input_guard_results.
    Usage, its per-model split, and the turn's messages all come off the
    attempt — one source of truth, no field threading.

    Args:
        attempt: The successful attempt (messages + usage ledger).
        message: The assistant's response message.
        tool_calls_made: List of tool calls made during execution.
        tool_results: Results from tool executions.
        response_format: Optional structured output configuration for parsing.
        stop_reason: Provider-native stop reason of the final completion.
        model: Model string reported by the API for the final completion.

    Returns:
        AgentResponse with output guardrail results and parsed content (if any).
    """
    output_policy: PolicyResult | None = None
    is_output_safe = True
    message_text = text_of(message)

    # Check output guardrails if configured
    if (
        agent._guardrails is not None
        and agent._guardrails.has_output_guardrails
        and message_text
    ):
        is_output_safe, output_policy = await check_guardrails(
            agent, message_text, "output"
        )

    # Build guardrail result if output guardrails were run
    guardrail_result: GuardrailResult | None = None
    if output_policy is not None:
        guardrail_result = GuardrailResult(
            safe=is_output_safe,
            flagged_at="output" if not is_output_safe else None,
            output_policy=output_policy,
        )

    # Parse structured output if response_format was provided
    parsed: BaseModel | None = None
    if response_format is not None and message_text:
        from neosian._foundation.shared.schema import validate_json

        parsed = validate_json(response_format.schema, message_text)

    usage = attempt.usage
    return AgentResponse(
        message=message,
        tool_calls_made=tuple(tool_calls_made),
        tool_results=tuple(tool_results),
        usage=(usage if usage is not None else Usage(input_tokens=0, output_tokens=0)),
        blocked=not is_output_safe,
        guardrail_result=guardrail_result,
        parsed=parsed,
        stop_reason=stop_reason,
        model=model,
        usage_by_model=attempt.usage_by_model,
        turn_messages=attempt.turn_messages(message),
    )
