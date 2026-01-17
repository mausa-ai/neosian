"""Evaluation result reporting.

Rich terminal output and JSON file export.
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.types import EvalConfig, EvalResult


def _format_latency(latency_ms: float) -> str:
    """Format latency for display."""
    if latency_ms < 1000:
        return f"{latency_ms:.0f}ms"
    return f"{latency_ms / 1000:.1f}s"


def print_results(
    config: EvalConfig,
    results: list[EvalResult],
    console: Console,
) -> None:
    """Print evaluation results to terminal.

    Args:
        config: Evaluation configuration.
        results: List of evaluation results.
        console: Rich console for output.
    """
    # Header
    console.print()
    console.print(
        Panel(
            f"[bold]{Evaluation.UI.TITLE}[/bold]\n[dim]{config.name}[/dim]",
            expand=False,
        )
    )
    console.print()

    # Stats
    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    console.print(
        f"Prompts: {len(config.prompts)} │ "
        f"Models: {len(config.models)} │ "
        f"Cases: {len(config.cases)} │ "
        f"Total: {total} runs │ "
        f"Passed: {passed_count}"
    )
    console.print()

    # Group results by model
    by_model: dict[str, list[EvalResult]] = defaultdict(list)
    for r in results:
        by_model[r.model].append(r)

    # Print table per model
    for model, model_results in by_model.items():
        _print_model_table(model, model_results, config, console)
        console.print()

    # Summary
    console.print(f"[bold]{Evaluation.UI.SUMMARY}[/bold]")
    console.print()
    _print_summary(config, results, console)
    console.print()

    # Failures
    failures = [r for r in results if not r.passed]
    if failures:
        console.print(f"[bold red]{Evaluation.UI.FAILURES}[/bold red]")
        console.print()
        _print_failures(failures, console)
    else:
        console.print(f"[green]{Evaluation.UI.NO_FAILURES}[/green]")


def _print_model_table(
    model: str,
    results: list[EvalResult],
    config: EvalConfig,
    console: Console,
) -> None:
    """Print results table for a single model."""
    # Extract short model name
    model_short = model.split(":")[-1] if ":" in model else model

    table = Table(title=f"[bold]{model_short}[/bold]", expand=True)

    # Columns: Prompt, then one per case, then Score
    table.add_column("Prompt", style="cyan")
    for case in config.cases:
        table.add_column(case.name, justify="center")
    table.add_column("Score", justify="right")

    # Group results by prompt
    by_prompt: dict[str, dict[str, EvalResult]] = defaultdict(dict)
    for r in results:
        by_prompt[r.prompt_file][r.case_name] = r

    # Add rows
    for prompt_file in config.prompts:
        prompt_short = Path(prompt_file).stem
        row = [prompt_short]

        prompt_results = by_prompt.get(prompt_file, {})
        passed_count = 0
        total_count = 0

        for case in config.cases:
            result = prompt_results.get(case.name)
            if result:
                total_count += 1
                latency_str = _format_latency(result.latency_ms)
                if result.passed:
                    passed_count += 1
                    # Show pass count for conversational
                    if result.total_turns > 1:
                        row.append(
                            f"[green]✓[/green] {result.pass_count}/{result.total_turns}\n[dim]{latency_str}[/dim]"
                        )
                    else:
                        row.append(f"[green]✓[/green]\n[dim]{latency_str}[/dim]")
                else:
                    if result.total_turns > 1:
                        row.append(
                            f"[red]✗[/red] {result.pass_count}/{result.total_turns}\n[dim]{latency_str}[/dim]"
                        )
                    else:
                        row.append(f"[red]✗[/red]\n[dim]{latency_str}[/dim]")
            else:
                row.append("-")

        # Score column
        if total_count > 0:
            pct = int(passed_count / total_count * 100)
            row.append(f"{passed_count}/{total_count} {pct}%")
        else:
            row.append("-")

        table.add_row(*row)

    console.print(table)


def _print_summary(
    config: EvalConfig,
    results: list[EvalResult],
    console: Console,
) -> None:
    """Print summary of best combinations."""
    # Best per model
    by_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        if r.passed:
            by_model[r.model][r.prompt_file] += 1

    for model, prompt_scores in by_model.items():
        if prompt_scores:
            max_score = max(prompt_scores.values())
            best_prompts = [p for p, s in prompt_scores.items() if s == max_score]
            best_names = [Path(p).stem for p in best_prompts]
            model_short = model.split(":")[-1] if ":" in model else model
            console.print(
                f"  {Evaluation.UI.BEST_ON.format(model=model_short)}: "
                f"{', '.join(best_names)} ({max_score}/{len(config.cases)})"
            )

    # Overall best
    prompt_totals: dict[str, int] = defaultdict(int)
    for r in results:
        if r.passed:
            prompt_totals[r.prompt_file] += 1

    if prompt_totals:
        max_total = max(prompt_totals.values())
        best_overall = [p for p, s in prompt_totals.items() if s == max_total]
        best_names = [Path(p).stem for p in best_overall]
        total_possible = len(config.models) * len(config.cases)
        console.print()
        console.print(
            f"  [bold]{Evaluation.UI.BEST_OVERALL}[/bold]: "
            f"{', '.join(best_names)} ({max_total}/{total_possible})"
        )


def _print_failures(failures: list[EvalResult], console: Console) -> None:
    """Print failure details."""
    for f in failures:
        prompt_short = Path(f.prompt_file).stem
        model_short = f.model.split(":")[-1] if ":" in f.model else f.model

        console.print(f"  [red]{prompt_short} × {model_short} × {f.case_name}[/red]")

        if f.error:
            console.print(f"    {f.error}")

        for turn in f.turns:
            if not turn.passed and turn.error:
                if f.total_turns > 1:
                    console.print(f"    [turn {turn.turn_index + 1}] {turn.error}")
                else:
                    console.print(f"    {turn.error}")

        console.print()


def save_results(
    config: EvalConfig,
    results: list[EvalResult],
    output_dir: str | None = None,
) -> Path:
    """Save results to JSON file.

    Args:
        config: Evaluation configuration.
        results: List of evaluation results.
        output_dir: Output directory (default: .neosian/evals).

    Returns:
        Path to saved file.
    """
    if output_dir is None:
        output_dir = Evaluation.OUTPUT_DIR

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{timestamp}.json"
    filepath = output_path / filename

    data = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "name": config.name,
            "agent": config.agent,
            "prompts": config.prompts,
            "models": config.models,
            "cases": [c.name for c in config.cases],
        },
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
        },
        "results": [_result_to_dict(r) for r in results],
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    return filepath


def _result_to_dict(result: EvalResult) -> dict[str, object]:
    """Convert EvalResult to JSON-serializable dict."""
    return {
        "case_name": result.case_name,
        "prompt_file": result.prompt_file,
        "model": result.model,
        "passed": result.passed,
        "latency_ms": result.latency_ms,
        "tool_sequence": result.tool_sequence,
        "error": result.error,
        "turns": [
            {
                "turn_index": t.turn_index,
                "passed": t.passed,
                "expected_tool": t.expected_tool,
                "actual_tool": t.actual_tool,
                "expected_params": t.expected_params,
                "actual_params": t.actual_params,
                "param_failures": t.param_failures,
                "actual_response": t.actual_response,
                "error": t.error,
            }
            for t in result.turns
        ],
    }
