"""A lane's board under an in-loop ceiling (DESIGN §31.4, ledger #211).

pytest-timeout's signal never ends a paced async test — dispatch #14's
Kimi lane idled to the job's 180-minute cap with nothing recorded. The
ceiling now lives in the loop: a board runs its cells one `run_evaluation`
at a time under one `asyncio.timeout(budget)`, so a cut run keeps every
finished cell whole — its failure lines, its store root — and lands the
started cell and the rest as `timeout` reds in the same report. Recorded,
never masked; `conftest.py`'s 3600 s mark stays the outer guard. The
scriptless derivation and the board's assertion live here too, shared by
the external test and its keyless pin.
"""

import asyncio
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from neosian import AnyModel, ClientFactory
from neosian._foundation.evaluation.matcher import clip
from neosian.evaluation import (
    CaseResult,
    EvalReport,
    MemoryEvalConfig,
    Transport,
    load_eval_config,
    run_evaluation,
)
from tests.external.lanes import Lane

Split = tuple[Transport, ...] | None


def scriptless(
    pack: Path, model: AnyModel, transports: Split = None
) -> MemoryEvalConfig:
    """The shipped pack with scripts stripped and one real model on the
    axis — derived in code so the scenario content never forks
    (ledger #68); `transports` narrows the axis for a split board."""
    config = load_eval_config(pack)
    assert isinstance(config, MemoryEvalConfig)
    scenarios = tuple(
        dataclasses.replace(
            scenario,
            sessions=tuple(
                dataclasses.replace(session, script=None)
                for session in scenario.sessions
            ),
        )
        for scenario in config.scenarios
    )
    return dataclasses.replace(
        config,
        agent=str(pack.parents[1] / config.agent),
        models=(model,),
        scenarios=scenarios,
        transports=transports if transports is not None else config.transports,
    )


def boards(
    lanes: tuple[Lane, ...], transports: tuple[Transport, ...]
) -> list[tuple[Lane, Split]]:
    """One board per lane — or one per transport when the lane splits."""
    out: list[tuple[Lane, Split]] = []
    for lane in lanes:
        if lane.split_transports:
            out.extend((lane, (transport,)) for transport in transports)
        else:
            out.append((lane, None))
    return out


def board_id(lane: Lane, split: Split) -> str:
    return lane.name if split is None else f"{lane.name}-{split[0].value}"


@dataclass
class Board:
    """The cells of one scriptless run, finished or cut."""

    config: MemoryEvalConfig
    finished: list[CaseResult] = field(default_factory=list)

    async def run(
        self,
        budget_seconds: float,
        *,
        store_root: Path,
        client_factory: ClientFactory | None = None,
    ) -> EvalReport:
        (model,) = self.config.models
        cells = [(t, s) for t in self.config.transports for s in self.config.scenarios]
        try:
            async with asyncio.timeout(budget_seconds):
                for transport, scenario in cells:
                    one = dataclasses.replace(
                        self.config, transports=(transport,), scenarios=(scenario,)
                    )
                    report = await run_evaluation(
                        one, store_root=store_root, client_factory=client_factory
                    )
                    self.finished.extend(report.results)
        except TimeoutError:
            pass
        cut = [
            CaseResult(
                case=scenario.name,
                variant=transport.value,
                model=model.value,
                passed=False,
                error=f"timeout: cut at {budget_seconds:g} s (ledger #211)",
            )
            for transport, scenario in cells[len(self.finished) :]
        ]
        return EvalReport(
            suite=self.config.name,
            variants=self.config.variant_names,
            models=self.config.model_names,
            cases=self.config.case_names,
            results=(*self.finished, *cut),
        )


def assert_board(report: EvalReport) -> None:
    """The whole board, one line per cell: pytest truncates the assertion's
    repr, and baselines.md transcribes every cell from the CI log. A red
    cell also prints its failure lines — or its harness/timeout error — and
    every live document under its store root: the bytes are gone with the
    runner, so the log is the only place a red can be read (NZ)."""
    for result in report.results:
        verdict = "ok" if result.passed else "RED"
        print(f"cell {result.variant} x {result.case}: {verdict}")
        if result.passed:
            continue
        if result.error is not None:
            print(f"  {result.error}")
        for failure in (f for t in result.turns for f in t.failures):
            print(f"  {failure}")
            if failure.startswith("store root: "):
                _print_store(Path(failure.removeprefix("store root: ")))
    harness_errors = [r.error for r in report.results if r.error is not None]
    assert not harness_errors, harness_errors
    failures = [
        (r.variant, r.case, f)
        for r in report.results
        for t in r.turns
        for f in t.failures
    ]
    assert report.failed == 0, failures


def _print_store(root: Path) -> None:
    # The body only: FileStore's frontmatter block would eat the clip.
    for file in sorted(root.rglob("*.md")):
        text = file.read_text(encoding="utf-8")
        if text.startswith("---\n"):
            text = text.partition("\n---\n")[2]
        print(f"  {file.relative_to(root)}: {clip(text.strip())!r}")
