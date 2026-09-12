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
from neosian._foundation.agent.events import (
    AgentEvent,
    BlockedEvent,
    ContentEvent,
    DoneEvent,
    ReasoningEvent,
    ToolCallEvent,
)
from neosian._foundation.agent.guards import (
    check_guard_and_block,
)
from neosian._foundation.agent.hooks import ToolEvent
from neosian._foundation.agent.lifetimes import closing
from neosian._foundation.agent.stream_final import stream_final_with_client_and_guard
from neosian._foundation.agent.tool_exec import format_tool_result, run_tool_stream
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompactionBlock,
    Message,
    Role,
    ToolCall,
    Usage,
    assemble_streamed_content,
    normalize_stop_reason,
)
from neosian._foundation.shared.types import AnyModel, ToolCallId
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import Attempt, RunContext
    from neosian._foundation.agent.lifetimes import GuardWatch


async def stream_with_client(
    ctx: RunContext,
    client: BaseLLMClient,
    model: AnyModel,
    attempt: Attempt,
    guard: GuardWatch,
) -> AsyncIterator[AgentEvent]:
    """Stream agent response with a specific client while monitoring guard task.

    Args:
        client: LLM client to use.
        model: Model identifier.
        attempt: This try's message snapshot and usage ledger; the
            ledger outlives an exception, so the caller reads billed
            usage off the attempt when this generator raises.
        guard: The run's guard watch — a finished verdict is polled
            before each call and after each tool batch.

    Yields:
        Unstamped AgentEvent values — run_streaming assigns sequence.
    """
    agent = ctx.agent

    # Silently drop reasoning_effort if model doesn't support it (graceful fallback)
    effective_reasoning = agent._reasoning_effort if model.supports_reasoning else None

    # Proactive window check, once per attempt before any spend; raised
    # outside the loop's try so no on_llm_call fires for a call never
    # made. Mid-run growth falls to the reactive wrap (DESIGN §5).
    if agent._context_policy is not None:
        agent._context_policy.ensure_fits(model, attempt.messages)

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
            if (guard_task := guard.poll()) is not None:
                blocked = await check_guard_and_block(
                    ctx,
                    guard_task,
                    usage=attempt.usage,
                    usage_by_model=attempt.usage_by_model,
                )
                if blocked:
                    yield blocked
                    return

            # Stream LLM response (real-time content + tool call detection)
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            accumulated_tool_calls: list[ToolCall] = []
            accumulated_compaction: list[CompactionBlock] = []
            message_extra: dict[str, Any] | None = None
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
                server_compaction=agent._server_compaction,
            )

            async with closing(stream):
                async for chunk in stream:
                    # Check guard during streaming
                    if (guard_task := guard.poll()) is not None:
                        blocked = await check_guard_and_block(
                            ctx,
                            guard_task,
                            usage=merge_usage(attempt.usage, final_usage),
                            usage_by_model=attempt.usage_by_model,
                        )
                        if blocked:
                            yield blocked
                            return

                    if chunk.model:
                        turn_api_model = chunk.model

                    if chunk.reasoning:
                        reasoning_parts.append(chunk.reasoning)
                        yield ReasoningEvent(reasoning=chunk.reasoning)

                    if chunk.content:
                        content_parts.append(chunk.content)
                        yield ContentEvent(content=chunk.content)

                    if chunk.tool_calls:
                        accumulated_tool_calls.extend(chunk.tool_calls)

                    if chunk.compaction:
                        accumulated_compaction.extend(chunk.compaction)

                    if chunk.extra:
                        message_extra = chunk.extra

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
                is_safe, policy = await guard.verdict()
                if (
                    not is_safe
                    and agent._guardrails
                    and agent._guardrails.block_on_input
                ):
                    rationale = policy.rationale if policy else None
                    # Hook before the terminal yield: a consumer that
                    # saw the terminal event has had on_turn run
                    # (register #6).
                    await emit_turn(
                        ctx,
                        blocked_response(policy, attempt.usage, attempt.usage_by_model),
                        streamed=True,
                    )
                    yield BlockedEvent(
                        rationale=rationale,
                        usage=attempt.usage,
                        usage_by_model=attempt.usage_by_model,
                    )
                    return

                normalized = (
                    normalize_stop_reason(turn_finish_reason)
                    if turn_finish_reason
                    else None
                )
                final_message = Message(
                    role=Role.ASSISTANT,
                    content=assemble_streamed_content(
                        "".join(content_parts) if content_parts else None,
                        tuple(accumulated_compaction),
                    ),
                    reasoning=("".join(reasoning_parts) if reasoning_parts else None),
                    extra=message_extra,
                )
                # Hook before the terminal yield: a consumer that saw
                # `done` has had on_turn run (register #6).
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
                yield DoneEvent(
                    model=turn_api_model or model.value,
                    stop_reason=normalized.value if normalized else None,
                    raw_stop_reason=turn_finish_reason,
                    usage=attempt.usage,
                    usage_by_model=attempt.usage_by_model,
                )
                return

            # Tool calls detected — add assistant message to history
            attempt.messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=assemble_streamed_content(
                        "".join(content_parts) if content_parts else None,
                        tuple(accumulated_compaction),
                    ),
                    reasoning=("".join(reasoning_parts) if reasoning_parts else None),
                    tool_calls=accumulated_tool_calls,
                    extra=message_extra,
                )
            )
            run_tool_calls.extend(accumulated_tool_calls)

            # Pre-batch guard check. Note: with parallel execution the guard
            # cannot interrupt mid-batch; in-flight tools run to completion.
            # The post-batch check below blocks the next LLM call.
            if (guard_task := guard.poll()) is not None:
                blocked = await check_guard_and_block(
                    ctx,
                    guard_task,
                    usage=attempt.usage,
                    usage_by_model=attempt.usage_by_model,
                )
                if blocked:
                    yield blocked
                    return

            # 1. Emit all tool_call events first, in LLM submission order.
            #    Sequence numbers are assigned by run_streaming's single
            #    stamping pass, atomic under cooperative async.
            for tool_call in accumulated_tool_calls:
                yield ToolCallEvent(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                )

            # 2. Spawn one wrapper task per tool; concurrency capped by
            #    semaphore. tool_result events arrive in completion order;
            #    each wrapper coalesces its completion into a single None
            #    sentinel via the shared counter (see _run_tool_stream).
            queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
            results: dict[ToolCallId, ToolResult[Any]] = {}
            durations: dict[ToolCallId, int] = {}
            counter = [len(accumulated_tool_calls)]
            semaphore = asyncio.Semaphore(agent._max_parallel_tools)
            tasks = [
                asyncio.create_task(
                    run_tool_stream(
                        agent,
                        tc,
                        queue,
                        results,
                        durations,
                        counter,
                        semaphore,
                    )
                )
                for tc in accumulated_tool_calls
            ]

            # 3. Drain queue. On early exit (a consumer disconnect) cancel
            #    the surviving wrappers and wait them out: each wrapper's
            #    own finally cancels the tool it shielded (AG-1), so
            #    aclose() returns only once every tool has stopped.
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
                await asyncio.gather(*tasks, return_exceptions=True)

            # 4. Post-batch guard check.
            if (guard_task := guard.poll()) is not None:
                blocked = await check_guard_and_block(
                    ctx,
                    guard_task,
                    usage=attempt.usage,
                    usage_by_model=attempt.usage_by_model,
                )
                if blocked:
                    yield blocked
                    return

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
        async with closing(
            stream_final_with_client_and_guard(
                ctx,
                client=client,
                model=model,
                attempt=attempt,
                guard=guard,
                reasoning_effort=effective_reasoning,
                run_tool_calls=run_tool_calls,
                run_tool_results=run_tool_results,
            )
        ) as events:
            async for event in events:
                yield event
    except Exception as exc:
        # Fold the mid-turn remainder into the ledger before the
        # exception escapes — the caller reads billed usage off the
        # attempt. Exception (not BaseException) deliberately excludes
        # GeneratorExit/CancelledError from consumer disconnects.
        # `fold`, not `record`: a budget raise here would mask `exc`.
        if final_usage is not None:
            attempt.fold(turn_api_model, final_usage)
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
