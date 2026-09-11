"""`neosian eval` — the run tier behind the typer command (DESIGN §30)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from rich.console import Console


def run_eval(config_file: str, *, json_output: bool) -> int:
    """Run one suite; the table on the console or the artifact's document
    as one JSON object; the exit code is the gate (1 when a case failed)."""
    from neosian._cli.providers import load_keys_into_env
    from neosian._foundation.evaluation.reporter import report_dict
    from neosian.evaluation import (
        EvalProgress,
        create_progress_callback,
        load_eval_config,
        print_report,
        run_evaluation,
        save_report,
    )

    # The library reads keys from the environment only; loading them from the
    # CLI config file is the CLI's job, done here before the run.
    load_keys_into_env()
    console = Console(stderr=json_output)
    try:
        config = load_eval_config(config_file)
    except Exception as e:
        if json_output:
            print(json.dumps({"error": f"loading config: {e}", "hint": None}))
        console.print(f"[red]Error loading config: {e}[/red]")
        return 1

    progress = EvalProgress(config) if not json_output else None
    try:
        if progress is not None:
            progress.start()
        report = asyncio.run(
            run_evaluation(
                config,
                on_progress=(
                    create_progress_callback(progress) if progress is not None else None
                ),
            )
        )
        if progress is not None:
            progress.stop()
    except Exception as e:
        if progress is not None:
            progress.stop()
        if json_output:
            print(json.dumps({"error": f"evaluation failed: {e}", "hint": None}))
        console.print(f"[red]Evaluation failed: {e}[/red]")
        return 1

    output_path = save_report(report)
    if json_output:
        print(json.dumps(report_dict(report, now=datetime.now(UTC)), default=str))
        return 1 if report.failed else 0
    console.print()
    print_report(report, console)
    console.print()
    console.print(f"[dim]{output_path}[/dim]")
    console.print(f"{report.passed}/{report.total} passed")
    return 1 if report.failed else 0
