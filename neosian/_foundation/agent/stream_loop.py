"""The streaming tool loop (DESIGN §3)."""

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
from neosian._foundation.agent.hooks import ToolEvent
from neosian._foundation.agent.stream_final import stream_final_with_client_and_guard
from neosian._foundation.agent.streaming import (
    SSEEventEmitter,
    blocked_event,
    content_event,
    done_event,
    reasoning_event,
    tool_call_event,
)
from neosian._foundation.agent.tool_exec import format_tool_result, run_tool_stream
from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    ToolCall,
    Usage,
)
from neosian._foundation.shared.types import Model, PolicyResult, ToolCallId
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import Attempt, RunContext


async def stream_with_client(
    ctx: RunContext,
    client: BaseLLMClient,
    model: Model,
    attempt: Attempt,
    guard_task: asyncio.Task[tuple[bool, PolicyResult | None]] | None,
    emitter: SSEEventEmitter | None = None,
) -> AsyncIterator[str]:
    """Stream agent response with a specific client while monitoring guard task.

    Args:
        client: LLM client to use.
        model: Model identifier.
        attempt: This try's message snapshot and usage ledger; the
            ledger outlives an exception, so the caller reads billed
            usage off the attempt when this generator raises.
        guard_task: Background guard task to monitor (or None if no guard).
        emitter: SSE event emitter for metadata. If None, creates a new one.

    Yields:
        SSE-formatted strings with sequence and created_at metadata.
    """
    agent = ctx.agent
    if emitter is None:
        emitter = SSEEventEmitter()

    # Silently drop reasoning_effort if model doesn't support it (graceful fallback)
    effective_reasoning = agent._reasoning_effort if model.supports_reasoning else None

    # The attempt ledger sums completed iterations (mirrors the
    # non-streaming path); final_usage is last-wins within the current
    # turn so a provider's partial usage chunk is overwritten by the
    # complete one. Hoisted above the try so the except handler can
    # fold a mid-turn remainder before the exception escapes.
    final_usage: Usage | None = None
    turn_api_model: str | None = None
    iteration = 0
    call_started = time.monotonic()
    in_final = False
    run_tool_calls: list[ToolCall] = []
    run_tool_results: list[ToolResult[Any]] = []

    try:
        for iteration in range(agent._max_tool_iterations):
            # Check guard before each LLM call
            if guard_task is not None and guard_task.done():
                blocked_event_sse = await check_guard_and_block(
                    ctx,
                    guard_task,
                    emitter,
                    usage=attempt.usage,
                    usage_by_model=attempt.usage_by_model,
                )
                if blocked_event_sse:
                    yield blocked_event_sse
                    return
                guard_task = None  # Don't check again

            # Stream LLM response (real-time content + tool call detection)
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            accumulated_tool_calls: list[ToolCall] = []
            final_usage = None
            turn_api_model = None
            turn_finish_reason: str | None = None

            call_started = time.monotonic()
            stream = client.stream(
                messages=attempt.messages,
                model=model,
                tools=agent._tool_definitions if agent._tool_definitions else None,
                reasoning_effort=effective_reasoning,
                max_tokens=agent._max_output_tokens,
                cache_conversation=agent._cache_conversation,
            )

            async for chunk in stream:
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

                if chunk.model:
                    turn_api_model = chunk.model

                if chunk.reasoning:
                    reasoning_parts.append(chunk.reasoning)
                    yield emitter.emit(reasoning_event(chunk.reasoning))

                if chunk.content:
                    content_parts.append(chunk.content)
                    yield emitter.emit(content_event(chunk.content))

                if chunk.tool_calls:
                    accumulated_tool_calls.extend(chunk.tool_calls)

                if chunk.usage:
                    final_usage = chunk.usage

                if chunk.finish_reason:
                    turn_finish_reason = chunk.finish_reason

            # Turn complete — fold its usage into the ledger and reset
            # so guard checks / the except handler don't double count.
            turn_usage = final_usage
            if final_usage is not None:
                attempt.record(turn_api_model, final_usage)
            final_usage = None
            await emit_llm_call(
                ctx,
                model=model,
                iteration=iteration,
                streamed=True,
                started=call_started,
                api_model=turn_api_model,
                usage=turn_usage,
                stop_reason=turn_finish_reason,
            )

            # Stream complete — no tool calls means final response
            if not accumulated_tool_calls:
                # Final guard await before done
                if guard_task is not None:
                    is_safe, policy = await await_guard_result_safe(agent, guard_task)
                    if (
                        not is_safe
                        and agent._guardrails
                        and agent._guardrails.block_on_input
                    ):
                        rationale = policy.rationale if policy else None
                        yield emitter.emit(
                            blocked_event(rationale=rationale, usage=attempt.usage)
                        )
                        await emit_turn(
                            ctx,
                            blocked_response(
                                policy, attempt.usage, attempt.usage_by_model
                            ),
                            streamed=True,
                        )
                        return

                yield emitter.emit(
                    done_event(
                        attempt.usage,
                        stop_reason=turn_finish_reason,
                        model=model.value,
                    )
                )
                final_message = Message(
                    role=Role.ASSISTANT,
                    content="".join(content_parts) if content_parts else None,
                    reasoning=("".join(reasoning_parts) if reasoning_parts else None),
                )
                await emit_turn(
                    ctx,
                    stream_response(
                        attempt,
                        final_message,
                        run_tool_calls,
                        run_tool_results,
                        stop_reason=turn_finish_reason,
                        model=turn_api_model or model.value,
                    ),
                    streamed=True,
                )
                return

            # Tool calls detected — add assistant message to history
            attempt.messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content="".join(content_parts) if content_parts else None,
                    reasoning=("".join(reasoning_parts) if reasoning_parts else None),
                    tool_calls=accumulated_tool_calls,
                )
            )
            run_tool_calls.extend(accumulated_tool_calls)

            # Pre-batch guard check. Note: with parallel execution the guard
            # cannot interrupt mid-batch; in-flight tools run to completion.
            # The post-batch check below blocks the next LLM call.
            if guard_task is not None and guard_task.done():
                blocked_event_sse = await check_guard_and_block(
                    ctx,
                    guard_task,
                    emitter,
                    usage=attempt.usage,
                    usage_by_model=attempt.usage_by_model,
                )
                if blocked_event_sse:
                    yield blocked_event_sse
                    return
                guard_task = None

            # 1. Emit all tool_call events first, in LLM submission order.
            #    SSEEventEmitter.emit() has no awaits — sequence assignment
            #    is atomic under cooperative async.
            for tool_call in accumulated_tool_calls:
                yield emitter.emit(tool_call_event(tool_call))

            # 2. Spawn one wrapper task per tool; concurrency capped by
            #    semaphore. tool_result events arrive in completion order;
            #    each wrapper coalesces its completion into a single None
            #    sentinel via the shared counter (see _run_tool_stream).
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            results: dict[ToolCallId, ToolResult[Any]] = {}
            durations: dict[ToolCallId, int] = {}
            counter = [len(accumulated_tool_calls)]
            semaphore = asyncio.Semaphore(agent._max_parallel_tools)
            tasks = [
                asyncio.create_task(
                    run_tool_stream(
                        agent,
                        tc,
                        emitter,
                        queue,
                        results,
                        durations,
                        counter,
                        semaphore,
                    )
                )
                for tc in accumulated_tool_calls
            ]

            # 3. Drain queue; cancel surviving tasks on early exit (e.g.
            #    consumer disconnect) to prevent orphaned tool tasks.
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item
                await asyncio.gather(*tasks)
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()

            # 4. Post-batch guard check.
            if guard_task is not None and guard_task.done():
                blocked_event_sse = await check_guard_and_block(
                    ctx,
                    guard_task,
                    emitter,
                    usage=attempt.usage,
                    usage_by_model=attempt.usage_by_model,
                )
                if blocked_event_sse:
                    yield blocked_event_sse
                    return
                guard_task = None

            # 5. Append Tool messages in SUBMISSION order (Anthropic API
            #    contract). SSE emission used completion order; LLM history
            #    must use submission order — and on_tool fires in the same
            #    order, matching the blocking path's hook sequence.
            for tool_call in accumulated_tool_calls:
                result = results[tool_call.id]
                run_tool_results.append(result)
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
                        duration_ms=durations[tool_call.id],
                        iteration=iteration,
                    )
                )

        # Max iterations reached - stream final response without tools
        in_final = True
        async for sse in stream_final_with_client_and_guard(
            ctx,
            client=client,
            model=model,
            attempt=attempt,
            guard_task=guard_task,
            emitter=emitter,
            reasoning_effort=effective_reasoning,
            run_tool_calls=run_tool_calls,
            run_tool_results=run_tool_results,
        ):
            yield sse
    except Exception as exc:
        # Fold the mid-turn remainder into the ledger before the
        # exception escapes — the caller reads billed usage off the
        # attempt. Exception (not BaseException) deliberately excludes
        # GeneratorExit/CancelledError from consumer disconnects.
        if final_usage is not None:
            attempt.record(turn_api_model, final_usage)
        if not in_final:
            # The final call reports its own llm_call event; a failure
            # in this loop's drain is this call's to report.
            await emit_llm_call(
                ctx,
                model=model,
                iteration=iteration,
                streamed=True,
                started=call_started,
                api_model=turn_api_model,
                usage=final_usage,
                stop_reason=None,
                error=exc,
            )
        raise
