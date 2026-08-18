"""The max-iterations final streaming call, toolless (DESIGN §3)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from neosian._foundation.agent.context import merge_usage
from neosian._foundation.agent.emit import (
    blocked_response,
    emit_llm_call,
    emit_turn,
    stream_response,
)
from neosian._foundation.agent.guards import (
    await_guard_result_safe,
    check_guard_and_block,
)
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.agent.streaming import (
    SSEEventEmitter,
    blocked_event,
    content_event,
    done_event,
    reasoning_event,
)
from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    ToolCall,
    Usage,
)
from neosian._foundation.shared.types import Model, PolicyResult, ReasoningEffort
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import Attempt, RunContext


async def stream_final_with_client_and_guard(
    ctx: RunContext,
    *,
    client: BaseLLMClient,
    model: Model,
    attempt: Attempt,
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]] | None,
    emitter: SSEEventEmitter,
    reasoning_effort: ReasoningEffort | None = None,
    run_tool_calls: list[ToolCall],
    run_tool_results: list[ToolResult[Any]],
) -> AsyncIterator[str]:
    """Stream final response with a specific client while monitoring guard task.

    Args:
        ctx: Per-run context (hook dispatch).
        client: LLM client to use.
        model: Model identifier.
        attempt: The attempt whose messages feed the call and whose
            ledger already holds the exhausted tool iterations' usage.
        guard_task: Background guard task to monitor (or None).
        emitter: SSE event emitter for metadata (required, passed from caller).
        reasoning_effort: Optional reasoning effort (pre-filtered for model support).
        run_tool_calls: Tool calls made across the exhausted iterations,
            for the terminal turn event's response.
        run_tool_results: Their results, submission order.

    Yields:
        SSE-formatted strings for content chunks, blocked, and done event.
    """
    agent = ctx.agent
    # Track usage and pending done for different provider patterns
    # OpenAI/Groq: usage comes in separate chunk after finish_reason
    # Anthropic: usage comes with finish_reason chunk
    pending_done = False
    final_usage: Usage | None = None
    call_usage: Usage | None = None
    final_api_model: str | None = None
    final_finish_reason: str | None = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    # on_turn is deferred past the end-of-stream on_llm_call so the
    # hook sequence matches the blocking path (llm_call, then turn).
    turn_response: AgentResponse | None = None
    call_started = time.monotonic()

    def _final_message() -> Message:
        return Message(
            role=Role.ASSISTANT,
            content="".join(content_parts) if content_parts else None,
            reasoning="".join(reasoning_parts) if reasoning_parts else None,
        )

    try:
        stream = client.stream(
            messages=attempt.messages,
            model=model,
            tools=None,
            reasoning_effort=reasoning_effort,
            max_tokens=agent._max_output_tokens,
            cache_conversation=agent._cache_conversation,
        )

        async for chunk in stream:
            if chunk.model:
                final_api_model = chunk.model

            # Check guard during streaming
            if guard_task is not None and guard_task.done():
                blocked_event_sse = await check_guard_and_block(
                    ctx,
                    guard_task,
                    emitter,
                    usage=merge_usage(attempt.usage, final_usage),
                    usage_by_model=attempt.usage_by_model,
                )
                if blocked_event_sse:
                    yield blocked_event_sse
                    return
                guard_task = None

            # Emit reasoning before content (for reasoning models)
            if chunk.reasoning:
                reasoning_parts.append(chunk.reasoning)
                yield emitter.emit(reasoning_event(chunk.reasoning))

            if chunk.content:
                content_parts.append(chunk.content)
                yield emitter.emit(content_event(chunk.content))

            if chunk.finish_reason:
                final_finish_reason = chunk.finish_reason
                # Final guard check before done (with error handling)
                if guard_task is not None:
                    is_safe, policy = await await_guard_result_safe(agent, guard_task)
                    if (
                        not is_safe
                        and agent._guardrails
                        and agent._guardrails.block_on_input
                    ):
                        rationale = policy.rationale if policy else None
                        # chunk.usage (complete) beats final_usage (possibly
                        # an early partial) — pick, never sum.
                        blocked_usage = merge_usage(
                            attempt.usage, chunk.usage or final_usage
                        )
                        yield emitter.emit(
                            blocked_event(
                                rationale=rationale,
                                usage=blocked_usage,
                            )
                        )
                        await emit_turn(
                            ctx,
                            blocked_response(
                                policy, blocked_usage, attempt.usage_by_model
                            ),
                            streamed=True,
                        )
                        return

                if chunk.usage:
                    # Anthropic: usage comes with finish_reason
                    attempt.record(final_api_model, chunk.usage)
                    call_usage = chunk.usage
                    final_usage = None
                    yield emitter.emit(
                        done_event(
                            attempt.usage,
                            stop_reason=final_finish_reason,
                            model=model.value,
                        )
                    )
                    turn_response = stream_response(
                        attempt,
                        _final_message(),
                        run_tool_calls,
                        run_tool_results,
                        stop_reason=final_finish_reason,
                        model=final_api_model or model.value,
                    )
                else:
                    # OpenAI/Groq: usage may come in next chunk
                    pending_done = True

            # Handle usage-only chunk (OpenAI/Groq pattern)
            if chunk.usage and not chunk.finish_reason and not chunk.content:
                final_usage = chunk.usage
                if pending_done:
                    attempt.record(final_api_model, final_usage)
                    call_usage = final_usage
                    final_usage = None
                    yield emitter.emit(
                        done_event(
                            attempt.usage,
                            stop_reason=final_finish_reason,
                            model=model.value,
                        )
                    )
                    turn_response = stream_response(
                        attempt,
                        _final_message(),
                        run_tool_calls,
                        run_tool_results,
                        stop_reason=final_finish_reason,
                        model=final_api_model or model.value,
                    )
                    pending_done = False

        # If we have a pending done without usage, emit it now
        if pending_done:
            if final_usage is not None:
                attempt.record(final_api_model, final_usage)
                call_usage = final_usage
                final_usage = None
            yield emitter.emit(
                done_event(
                    attempt.usage,
                    stop_reason=final_finish_reason,
                    model=model.value,
                )
            )
            turn_response = stream_response(
                attempt,
                _final_message(),
                run_tool_calls,
                run_tool_results,
                stop_reason=final_finish_reason,
                model=final_api_model or model.value,
            )

        await emit_llm_call(
            ctx,
            model=model,
            iteration=agent._max_tool_iterations,
            streamed=True,
            started=call_started,
            api_model=final_api_model,
            usage=call_usage,
            stop_reason=final_finish_reason,
        )
        if turn_response is not None:
            await emit_turn(ctx, turn_response, streamed=True)
    except Exception as exc:
        # Fold the un-ledgered remainder so the caller's attempt read
        # sees everything billed before the failure.
        if final_usage is not None:
            attempt.record(final_api_model, final_usage)
        await emit_llm_call(
            ctx,
            model=model,
            iteration=agent._max_tool_iterations,
            streamed=True,
            started=call_started,
            api_model=final_api_model,
            usage=final_usage,
            stop_reason=None,
            error=exc,
        )
        raise
