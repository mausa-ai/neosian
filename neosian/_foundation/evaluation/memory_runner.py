"""One memory scenario, one cell (DESIGN §13.12).

A session is a fresh bare Agent wired through `derive_config` — the
shipped wiring, not a harness replica — over a fresh FileStore on the
cell's one store root. The index section regenerates per session (the
frozen-index rule), so a fact written in session 1 surfaces in session
2's prefix exactly as it would in production. Store truth is checked
after each session through a freshly constructed store.
"""

import dataclasses
import time
from pathlib import Path

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.hooks import TurnEvent
from neosian._foundation.conversation.wiring import derive_config
from neosian._foundation.evaluation.capture import (
    FallbackRecorder,
    ToolCapture,
    compose_hooks,
    scripted_factory,
)
from neosian._foundation.evaluation.matcher import match_turn
from neosian._foundation.evaluation.memory_score import check_store
from neosian._foundation.evaluation.memory_types import (
    MemoryScenario,
    MemorySession,
    Transport,
)
from neosian._foundation.evaluation.results import CaseResult, TurnResult
from neosian._foundation.evaluation.stubs import StubResults, build_tools
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.index import memory_system_section
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalError,
    EvalRunError,
)
from neosian._foundation.shared.types import AgentConfig, Model, ToolName


async def run_scenario(
    base: AgentConfig,
    transport: Transport,
    model: Model,
    scenario: MemoryScenario,
    *,
    mounts: tuple[Mount, ...],
    store_root: Path,
    suite_execute: frozenset[ToolName] = frozenset(),
    ignore: frozenset[ToolName] = frozenset(),
    stop_on_failure: bool = True,
) -> CaseResult:
    """Run one transport × model × scenario cell against a loaded config.

    Expectation and store-truth misses return a failed CaseResult;
    harness-level failures raise typed errors.

    Raises:
        EvalCaseInvalidError: If the suite's `execute_tools` names
            unknown tools.
        EvalRunError: The base config carries `memory=` (the suite owns
            the store and mounts), or any unexpected failure with the
            cell's context.
    """
    try:
        return await _run(
            base,
            transport,
            model,
            scenario,
            mounts=mounts,
            store_root=store_root,
            suite_execute=suite_execute,
            ignore=ignore,
            stop_on_failure=stop_on_failure,
        )
    except EvalError:
        raise
    except Exception as e:
        raise EvalRunError(transport.value, model.value, scenario.name, str(e)) from e


async def _run(
    base: AgentConfig,
    transport: Transport,
    model: Model,
    scenario: MemoryScenario,
    *,
    mounts: tuple[Mount, ...],
    store_root: Path,
    suite_execute: frozenset[ToolName],
    ignore: frozenset[ToolName],
    stop_on_failure: bool,
) -> CaseResult:
    if base.memory is not None:
        raise EvalRunError(
            transport.value,
            model.value,
            scenario.name,
            "the agent config carries memory= — the memory suite owns the "
            "store and mounts; declare them under 'mounts:' and drop memory= "
            "from the agent",
        )
    stub_results = StubResults()
    try:
        tools, stubbed = build_tools(
            base.tools, execute=suite_execute, descriptions={}, results=stub_results
        )
    except ValueError as e:
        raise EvalCaseInvalidError(scenario.name, str(e)) from e

    turn_results: list[TurnResult] = []
    latency_ms = 0.0
    turn_index = 0
    for session in scenario.sessions:
        # A fresh store handle and a freshly rendered index per session —
        # the cross-session seam the scenario measures.
        memory_config = MemoryConfig(store=FileStore(store_root), mounts=mounts)
        section = await memory_system_section(memory_config)
        # native_memory is read by derive_config when it builds the tool,
        # so the transport lands on the base *before* derivation.
        staged = dataclasses.replace(
            base,
            model=model,
            tools=tools,
            native_memory=(transport is Transport.NATIVE),
        )
        capture = ToolCapture(stubbed)
        recorder = FallbackRecorder()
        derived = derive_config(
            staged,
            section=section,
            memory_config=memory_config,
            actor=f"eval:{scenario.name}:{session.name}",
            capture=_noop,
        )
        derived = dataclasses.replace(
            derived,
            hooks=compose_hooks(derived.hooks, capture, recorder),
            # An explicit script wins over a caller-provided factory.
            client_factory=scripted_factory(
                session.script, f"Session {scenario.name}/{session.name}"
            )
            or base.client_factory,
        )
        agent = Agent(config=derived)

        aborted = False
        messages: list[Message] = []
        for turn in session.turns:
            stub_results.enter_turn(turn.tool_results)
            capture.enter_turn()
            messages.append(Message(role=Role.USER, content=turn.user))
            start = time.perf_counter()
            response = await agent.run(messages, stream=False)
            latency_ms += (time.perf_counter() - start) * 1000

            if recorder.event is not None:
                return CaseResult(
                    case=scenario.name,
                    variant=transport.value,
                    model=model.value,
                    passed=False,
                    turns=tuple(turn_results),
                    latency_ms=latency_ms,
                    error=recorder.reason,
                )

            text = text_of(response.message) or None
            passed, failures = match_turn(
                turn.expect, capture.calls, text, ignore=ignore
            )
            turn_results.append(
                TurnResult(
                    index=turn_index,
                    passed=passed,
                    expectation=turn.expect,
                    tool_calls=tuple(capture.calls),
                    response=text,
                    failures=tuple(f"session '{session.name}': {f}" for f in failures),
                )
            )
            turn_index += 1
            if not passed and stop_on_failure:
                aborted = True
                break
            messages.extend(response.turn_messages)

        if aborted:
            break
        clean = await _score_session(session, mounts, store_root, turn_results)
        if not clean and stop_on_failure:
            break

    passed = bool(turn_results) and all(t.passed for t in turn_results)
    if turn_results and not passed:
        # Every red cell points at the directory to inspect.
        last = turn_results[-1]
        turn_results[-1] = dataclasses.replace(
            last, failures=last.failures + (f"store root: {store_root}",)
        )
    return CaseResult(
        case=scenario.name,
        variant=transport.value,
        model=model.value,
        passed=passed,
        turns=tuple(turn_results),
        latency_ms=latency_ms,
    )


async def _score_session(
    session: MemorySession,
    mounts: tuple[Mount, ...],
    store_root: Path,
    turn_results: list[TurnResult],
) -> bool:
    """Fold the session's store truth into its last turn. True = clean."""
    if session.expect_store.is_empty:
        return True
    # A fresh store: a pass means the documents survive re-reading.
    score_config = MemoryConfig(store=FileStore(store_root), mounts=mounts)
    failures = await check_store(score_config, session.expect_store)
    if not failures:
        return True
    last = turn_results[-1]
    turn_results[-1] = dataclasses.replace(
        last,
        passed=False,
        failures=last.failures
        + tuple(f"session '{session.name}': {f}" for f in failures),
    )
    return False


def _noop(_event: TurnEvent) -> None:
    return None
