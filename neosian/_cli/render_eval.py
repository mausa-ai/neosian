"""CLI evaluation reports, leaving the exported library reporter intact."""

from rich.console import Console
from rich.text import Text

from neosian._cli.display import fields, note, records, section
from neosian._foundation.evaluation.results import EvalReport


def print_report(report: EvalReport, console: Console) -> None:
    section(console, "Evaluation")
    fields(
        console,
        [
            ("Suite", report.suite),
            (
                "Results",
                f"{report.passed}/{report.total} passed; {report.failed} failed",
            ),
        ],
    )
    for model in report.models:
        console.print()
        section(console, model)
        rows = []
        for variant in report.variants:
            for case in report.cases:
                result = report.result_for(variant, model, case)
                rows.append(
                    (
                        variant,
                        case,
                        (
                            "not run"
                            if result is None
                            else "passed" if result.passed else "failed"
                        ),
                        (
                            "-"
                            if result is None
                            else f"{result.pass_count}/{result.total_turns}"
                        ),
                        "-" if result is None else f"{result.latency_ms / 1000:.2f}s",
                    )
                )
        records(console, ("Variant", "Case", "Result", "Turns", "Time"), rows)
    console.print()
    section(console, "Summary")
    for model in report.models:
        scores = {
            v: sum(
                r.passed for r in report.results if r.model == model and r.variant == v
            )
            for v in report.variants
        }
        best = max(scores.values(), default=0)
        names = ", ".join(v for v, score in scores.items() if score == best)
        console.print(Text(f"Best on {model}: {names} ({best}/{len(report.cases)})"))
    totals = {
        v: sum(r.passed for r in report.results if r.variant == v)
        for v in report.variants
    }
    best = max(totals.values(), default=0)
    names = ", ".join(v for v, score in totals.items() if score == best)
    console.print(
        Text(f"Best overall: {names} ({best}/{len(report.models) * len(report.cases)})")
    )
    if not report.failed:
        console.print(Text("All cases passed", style="green"))
        return
    console.print()
    section(console, "Failures")
    for result in report.results:
        if result.passed:
            continue
        console.print(
            Text(f"{result.variant} / {result.model} / {result.case}", style="bold red")
        )
        if result.error:
            note(console, result.error)
        for turn in result.turns:
            for failure in turn.failures:
                note(console, f"Turn {turn.index + 1}: {failure}")
