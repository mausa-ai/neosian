"""One case, one cell (DESIGN §13.5).

The caller's AgentConfig is never written: the eval agent rides a
`dataclasses.replace` derivation — model, variant prompt, harness-built
tools, composed hooks — constructed once and never touched after. The
`on_tool` hook is the single capture path; `on_fallback` fails the case
on the first non-sticky switch. Hooks run strict: an eval harness that
swallows its own observer's failure measures nothing.
"""

import dataclasses
import inspect
import logging
import time
from collections.abc import Awaitable, Callable

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.hooks import AgentHooks, FallbackEvent, ToolEvent
from neosian._foundation.evaluation.matcher import match_turn
from neosian._foundation.evaluation.results import (
    CaseResult,
    ToolCallCapture,
    TurnResult,
)
from neosian._foundation.evaluation.stubs import StubResults, build_tools
from neosian._foundation.evaluation.types import EvalCase, Variant
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.llm.fake import FakeClient, FakeScript
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalError,
    EvalRunError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    ClientFactory,
    Model,
    ToolName,
)

logger = logging.getLogger(__name__)


class _FallbackRecorder:
    """on_fallback hook recording the first failure-driven model switch.

    An eval measures the configured model; a case answered by its
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


class _ToolCapture:
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


async def run_case(
    base: AgentConfig,
    variant: Variant,
    model: Model,
    case: EvalCase,
    *,
    suite_execute: frozenset[ToolName] = frozenset(),
    ignore: frozenset[ToolName] = frozenset(),
    stop_on_failure: bool = True,
) -> CaseResult:
    """Run one variant × model × case cell against a loaded config.

    Expectation misses return a failed CaseResult; harness-level
    failures raise typed errors.

    Raises:
        EvalCaseInvalidError: If the case or variant names unknown tools.
        EvalRunError: Wrapping any unexpected failure (agent
            construction, exhausted script) with the cell's context.
    """
    try:
        return await _run(
            base,
            variant,
            model,
            case,
            suite_execute=suite_execute,
            ignore=ignore,
            stop_on_failure=stop_on_failure,
        )
    except EvalError:
        raise
    except Exception as e:
        raise EvalRunError(variant.name, model.value, case.name, str(e)) from e


async def _run(
    base: AgentConfig,
    variant: Variant,
    model: Model,
    case: EvalCase,
    *,
    suite_execute: frozenset[ToolName],
    ignore: frozenset[ToolName],
    stop_on_failure: bool,
) -> CaseResult:
    recorder = _FallbackRecorder()
    stub_results = StubResults()
    try:
        tools, stubbed = build_tools(
            base.tools,
            execute=suite_execute | case.execute_tools,
            descriptions=variant.tool_descriptions,
            results=stub_results,
        )
    except ValueError as e:
        raise EvalCaseInvalidError(case.name, str(e)) from e
    capture = _ToolCapture(stubbed)

    derived = dataclasses.replace(
        base,
        model=model,
        system_prompt=(
            variant.system_prompt
            if variant.system_prompt is not None
            else base.system_prompt
        ),
        tools=tools,
        hooks=_compose_hooks(base.hooks, capture, recorder),
        # An explicit script wins over a caller-provided factory.
        client_factory=_scripted_client_factory(case) or base.client_factory,
    )
    agent = Agent(config=derived)

    messages: list[Message] = []
    turn_results: list[TurnResult] = []
    latency_ms = 0.0
    for index, turn in enumerate(case.turns):
        stub_results.enter_turn(turn.tool_results)
        capture.enter_turn()
        messages.append(Message(role=Role.USER, content=turn.user))
        start = time.perf_counter()
        response = await agent.run(messages, stream=False)
        latency_ms += (time.perf_counter() - start) * 1000

        if recorder.event is not None:
            return CaseResult(
                case=case.name,
                variant=variant.name,
                model=model.value,
                passed=False,
                turns=tuple(turn_results),
                latency_ms=latency_ms,
                error=recorder.reason,
            )

        text = text_of(response.message) or None
        passed, failures = match_turn(turn.expect, capture.calls, text, ignore=ignore)
        turn_results.append(
            TurnResult(
                index=index,
                passed=passed,
                expectation=turn.expect,
                tool_calls=tuple(capture.calls),
                response=text,
                failures=failures,
            )
        )
        if not passed and stop_on_failure:
            break
        # Recorded history is what the model saw — extended verbatim
        # (DESIGN §3: input + turn_messages replays as valid history).
        messages.extend(response.turn_messages)

    return CaseResult(
        case=case.name,
        variant=variant.name,
        model=model.value,
        passed=all(t.passed for t in turn_results),
        turns=tuple(turn_results),
        latency_ms=latency_ms,
    )


def _compose_hooks(
    user: AgentHooks | None, capture: _ToolCapture, recorder: _FallbackRecorder
) -> AgentHooks:
    """Compose — never clobber — the caller's hooks with the harness's.

    The harness's callback runs first (it cannot fail), then the
    caller's. Always strict: a raising caller hook fails the case
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


def _scripted_client_factory(case: EvalCase) -> ClientFactory | None:
    """Build a scripted-FakeClient factory for a `script:` case.

    One FakeClient instance serves the whole case, so the script position
    survives across the case's LLM calls. Works with any configured model
    — the run is keyless and makes no API calls.
    """
    if case.script is None:
        return None
    logger.info(
        "Case %s runs scripted via FakeClient — no API calls are made", case.name
    )
    fake = FakeClient(FakeScript(turns=case.script))
    return lambda _provider: fake
