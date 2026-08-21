"""One case, one cell (DESIGN §13.5).

The caller's AgentConfig is never written: the eval agent rides a
`dataclasses.replace` derivation — model, variant prompt, harness-built
tools, composed hooks — constructed once and never touched after. The
`on_tool` hook is the single capture path; `on_fallback` fails the case
on the first non-sticky switch. Hooks run strict: an eval harness that
swallows its own observer's failure measures nothing.
"""

import dataclasses
import time

from neosian._foundation.agent.base import Agent
from neosian._foundation.evaluation.capture import (
    FallbackRecorder,
    ToolCapture,
    compose_hooks,
    scripted_factory,
)
from neosian._foundation.evaluation.matcher import match_turn
from neosian._foundation.evaluation.results import CaseResult, TurnResult
from neosian._foundation.evaluation.stubs import StubResults, build_tools
from neosian._foundation.evaluation.types import EvalCase, Variant
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalError,
    EvalRunError,
)
from neosian._foundation.shared.types import AgentConfig, Model, ToolName


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
    recorder = FallbackRecorder()
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
    capture = ToolCapture(stubbed)

    derived = dataclasses.replace(
        base,
        model=model,
        system_prompt=(
            variant.system_prompt
            if variant.system_prompt is not None
            else base.system_prompt
        ),
        tools=tools,
        hooks=compose_hooks(base.hooks, capture, recorder),
        # An explicit script wins over a caller-provided factory.
        client_factory=scripted_factory(case.script, f"Case {case.name}")
        or base.client_factory,
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
