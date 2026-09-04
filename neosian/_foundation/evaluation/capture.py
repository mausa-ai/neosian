"""Harness-side observation seams (DESIGN §13.5).

`on_tool` is the single capture path; `on_fallback` fails the cell on
the first non-sticky switch; hooks compose — never clobber — and run
strict. Shared by every kind's runner.
"""

import dataclasses
import inspect
import logging
from collections.abc import Awaitable, Callable

from neosian._foundation.agent.hooks import AgentHooks, FallbackEvent, ToolEvent
from neosian._foundation.evaluation.results import ToolCallCapture
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import ClientFactory, ToolName

logger = logging.getLogger(__name__)


class FallbackRecorder:
    """on_fallback hook recording the first failure-driven model switch.

    An eval measures the configured model; a cell answered by its
    fallback is a failure, not a pass. Sticky retry-main transitions are
    not failures and are ignored.
    """

    def __init__(self) -> None:
        self.event: FallbackEvent | None = None

    def __call__(self, event: FallbackEvent) -> None:
        if not event.sticky and self.event is None:
            self.event = event

    @property
    def reason(self) -> str | None:
        """Human-readable failure summary for CaseResult.error."""
        if self.event is None:
            return None
        return (
            f"Falling back from {self.event.from_model} to "
            f"{self.event.to_model}: {self.event.reason}"
        )


class ToolCapture:
    """on_tool sink — one capture path for stubbed and real tools."""

    def __init__(self, stubbed: frozenset[ToolName]) -> None:
        self._stubbed = stubbed
        self.calls: list[ToolCallCapture] = []

    def enter_turn(self) -> None:
        self.calls = []

    def __call__(self, event: ToolEvent) -> None:
        self.calls.append(
            ToolCallCapture(
                name=event.name,
                arguments=dict(event.arguments),
                executed=event.name not in self._stubbed,
                ok=event.result.success,
                duration_ms=event.duration_ms,
            )
        )


def compose_hooks(
    user: AgentHooks | None, capture: ToolCapture, recorder: FallbackRecorder
) -> AgentHooks:
    """Compose — never clobber — the caller's hooks with the harness's.

    The harness's callback runs first (it cannot fail), then the
    caller's. Always strict: a raising caller hook fails the cell
    instead of being silently swallowed mid-measurement (§13.5).
    """
    if user is None:
        return AgentHooks(on_tool=capture, on_fallback=recorder, strict=True)
    return dataclasses.replace(
        user,
        on_tool=_compose(capture, user.on_tool),
        on_fallback=_compose(recorder, user.on_fallback),
        strict=True,
    )


def _compose[E](
    first: Callable[[E], None],
    second: Callable[[E], Awaitable[None] | None] | None,
) -> Callable[[E], Awaitable[None] | None]:
    if second is None:
        return first

    async def composed(event: E) -> None:
        first(event)
        result = second(event)
        if inspect.isawaitable(result):
            await result

    return composed


def scripted_factory(
    script: tuple[FakeTurn, ...] | None, label: str
) -> ClientFactory | None:
    """Build a scripted-FakeClient factory for a `script:` block.

    One FakeClient instance serves the whole block, so the script
    position survives across its LLM calls. Works with any configured
    model — the run is keyless and makes no API calls.
    """
    if script is None:
        return None
    logger.info("%s runs scripted via FakeClient — no API calls are made", label)
    fake = FakeClient(FakeScript(turns=script))
    return lambda _model: fake
