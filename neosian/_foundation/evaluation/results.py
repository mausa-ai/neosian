"""Evaluation result vocabulary (DESIGN §13).

Frozen throughout. `EvalReport` carries its own axes so presentation
never needs the config type — the seam later kinds reuse.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from neosian._foundation.evaluation.types import Expectation
from neosian._foundation.shared.types import ToolName


class CaseStatus(str, Enum):
    """Lifecycle of one case in a run. PENDING exists for displays;
    ProgressEvent only ever carries the other three."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ToolCallCapture:
    """One observed tool call, straight off the `on_tool` hook.

    `executed` is False for a harness stub, True for the real tool;
    `ok` is the ToolResult's success flag either way.
    """

    name: ToolName
    arguments: Mapping[str, Any]
    executed: bool
    ok: bool
    duration_ms: int


@dataclass(frozen=True, slots=True)
class TurnResult:
    """One scored turn. `failures` accumulates every unmet expectation —
    never just the first."""

    index: int
    passed: bool
    expectation: Expectation
    tool_calls: tuple[ToolCallCapture, ...] = ()
    response: str | None = None
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One variant × model × case cell.

    `error` is a harness-level failure (fallback observed, agent load,
    script exhausted) — expectation misses live in the turns.
    """

    case: str
    variant: str
    model: str
    passed: bool
    turns: tuple[TurnResult, ...] = ()
    latency_ms: float = 0.0
    error: str | None = None

    @property
    def tool_sequence(self) -> tuple[ToolName, ...]:
        """Every observed tool call across the case's turns, in order."""
        return tuple(c.name for t in self.turns for c in t.tool_calls)

    @property
    def pass_count(self) -> int:
        """Number of turns that passed."""
        return sum(1 for t in self.turns if t.passed)

    @property
    def total_turns(self) -> int:
        """Number of turns evaluated."""
        return len(self.turns)


@dataclass(frozen=True, slots=True)
class EvalReport:
    """A whole run: the axes it covered and one result per cell."""

    suite: str
    variants: tuple[str, ...]
    models: tuple[str, ...]
    cases: tuple[str, ...]
    results: tuple[CaseResult, ...]

    @property
    def total(self) -> int:
        """Number of cells run."""
        return len(self.results)

    @property
    def passed(self) -> int:
        """Number of passing cells."""
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        """Number of failing cells."""
        return self.total - self.passed

    def result_for(self, variant: str, model: str, case: str) -> CaseResult | None:
        """Look up one cell by its axis values."""
        for r in self.results:
            if r.variant == variant and r.model == model and r.case == case:
                return r
        return None


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One progress tick: RUNNING before a cell, PASSED/FAILED after."""

    variant_index: int
    model_index: int
    case_index: int
    status: CaseStatus
    latency_ms: float = 0.0


type ProgressCallback = Callable[[ProgressEvent], None]
