"""What one run spawns and must not outlive it (DESIGN §3).

Three lifetimes, one rule — nothing a run started survives its end:

- the input-guard task (`GuardWatch`; AG-2, AG-3): it races the agent in
  its own task, hands a finished verdict out exactly once, and is reaped
  when the run ends — an agent error, a cancellation, a consumer that
  stopped iterating;
- the tool task a heartbeat generator shields (`reap`; AG-1);
- every nested stream (`closing`; AG-14): a consumer's `aclose()` reaches
  the provider stream and the tool batch synchronously, never on the
  garbage collector's schedule.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from neosian._foundation.agent.guards import (
    await_guard_result_safe,
    check_guardrails,
    extract_user_content,
)
from neosian._foundation.shared.types import GuardrailMode, PolicyResult

if TYPE_CHECKING:
    from neosian._foundation.agent.context import RunContext
    from neosian._foundation.llm.base import Message

logger = logging.getLogger(__name__)

GuardVerdict = tuple[bool, PolicyResult | None]


@asynccontextmanager
async def closing[T](stream: AsyncIterator[T]) -> AsyncIterator[AsyncIterator[T]]:
    """`contextlib.aclosing` for a stream the ABC types as an iterator.

    Every provider stream and every orchestration stream is an async
    generator in practice; closing it with its consumer keeps cleanup
    synchronous. A plain iterator has nothing to close.
    """
    try:
        yield stream
    finally:
        if isinstance(stream, AsyncGenerator):
            await stream.aclose()


async def reap(task: asyncio.Task[Any]) -> None:
    """Cancel `task` if still pending and wait it out.

    Its own outcome is consumed here — the run it served is over — while
    a cancellation aimed at the *current* task still propagates, so a
    reap inside a cancellation handler never swallows the cancel.
    """
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
    except Exception:
        logger.debug("reaped %r after its run ended", task, exc_info=True)


class GuardWatch:
    """The unconsumed input-guard task of one run, if any."""

    __slots__ = ("_ctx", "_task")

    def __init__(self, ctx: RunContext, task: asyncio.Task[GuardVerdict] | None):
        self._ctx = ctx
        self._task = task

    @classmethod
    def start(cls, ctx: RunContext, messages: list[Message]) -> GuardWatch:
        """Start the input guard for `messages` — or watch nothing when no
        input guard is configured or the last user message has no text."""
        agent = ctx.agent
        task: asyncio.Task[GuardVerdict] | None = None
        if (
            agent._guardrails is not None
            and agent._guardrails.input_mode != GuardrailMode.NONE
        ):
            content = extract_user_content(messages)
            if content:
                task = asyncio.create_task(check_guardrails(ctx, content, "input"))
        return cls(ctx, task)

    @property
    def task(self) -> asyncio.Task[GuardVerdict] | None:
        """The unconsumed task — for a caller racing it against its own."""
        return self._task

    def poll(self) -> asyncio.Task[GuardVerdict] | None:
        """A finished, unconsumed verdict task — handed out once, then gone."""
        task = self._task
        if task is None or not task.done():
            return None
        self._task = None
        return task

    async def verdict(self) -> GuardVerdict:
        """Await the verdict under the error policy; safe once consumed or
        when nothing is watched. A cancelled await leaves the task for
        `close`."""
        task = self._task
        if task is None:
            return (True, None)
        result = await await_guard_result_safe(self._ctx.agent, task)
        self._task = None
        return result

    async def close(self) -> None:
        """Reap the unconsumed task, if any."""
        task, self._task = self._task, None
        if task is not None:
            await reap(task)

    async def __aenter__(self) -> GuardWatch:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
