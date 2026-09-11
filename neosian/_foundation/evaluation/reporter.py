"""Result presentation (DESIGN §13.8).

Rich tables to the terminal, a schema-2 JSON artifact to
`.neosian/evals/<ts>.json`. Everything renders from EvalReport alone —
presentation never needs the config type. Rich is imported at use (NF,
TP-2): `import neosian.evaluation` never loads it, and the terminal
rendering answers a missing rich with the reinstall hint.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from neosian._foundation.evaluation.results import CaseResult, EvalReport
from neosian._foundation.evaluation.types import Expectation, ValueMatcher

if TYPE_CHECKING:
    from rich.console import Console

ARTIFACT_SCHEMA = 2
_OUTPUT_DIR = ".neosian/evals"
_TITLE = "neosian eval"
_INSTALL_HINT = (
    "The eval terminal rendering needs rich, which the neosian install "
    "carries — reinstall: uv add neosian (or pip install neosian)"
)


def require_rich() -> None:
    """Raise the reinstall hint, not a traceback, when rich is absent."""
    try:
        import rich  # noqa: F401
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc


def print_report(report: EvalReport, console: Console) -> None:
    """Print a full run to the terminal."""
    require_rich()
    from rich.panel import Panel

    console.print()
    console.print(
        Panel(f"[bold]{_TITLE}[/bold]\n[dim]{report.suite}[/dim]", expand=False)
    )
    console.print()
    console.print(
        f"Variants: {len(report.variants)} │ "
        f"Models: {len(report.models)} │ "
        f"Cases: {len(report.cases)} │ "
        f"Total: {report.total} runs │ "
        f"Passed: {report.passed}"
    )
    console.print()

    for model in report.models:
        _print_model_table(model, report, console)
        console.print()

    console.print("[bold]SUMMARY[/bold]")
    console.print()
    _print_summary(report, console)
    console.print()

    failures = [r for r in report.results if not r.passed]
    if failures:
        console.print("[bold red]FAILURES[/bold red]")
        console.print()
        _print_failures(failures, console)
    else:
        console.print("[green]All cases passed![/green]")


def _print_model_table(model: str, report: EvalReport, console: Console) -> None:
    from rich.table import Table

    table = Table(title=f"[bold]{model}[/bold]", expand=True)
    table.add_column("Variant", style="cyan")
    for case in report.cases:
        table.add_column(case, justify="center")
    table.add_column("Score", justify="right")

    for variant in report.variants:
        row = [variant]
        passed_count = 0
        for case in report.cases:
            result = report.result_for(variant, model, case)
            if result is None:
                row.append("-")
                continue
            if result.passed:
                passed_count += 1
            row.append(_cell(result))
        pct = int(passed_count / len(report.cases) * 100) if report.cases else 0
        row.append(f"{passed_count}/{len(report.cases)} {pct}%")
        table.add_row(*row)

    console.print(table)


def _cell(result: CaseResult) -> str:
    mark = "[green]✓[/green]" if result.passed else "[red]✗[/red]"
    if result.total_turns > 1:
        mark = f"{mark} {result.pass_count}/{result.total_turns}"
    return f"{mark}\n[dim]{_format_latency(result.latency_ms)}[/dim]"


def _print_summary(report: EvalReport, console: Console) -> None:
    for model in report.models:
        scores = {
            variant: sum(
                1
                for case in report.cases
                if (r := report.result_for(variant, model, case)) and r.passed
            )
            for variant in report.variants
        }
        best = max(scores.values(), default=0)
        names = [v for v, s in scores.items() if s == best]
        console.print(
            f"  Best on {model}: {', '.join(names)} ({best}/{len(report.cases)})"
        )

    totals = {
        variant: sum(1 for r in report.results if r.variant == variant and r.passed)
        for variant in report.variants
    }
    best = max(totals.values(), default=0)
    names = [v for v, s in totals.items() if s == best]
    possible = len(report.models) * len(report.cases)
    console.print()
    console.print(
        f"  [bold]Best overall[/bold]: {', '.join(names)} ({best}/{possible})"
    )


def _print_failures(failures: list[CaseResult], console: Console) -> None:
    for f in failures:
        console.print(f"  [red]{f.variant} × {f.model} × {f.case}[/red]")
        if f.error:
            console.print(f"    {f.error}")
        for turn in f.turns:
            for failure in turn.failures:
                if f.total_turns > 1:
                    console.print(f"    [turn {turn.index + 1}] {failure}")
                else:
                    console.print(f"    {failure}")
        console.print()


def save_report(report: EvalReport, output_dir: str | None = None) -> Path:
    """Write the schema-2 JSON artifact; returns its path."""
    output_path = Path(output_dir if output_dir is not None else _OUTPUT_DIR)
    output_path.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    filepath = output_path / f"{now.strftime('%Y-%m-%d_%H-%M-%S')}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report_dict(report, now=now), f, indent=2, default=str)
    return filepath


def report_dict(report: EvalReport, *, now: datetime) -> dict[str, Any]:
    """The artifact's document — what `save_report` writes and `neosian
    eval --json` prints."""
    return {
        "schema": ARTIFACT_SCHEMA,
        "timestamp": now.isoformat(),
        "suite": report.suite,
        "axes": {
            "variants": list(report.variants),
            "models": list(report.models),
            "cases": list(report.cases),
        },
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
        },
        "results": [_result_to_dict(r) for r in report.results],
    }


def _result_to_dict(result: CaseResult) -> dict[str, Any]:
    return {
        "case": result.case,
        "variant": result.variant,
        "model": result.model,
        "passed": result.passed,
        "latency_ms": result.latency_ms,
        "tool_sequence": [str(n) for n in result.tool_sequence],
        "error": result.error,
        "turns": [
            {
                "index": t.index,
                "passed": t.passed,
                "expectation": _expectation_to_dict(t.expectation),
                "response": t.response,
                "failures": list(t.failures),
                "tool_calls": [
                    {
                        "name": str(c.name),
                        "arguments": dict(c.arguments),
                        "executed": c.executed,
                        "ok": c.ok,
                        "duration_ms": c.duration_ms,
                    }
                    for c in t.tool_calls
                ],
            }
            for t in result.turns
        ],
    }


def _expectation_to_dict(expectation: Expectation) -> dict[str, Any]:
    return {
        "tool": expectation.tool,
        "params": {name: _matcher_to_dict(m) for name, m in expectation.params.items()},
        "sequence": (
            None
            if expectation.sequence is None
            else [
                {
                    "tool": str(step.tool),
                    "params": {
                        name: _matcher_to_dict(m) for name, m in step.params.items()
                    },
                }
                for step in expectation.sequence
            ]
        ),
        "no_tool": expectation.no_tool,
        "response": [_matcher_to_dict(m) for m in expectation.response],
    }


def _matcher_to_dict(matcher: ValueMatcher) -> dict[str, Any]:
    value = matcher.value
    if hasattr(value, "pattern"):  # compiled regex
        value = value.pattern
    return {"mode": matcher.mode.value, "value": value}


def _format_latency(latency_ms: float) -> str:
    if latency_ms < 1000:
        return f"{latency_ms:.0f}ms"
    return f"{latency_ms / 1000:.1f}s"
