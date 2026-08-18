"""Hook-event emission and streamed-terminal value objects (DESIGN §3).

Free functions: none of them touch Agent state — everything they need
rides on the RunContext or their arguments.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from neosian._foundation.agent.hooks import FallbackEvent, LlmCallEvent, TurnEvent
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import Message, ModelUsage, Role, ToolCall, Usage
from neosian._foundation.shared.types import GuardrailResult, Model, PolicyResult
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import Attempt, RunContext


async def emit_turn(
    ctx: RunContext, response: AgentResponse, *, streamed: bool
) -> None:
    """Fire on_turn with the run's duration."""
    await ctx.hooks.turn(
        TurnEvent(
            response=response,
            streamed=streamed,
            duration_ms=int((time.monotonic() - ctx.started) * 1000),
        )
    )


async def emit_llm_call(
    ctx: RunContext,
    *,
    model: Model,
    iteration: int,
    streamed: bool,
    started: float,
    api_model: str | None,
    usage: Usage | None,
    stop_reason: str | None,
    error: BaseException | None = None,
) -> None:
    """Fire on_llm_call for one completed (or failed) provider call."""
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
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=code if isinstance(code, str) else None,
        )
    )


async def emit_fallback(
    ctx: RunContext,
    *,
    from_model: str,
    to_model: str,
    reason: str,
    cause: BaseException | None,
    sticky: bool,
    streamed: bool,
) -> None:
    """Fire on_fallback for one model switch."""
    code = getattr(cause, "code", None) if cause is not None else None
    status = getattr(cause, "status", None) if cause is not None else None
    await ctx.hooks.fallback(
        FallbackEvent(
            from_model=from_model,
            to_model=to_model,
            reason=reason,
            cause_code=code if isinstance(code, str) else None,
            provider_status=status if isinstance(status, int) else None,
            sticky=sticky,
            streamed=streamed,
        )
    )


def blocked_response(
    policy: PolicyResult | None,
    usage: Usage | None,
    usage_by_model: tuple[ModelUsage, ...],
) -> AgentResponse:
    """The value object for an input-guard block on the streaming path.

    Content is discarded but the billed usage survives; turn_messages
    stays empty — a blocked turn is not replayable.
    """
    return AgentResponse(
        message=Message(role=Role.ASSISTANT, content=""),
        usage=(usage if usage is not None else Usage(input_tokens=0, output_tokens=0)),
        blocked=True,
        guardrail_result=GuardrailResult(
            safe=False,
            flagged_at="input",
            input_policy=policy,
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
    )
