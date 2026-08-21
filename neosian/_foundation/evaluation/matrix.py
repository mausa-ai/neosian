"""The suite loop (DESIGN §13.5, §13.12).

The agent module loads once per suite — every cell derives its own
config view from that one object and never writes to it. A cell's
EvalError downgrades to a failed CaseResult (one bad case never kills
the suite); an agent that cannot load kills the run, since nothing
could measure anything.
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from neosian._foundation.agent.loader import load_agent_config
from neosian._foundation.evaluation.memory_runner import run_scenario
from neosian._foundation.evaluation.memory_types import MemoryEvalConfig
from neosian._foundation.evaluation.results import (
    CaseResult,
    CaseStatus,
    EvalReport,
    ProgressCallback,
    ProgressEvent,
)
from neosian._foundation.evaluation.runner import run_case
from neosian._foundation.evaluation.types import EvalCase, EvalConfig
from neosian._foundation.shared.exceptions import EvalError
from neosian._foundation.shared.types import AgentConfig, Model, Provider

logger = logging.getLogger(__name__)

_MEMORY_ROOT_DIR = ".neosian/evals"


async def run_evaluation(
    config: EvalConfig,
    *,
    on_progress: ProgressCallback | None = None,
    store_root: str | Path | None = None,
) -> EvalReport:
    """Run the suite's matrix and report every cell.

    `on_progress` receives RUNNING before each cell and PASSED/FAILED
    (with latency) after it. `store_root` overrides where a memory
    suite lands its per-cell scenario stores (default
    `.neosian/evals/<ts>-memory/`, cwd-relative like `save_report`);
    agent suites ignore it.
    """
    base, _ = load_agent_config(config.agent)
    if isinstance(config, MemoryEvalConfig):
        return await _run_memory_matrix(
            config, base, on_progress=on_progress, store_root=store_root
        )

    results: list[CaseResult] = []
    first = True
    for vi, variant in enumerate(config.variants):
        for mi, model in enumerate(config.models):
            for ci, case in enumerate(config.cases):
                # Throttle real-API runs only, and never before the first
                if not first and _needs_throttle(model, case):
                    await asyncio.sleep(config.throttle_ms / 1000)
                first = False

                if on_progress is not None:
                    on_progress(ProgressEvent(vi, mi, ci, CaseStatus.RUNNING))
                try:
                    result = await run_case(
                        base,
                        variant,
                        model,
                        case,
                        suite_execute=config.execute_tools,
                        ignore=config.ignore_tools,
                        stop_on_failure=config.stop_on_failure,
                    )
                except EvalError as e:
                    logger.warning(
                        "Case %s × %s × %s failed at the harness level: %s",
                        variant.name,
                        model.value,
                        case.name,
                        e,
                    )
                    result = CaseResult(
                        case=case.name,
                        variant=variant.name,
                        model=model.value,
                        passed=False,
                        error=str(e),
                    )
                results.append(result)
                if on_progress is not None:
                    status = CaseStatus.PASSED if result.passed else CaseStatus.FAILED
                    on_progress(ProgressEvent(vi, mi, ci, status, result.latency_ms))

    return _report(config, results)


async def _run_memory_matrix(
    config: MemoryEvalConfig,
    base: AgentConfig,
    *,
    on_progress: ProgressCallback | None,
    store_root: str | Path | None,
) -> EvalReport:
    """The transports × models × scenarios loop (§13.12).

    One store root per run, one subdirectory per cell — inspectable
    after the run, and every red result names it.
    """
    if store_root is not None:
        run_root = Path(store_root)
    else:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_root = Path(_MEMORY_ROOT_DIR) / f"{stamp}-memory"

    results: list[CaseResult] = []
    first = True
    for vi, transport in enumerate(config.transports):
        for mi, model in enumerate(config.models):
            for ci, scenario in enumerate(config.scenarios):
                needs_throttle = (
                    not scenario.is_scripted and model.provider is not Provider.FAKE
                )
                if not first and needs_throttle:
                    await asyncio.sleep(config.throttle_ms / 1000)
                first = False

                if on_progress is not None:
                    on_progress(ProgressEvent(vi, mi, ci, CaseStatus.RUNNING))
                cell_root = (
                    run_root
                    / transport.value
                    / _slug(model.value)
                    / _slug(scenario.name)
                )
                logger.info(
                    "Cell %s × %s × %s stores at %s",
                    transport.value,
                    model.value,
                    scenario.name,
                    cell_root,
                )
                try:
                    result = await run_scenario(
                        base,
                        transport,
                        model,
                        scenario,
                        mounts=config.mounts,
                        store_root=cell_root,
                        suite_execute=config.execute_tools,
                        ignore=config.ignore_tools,
                        stop_on_failure=config.stop_on_failure,
                    )
                except EvalError as e:
                    logger.warning(
                        "Scenario %s × %s × %s failed at the harness level: %s",
                        transport.value,
                        model.value,
                        scenario.name,
                        e,
                    )
                    result = CaseResult(
                        case=scenario.name,
                        variant=transport.value,
                        model=model.value,
                        passed=False,
                        error=str(e),
                    )
                results.append(result)
                if on_progress is not None:
                    status = CaseStatus.PASSED if result.passed else CaseStatus.FAILED
                    on_progress(ProgressEvent(vi, mi, ci, status, result.latency_ms))

    return _report(config, results)


def _report(config: EvalConfig, results: list[CaseResult]) -> EvalReport:
    return EvalReport(
        suite=config.name,
        variants=config.variant_names,
        models=config.model_names,
        cases=config.case_names,
        results=tuple(results),
    )


def _slug(value: str) -> str:
    """One path segment per axis value — `openai/gpt-oss-120b` must not
    nest."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value)


def _needs_throttle(model: Model, case: EvalCase) -> bool:
    """Rate-limit throttling applies only to runs that hit a real API."""
    return case.script is None and model.provider is not Provider.FAKE
