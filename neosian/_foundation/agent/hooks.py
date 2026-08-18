"""Agent observation hooks (DESIGN §3).

`AgentHooks` is the public seam: four optional callbacks, each taking one
frozen event dataclass and returning None or an awaitable. Hooks observe —
they never alter the run. Exceptions they raise are swallowed and logged
unless `strict=True` (an eval harness wants strict; production wants
swallow). `HookRunner` is the internal dispatcher; hooks are awaited
inline so blocking and streaming runs produce identical hook sequences —
a slow hook therefore stalls the run it observes.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from neosian._foundation.llm.base import Usage
from neosian._foundation.shared.types import Provider, ToolCallId, ToolName
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from neosian._foundation.agent.response import AgentResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LlmCallEvent:
    """One provider completion/stream call — maps 1:1 onto a host's metering.

    Fires after every call, success or failure (failed calls still bill
    input tokens; `error_code` distinguishes). `model` is the API-reported
    string, None when the call raised before reporting one.
    """

    requested_model: str
    model: str | None
    provider: Provider
    iteration: int
    streamed: bool
    usage: Usage | None
    stop_reason: str | None
    duration_ms: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ToolEvent:
    """One completed tool execution.

    Fires in submission order after the iteration's tool batch completes,
    so blocking and streaming runs emit identical sequences; `duration_ms`
    is the individual tool's wall time.
    """

    call_id: ToolCallId
    name: ToolName
    arguments: Mapping[str, Any]
    result: ToolResult[Any]
    duration_ms: int
    iteration: int


@dataclass(frozen=True, slots=True)
class FallbackEvent:
    """One model switch: failure-driven, or the sticky retry-main return.

    `sticky=True` means the switch was driven by session sticky state
    (returning to the main model), not by a failure — `cause_code` and
    `provider_status` are None there.
    """

    from_model: str
    to_model: str
    reason: str
    cause_code: str | None
    provider_status: int | None
    sticky: bool
    streamed: bool


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """The finished turn — exactly once per run that yields a response.

    Fires on every terminal that produces an `AgentResponse` (done and
    blocked, blocking and streaming); never when the run raises — the
    caller already has the exception.
    """

    response: AgentResponse
    streamed: bool
    duration_ms: int


@dataclass(frozen=True, slots=True)
class AgentHooks:
    """Observation callbacks; each sync or async, swallowed unless strict."""

    on_turn: Callable[[TurnEvent], Awaitable[None] | None] | None = None
    on_llm_call: Callable[[LlmCallEvent], Awaitable[None] | None] | None = None
    on_tool: Callable[[ToolEvent], Awaitable[None] | None] | None = None
    on_fallback: Callable[[FallbackEvent], Awaitable[None] | None] | None = None
    strict: bool = False


class HookRunner:
    """Internal dispatcher for AgentHooks; the public seam is AgentHooks."""

    __slots__ = ("_hooks",)

    def __init__(self, hooks: AgentHooks | None) -> None:
        self._hooks = hooks

    @property
    def enabled(self) -> bool:
        """False when no callback is registered — callers may skip event
        assembly (timers included) entirely."""
        hooks = self._hooks
        if hooks is None:
            return False
        return any((hooks.on_turn, hooks.on_llm_call, hooks.on_tool, hooks.on_fallback))

    async def _fire[E](
        self, fn: Callable[[E], Awaitable[None] | None], event: E, name: str
    ) -> None:
        try:
            result = fn(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Never BaseException: CancelledError/GeneratorExit propagate.
            if self._hooks is not None and self._hooks.strict:
                raise
            logger.exception("agent hook %s raised; swallowed (strict=False)", name)

    async def turn(self, event: TurnEvent) -> None:
        if self._hooks is not None and self._hooks.on_turn is not None:
            await self._fire(self._hooks.on_turn, event, "on_turn")

    async def llm_call(self, event: LlmCallEvent) -> None:
        if self._hooks is not None and self._hooks.on_llm_call is not None:
            await self._fire(self._hooks.on_llm_call, event, "on_llm_call")

    async def tool(self, event: ToolEvent) -> None:
        if self._hooks is not None and self._hooks.on_tool is not None:
            await self._fire(self._hooks.on_tool, event, "on_tool")

    async def fallback(self, event: FallbackEvent) -> None:
        if self._hooks is not None and self._hooks.on_fallback is not None:
            await self._fire(self._hooks.on_fallback, event, "on_fallback")
