"""Hook-event emission and streamed-terminal value objects (DESIGN §3).

Free functions: none of them touch Agent state — everything they need
rides on the RunContext or their arguments. Every event carries the
run's identity (N7, ledger #333) and the call events their wall-clock
start, so a span drawn after the fact lands where the work happened.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from neosian._foundation.agent.hooks import (
    CallPurpose,
    FallbackEvent,
    LlmCallEvent,
    ToolEvent,
    TurnEvent,
)
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import Message, ModelUsage, Role, ToolCall, Usage
from neosian._foundation.shared.types import (
    AnyModel,
    GuardrailResult,
    PolicyResult,
)
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import Attempt, RunContext
    from neosian._foundation.agent.plan import Switch


@dataclass(frozen=True, slots=True)
class ToolTiming:
    """When one tool ran: its wall-clock start and duration (#333). The
    loops time each tool where it runs, so a parallel batch reports every
    tool's own start rather than the batch's end."""

    started_at: datetime
    duration_ms: int

    @staticmethod
    def start() -> tuple[float, datetime]:
        return time.monotonic(), datetime.now(UTC)

    @classmethod
    def end(cls, start: tuple[float, datetime]) -> ToolTiming:
        monotonic, wall = start
        return cls(
            started_at=wall, duration_ms=int((time.monotonic() - monotonic) * 1000)
        )


def _started_at(started: float, now: float) -> datetime:
    """The wall-clock twin of a monotonic start."""
    return datetime.now(UTC) - timedelta(seconds=now - started)


async def emit_turn(
    ctx: RunContext, response: AgentResponse, *, streamed: bool
) -> None:
    """Fire on_turn with the run's duration."""
    await ctx.hooks.turn(
        TurnEvent(
            response=response,
            streamed=streamed,
            duration_ms=int((time.monotonic() - ctx.started) * 1000),
            run_id=ctx.run_id,
            parent_run_id=ctx.parent_run_id,
            conversation_id=ctx.conversation_id,
        )
    )


async def emit_llm_call(
    ctx: RunContext,
    *,
    model: AnyModel,
    iteration: int,
    streamed: bool,
    started: float,
    api_model: str | None,
    usage: Usage | None,
    stop_reason: str | None,
    error: BaseException | None = None,
    purpose: CallPurpose = "agent",
) -> None:
    """Fire on_llm_call for one completed (or failed) billed call."""
    now = time.monotonic()
    code = getattr(error, "code", None) if error is not None else None
    await ctx.hooks.llm_call(
        LlmCallEvent(
            requested_model=model.value,
            model=api_model,
            provider=model.provider,
            iteration=iteration,
            streamed=streamed,
            usage=usage,
            stop_reason=stop_reason,
            duration_ms=int((now - started) * 1000),
            error_code=code if isinstance(code, str) else None,
            purpose=purpose,
            started_at=_started_at(started, now),
            run_id=ctx.run_id,
            parent_run_id=ctx.parent_run_id,
            conversation_id=ctx.conversation_id,
        )
    )


def tool_event(
    ctx: RunContext,
    tool_call: ToolCall,
    result: ToolResult[Any],
    *,
    timing: ToolTiming,
    iteration: int,
) -> ToolEvent:
    """The on_tool event for one finished tool, on either path."""
    return ToolEvent(
        call_id=tool_call.id,
        name=tool_call.name,
        arguments=tool_call.arguments,
        result=result,
        duration_ms=timing.duration_ms,
        iteration=iteration,
        started_at=timing.started_at,
        run_id=ctx.run_id,
        parent_run_id=ctx.parent_run_id,
        conversation_id=ctx.conversation_id,
    )


async def emit_fallback(ctx: RunContext, switch: Switch, *, streamed: bool) -> None:
    """Fire on_fallback for one model switch."""
    cause = switch.cause
    code = getattr(cause, "code", None) if cause is not None else None
    status = getattr(cause, "status", None) if cause is not None else None
    await ctx.hooks.fallback(
        FallbackEvent(
            from_model=switch.from_model,
            to_model=switch.to_model,
            reason=switch.reason,
            cause_code=code if isinstance(code, str) else None,
            provider_status=status if isinstance(status, int) else None,
            sticky=switch.sticky,
            streamed=streamed,
            run_id=ctx.run_id,
            parent_run_id=ctx.parent_run_id,
            conversation_id=ctx.conversation_id,
        )
    )


def blocked_response(
    policy: PolicyResult | None,
    usage: Usage | None,
    usage_by_model: tuple[ModelUsage, ...],
    *,
    checkpoint: Literal["input", "output"] = "input",
) -> AgentResponse:
    """The value object for a guard block at either checkpoint, on either
    path.

    Content is discarded but the billed usage survives; turn_messages
    stays empty — a blocked turn is not replayable.
    """
    return AgentResponse(
        message=Message(role=Role.ASSISTANT, content=""),
        usage=(usage if usage is not None else Usage(input_tokens=0, output_tokens=0)),
        blocked=True,
        guardrail_result=GuardrailResult(
            safe=False,
            flagged_at=checkpoint,
            input_policy=policy if checkpoint == "input" else None,
            output_policy=policy if checkpoint == "output" else None,
        ),
        usage_by_model=usage_by_model,
    )


def stream_response(
    attempt: Attempt,
    message: Message,
    tool_calls_made: list[ToolCall],
    tool_results: list[ToolResult[Any]],
    *,
    stop_reason: str | None,
    model: str | None,
    iterations_exhausted: bool = False,
) -> AgentResponse:
    """The value object for a streamed run's done terminal (on_turn)."""
    usage = attempt.usage
    return AgentResponse(
        message=message,
        tool_calls_made=tuple(tool_calls_made),
        tool_results=tuple(tool_results),
        usage=(usage if usage is not None else Usage(input_tokens=0, output_tokens=0)),
        stop_reason=stop_reason,
        model=model,
        usage_by_model=attempt.usage_by_model,
        turn_messages=attempt.turn_messages(message),
        iterations_exhausted=iterations_exhausted,
    )
