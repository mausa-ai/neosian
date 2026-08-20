"""The suite loop (DESIGN §13.5).

The agent module loads once per suite — every cell derives its own
config view from that one object and never writes to it. A cell's
EvalError downgrades to a failed CaseResult (one bad case never kills
the suite); an agent that cannot load kills the run, since nothing
could measure anything.
"""

import asyncio
import logging

from neosian._foundation.agent.loader import load_agent_config
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
from neosian._foundation.shared.types import Model, Provider

logger = logging.getLogger(__name__)


async def run_evaluation(
    config: EvalConfig,
    *,
    on_progress: ProgressCallback | None = None,
) -> EvalReport:
    """Run the variants × models × cases matrix and report every cell.

    `on_progress` receives RUNNING before each cell and PASSED/FAILED
    (with latency) after it.
    """
    base, _ = load_agent_config(config.agent)

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

    return EvalReport(
        suite=config.name,
        variants=tuple(v.name for v in config.variants),
        models=tuple(m.value for m in config.models),
        cases=tuple(c.name for c in config.cases),
        results=tuple(results),
    )


def _needs_throttle(model: Model, case: EvalCase) -> bool:
    """Rate-limit throttling applies only to runs that hit a real API."""
    return case.script is None and model.provider is not Provider.FAKE
