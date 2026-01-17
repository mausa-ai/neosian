"""Evaluation progress display.

Tree-based live progress UI for evaluation runs.

Display format:
    Media Agent Prompt Comparison (12.3s)
    ├── minimal.yaml
    │   ├── gpt-5.1  ●●●●●●●●●✗  2.1s
    │   ├── haiku    ●●●●●●●✗✗✗  1.8s
    │   └── sonnet   ○○○○○○○○○○
    ├── verbose.yaml
    │   └── ...

Status indicators:
    ○ = pending (dim)
    ◐ = running (yellow)
    ● = passed (green)
    ✗ = failed (red)
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.tree import Tree

from neosian._foundation.shared.types import EvalConfig


@dataclass
class CaseStatus:
    """Status of a single test case."""

    name: str
    status: str = "pending"  # pending, running, passed, failed
    latency_ms: float = 0.0


@dataclass
class ModelStatus:
    """Status of a model's test cases."""

    model: str
    cases: list[CaseStatus] = field(default_factory=list)

    @property
    def total_latency_ms(self) -> float:
        """Total latency of completed cases."""
        return sum(c.latency_ms for c in self.cases)

    @property
    def is_complete(self) -> bool:
        """Check if all cases are done."""
        return all(c.status in ("passed", "failed") for c in self.cases)


@dataclass
class PromptStatus:
    """Status of a prompt config's models."""

    prompt: str
    models: list[ModelStatus] = field(default_factory=list)


class EvalProgress:
    """Manages tree-based progress display for evaluations.

    Displays a hierarchical tree:
    - Eval name (root)
      - Prompt configs (with aggregate status)
        - Models (with aggregate status)
          - Test cases (leaf level with status)
    """

    # Status indicators
    PENDING = "○"
    RUNNING = "◐"
    PASSED = "●"
    FAILED = "✗"

    def __init__(self, config: EvalConfig) -> None:
        """Initialize progress tracker.

        Args:
            config: Evaluation configuration.
        """
        self.config = config
        self.console = Console()
        self._live: Live | None = None

        # Build status tree structure
        self.prompts: list[PromptStatus] = []
        for prompt in config.prompts:
            prompt_status = PromptStatus(prompt=prompt)
            for model in config.models:
                model_status = ModelStatus(model=model)
                for case in config.cases:
                    model_status.cases.append(CaseStatus(name=case.name))
                prompt_status.models.append(model_status)
            self.prompts.append(prompt_status)

    def start(self) -> None:
        """Start the live display."""
        self._live = Live(
            self._build_tree(),
            console=self.console,
            refresh_per_second=4,
            transient=True,  # Clear on each update
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the live display and print final tree."""
        if self._live:
            self._live.stop()
            self._live = None
            # Print final tree state
            self.console.print(self._build_tree())

    def update(
        self,
        prompt_idx: int,
        model_idx: int,
        case_idx: int,
        status: str,
        latency_ms: float = 0.0,
    ) -> None:
        """Update status of a specific case.

        Args:
            prompt_idx: Index of the prompt config.
            model_idx: Index of the model.
            case_idx: Index of the test case.
            status: New status ("running", "passed", "failed").
            latency_ms: Response time in milliseconds.
        """
        case = self.prompts[prompt_idx].models[model_idx].cases[case_idx]
        case.status = status
        case.latency_ms = latency_ms

        if self._live:
            self._live.update(self._build_tree())

    def _build_tree(self) -> Tree:
        """Build the Rich tree for display.

        Compact format with dots inline:
            Media Agent Prompt Comparison
            ├── minimal.yaml
            │   ├── gpt-5.1  ●●●●●●●●●✗
            │   ├── haiku    ●●●●●●●✗✗✗
            │   └── sonnet   ●●●●●●●●✗✗
            ├── verbose.yaml
            │   └── ...
        """
        # Root node with eval name
        tree = Tree(
            Text(self.config.name, style="bold"),
            guide_style="dim",
        )

        for prompt_status in self.prompts:
            # Prompt node: "minimal.yaml"
            prompt_name = Path(prompt_status.prompt).name
            prompt_node = tree.add(Text(prompt_name, style="cyan"))

            for model_status in prompt_status.models:
                # Model node with inline dots: "gpt-5.1  ●●●○○○○○○○  1.2s"
                model_name = self._short_model_name(model_status.model)

                # Build dots string
                dots = Text()
                for case_status in model_status.cases:
                    dots.append_text(self._get_indicator(case_status.status))

                # Add latency if model is complete
                if model_status.is_complete:
                    latency_str = self._format_latency(model_status.total_latency_ms)
                    model_text = Text.assemble(
                        (model_name, ""),
                        "  ",
                        dots,
                        "  ",
                        (latency_str, "dim"),
                    )
                else:
                    model_text = Text.assemble(
                        (model_name, ""),
                        "  ",
                        dots,
                    )
                prompt_node.add(model_text)

        return tree

    def _short_model_name(self, model: str) -> str:
        """Extract short model name from provider:model format."""
        if ":" in model:
            return model.split(":")[-1]
        return model

    def _get_indicator(self, status: str) -> Text:
        """Get styled status indicator."""
        if status == "pending":
            return Text(self.PENDING, style="dim")
        elif status == "running":
            return Text(self.RUNNING, style="yellow bold")
        elif status == "passed":
            return Text(self.PASSED, style="green")
        elif status == "failed":
            return Text(self.FAILED, style="red bold")
        return Text(self.PENDING, style="dim")

    def _format_latency(self, latency_ms: float) -> str:
        """Format latency for display."""
        if latency_ms < 1000:
            return f"{latency_ms:.0f}ms"
        return f"{latency_ms / 1000:.1f}s"


def create_progress_callback(
    progress: EvalProgress,
) -> Callable[[int, int, int, str, float], None]:
    """Create a progress callback bound to an EvalProgress instance.

    Args:
        progress: The EvalProgress instance to update.

    Returns:
        Callback function for run_evaluation.
    """

    def callback(
        prompt_idx: int,
        model_idx: int,
        case_idx: int,
        status: str,
        latency_ms: float,
    ) -> None:
        progress.update(prompt_idx, model_idx, case_idx, status, latency_ms)

    return callback