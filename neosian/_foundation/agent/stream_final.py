"""The max-iterations final streaming call, toolless (DESIGN §3)."""

from __future__ import annotations

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
)
from neosian._foundation.agent.guards import (
    check_guard_and_block,
)
from neosian._foundation.agent.lifetimes import closing
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
from neosian._foundation.shared.types import (
    AnyModel,
    ReasoningEffort,
)
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import Attempt, RunContext
    from neosian._foundation.agent.lifetimes import GuardWatch


async def stream_final_with_client_and_guard(
    ctx: RunContext,
    *,
    client: BaseLLMClient,
    model: AnyModel,
    attempt: Attempt,
    guard: GuardWatch,
    reasoning_effort: ReasoningEffort | None = None,
    run_tool_calls: list[ToolCall],
    run_tool_results: list[ToolResult[Any]],
) -> AsyncIterator[AgentEvent]:
    """Stream final response with a specific client while monitoring guard task.

    Args:
        ctx: Per-run context (hook dispatch).
        client: LLM client to use.
        model: Model identifier.
        attempt: The attempt whose messages feed the call and whose
            ledger already holds the exhausted tool iterations' usage.
        guard: The run's guard watch.
        reasoning_effort: Optional reasoning effort (pre-filtered for model support).
        run_tool_calls: Tool calls made across the exhausted iterations,
            for the terminal turn event's response.
        run_tool_results: Their results, submission order.

    Yields:
        Unstamped AgentEvent values — run_streaming assigns sequence.
    """
    agent = ctx.agent
    final_usage: Usage | None = None
    final_api_model: str | None = None
    final_finish_reason: str | None = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    compaction_blocks: list[CompactionBlock] = []
    message_extra: dict[str, Any] | None = None
    call_started = time.monotonic()

    try:
        stream = client.stream(
            messages=attempt.messages,
            model=model,
            tools=None,
            reasoning_effort=reasoning_effort,
            max_tokens=agent._max_output_tokens,
            cache_conversation=agent._cache_conversation,
            cache_ttl=agent._cache_ttl,
            server_compaction=agent._server_compaction,
        )

        async with closing(stream):
            async for chunk in stream:
                if chunk.model:
                    final_api_model = chunk.model

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

                # Emit reasoning before content (for reasoning models)
                if chunk.reasoning:
                    reasoning_parts.append(chunk.reasoning)
                    yield ReasoningEvent(reasoning=chunk.reasoning)

                if chunk.content:
                    content_parts.append(chunk.content)
                    yield ContentEvent(content=chunk.content)

                if chunk.compaction:
                    compaction_blocks.extend(chunk.compaction)

                if chunk.extra:
                    message_extra = chunk.extra

                if chunk.usage:
                    # Last-wins within the call: a provider's early partial
                    # usage chunk is overwritten by the complete one, and a
                    # trailing usage-only chunk is the only one there is.
                    final_usage = chunk.usage

                if chunk.finish_reason:
                    final_finish_reason = chunk.finish_reason

        # The stream ended — with or without a finish_reason the call
        # completed, so the terminal is built unconditionally (AG-4), in
        # the tool loop's order: ledger, on_llm_call, the verdict, then
        # on_turn before the terminal yield (register #6).
        call_usage, final_usage = final_usage, None
        if call_usage is not None:
            attempt.record(final_api_model, call_usage)
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

        is_safe, policy = await guard.verdict()
        if not is_safe and agent._guardrails and agent._guardrails.block_on_input:
            await emit_turn(
                ctx,
                blocked_response(policy, attempt.usage, attempt.usage_by_model),
                streamed=True,
            )
            yield BlockedEvent(
                rationale=policy.rationale if policy else None,
                usage=attempt.usage,
                usage_by_model=attempt.usage_by_model,
            )
            return

        final_message = Message(
            role=Role.ASSISTANT,
            content=assemble_streamed_content(
                "".join(content_parts) if content_parts else None,
                tuple(compaction_blocks),
            ),
            reasoning="".join(reasoning_parts) if reasoning_parts else None,
            extra=message_extra,
        )
        api_model = final_api_model or model.value
        await emit_turn(
            ctx,
            stream_response(
                attempt,
                final_message,
                run_tool_calls,
                run_tool_results,
                stop_reason=final_finish_reason,
                model=api_model,
                iterations_exhausted=True,
            ),
            streamed=True,
        )
        normalized = (
            normalize_stop_reason(final_finish_reason) if final_finish_reason else None
        )
        yield DoneEvent(
            model=api_model,
            stop_reason=normalized.value if normalized else None,
            raw_stop_reason=final_finish_reason,
            usage=attempt.usage,
            usage_by_model=attempt.usage_by_model,
            iterations_exhausted=True,
        )
    except Exception as exc:
        # Fold the un-ledgered remainder so the caller's attempt read
        # sees everything billed before the failure. `fold`, not
        # `record`: a budget raise here would mask `exc`.
        if final_usage is not None:
            attempt.fold(final_api_model, final_usage)
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
