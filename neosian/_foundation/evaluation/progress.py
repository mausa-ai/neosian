"""Live progress display (DESIGN §13).

A Rich tree over the suite's axes:

    Media Agent Prompt Comparison
    ├── minimal
    │   ├── gpt-5.1  ●●●●●●●●●✗  2.1s
    │   └── fake     ○○○○○○○○○○
    ├── verbose
    │   └── ...

Indicators: ○ pending · ◐ running · ● passed · ✗ failed.
"""

from dataclasses import dataclass, field

from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.tree import Tree

from neosian._foundation.evaluation.results import (
    CaseStatus,
    ProgressCallback,
    ProgressEvent,
)
from neosian._foundation.evaluation.types import EvalConfig

_INDICATORS: dict[CaseStatus, tuple[str, str]] = {
    CaseStatus.PENDING: ("○", "dim"),
    CaseStatus.RUNNING: ("◐", "yellow bold"),
    CaseStatus.PASSED: ("●", "green"),
    CaseStatus.FAILED: ("✗", "red bold"),
}


@dataclass
class CaseState:
    """Display state of one case cell."""

    name: str
    status: CaseStatus = CaseStatus.PENDING
    latency_ms: float = 0.0


@dataclass
class ModelState:
    """Display state of one model row."""

    model: str
    cases: list[CaseState] = field(default_factory=list)

    @property
    def total_latency_ms(self) -> float:
        """Total latency of completed cases."""
        return sum(c.latency_ms for c in self.cases)

    @property
    def is_complete(self) -> bool:
        """All cases finished."""
        return all(
            c.status in (CaseStatus.PASSED, CaseStatus.FAILED) for c in self.cases
        )


@dataclass
class VariantState:
    """Display state of one variant subtree."""

    variant: str
    models: list[ModelState] = field(default_factory=list)


class EvalProgress:
    """Tree-based live progress for a suite run."""

    def __init__(self, config: EvalConfig) -> None:
        self.config = config
        self.console = Console()
        self._live: Live | None = None
        # The axis-name properties — every config kind carries them, so
        # the tree never needs the concrete type.
        self.variants: list[VariantState] = [
            VariantState(
                variant=variant_name,
                models=[
                    ModelState(
                        model=model_name,
                        cases=[CaseState(name=name) for name in config.case_names],
                    )
                    for model_name in config.model_names
                ],
            )
            for variant_name in config.variant_names
        ]

    def start(self) -> None:
        """Start the live display."""
        self._live = Live(
            self._build_tree(),
            console=self.console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the live display and print the final tree."""
        if self._live:
            self._live.stop()
            self._live = None
            self.console.print(self._build_tree())

    def update(self, event: ProgressEvent) -> None:
        """Apply one progress tick."""
        case = (
            self.variants[event.variant_index]
            .models[event.model_index]
            .cases[event.case_index]
        )
        case.status = event.status
        case.latency_ms = event.latency_ms
        if self._live:
            self._live.update(self._build_tree())

    def _build_tree(self) -> Tree:
        tree = Tree(Text(self.config.name, style="bold"), guide_style="dim")
        for variant_state in self.variants:
            variant_node = tree.add(Text(variant_state.variant, style="cyan"))
            for model_state in variant_state.models:
                dots = Text()
                for case_state in model_state.cases:
                    glyph, style = _INDICATORS[case_state.status]
                    dots.append(glyph, style=style)
                parts: list[Text | str] = [Text(model_state.model), "  ", dots]
                if model_state.is_complete:
                    parts += [
                        "  ",
                        Text(_format_latency(model_state.total_latency_ms), "dim"),
                    ]
                variant_node.add(Text.assemble(*parts))
        return tree


def create_progress_callback(progress: EvalProgress) -> ProgressCallback:
    """Bind run_evaluation's progress stream to an EvalProgress."""
    return progress.update


def _format_latency(latency_ms: float) -> str:
    if latency_ms < 1000:
        return f"{latency_ms:.0f}ms"
    return f"{latency_ms / 1000:.1f}s"
