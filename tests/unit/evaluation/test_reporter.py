"""Reporting — the schema-2 artifact and the terminal rendering."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from neosian._foundation.evaluation.reporter import print_report, save_report
from neosian._foundation.evaluation.results import (
    CaseResult,
    EvalReport,
    ToolCallCapture,
    TurnResult,
)
from neosian._foundation.evaluation.types import (
    Expectation,
    MatchMode,
    ValueMatcher,
)
from neosian._foundation.shared.types import ToolName


def _report() -> EvalReport:
    expectation = Expectation(
        tool=ToolName("draw"),
        params={
            "prompt": ValueMatcher(mode=MatchMode.EXISTS),
            "quality": ValueMatcher(mode=MatchMode.REGEX, value=re.compile("^hi?$")),
        },
    )
    passing = CaseResult(
        case="good",
        variant="base",
        model="fake",
        passed=True,
        turns=(
            TurnResult(
                index=0,
                passed=True,
                expectation=expectation,
                tool_calls=(
                    ToolCallCapture(
                        name=ToolName("draw"),
                        arguments={"prompt": "sunset", "quality": "hi"},
                        executed=False,
                        ok=True,
                        duration_ms=3,
                    ),
                ),
                response="done",
            ),
        ),
        latency_ms=120.0,
    )
    failing = CaseResult(
        case="bad",
        variant="base",
        model="fake",
        passed=False,
        turns=(
            TurnResult(
                index=0,
                passed=False,
                expectation=Expectation(no_tool=True),
                failures=("expected no tool call, got 'draw'",),
            ),
        ),
        latency_ms=80.0,
    )
    return EvalReport(
        suite="suite",
        variants=("base",),
        models=("fake",),
        cases=("good", "bad"),
        results=(passing, failing),
    )


@pytest.mark.unit
class TestArtifact:
    def test_schema_two_shape(self, tmp_path: Path) -> None:
        path = save_report(_report(), output_dir=str(tmp_path))
        data = json.loads(path.read_text())

        assert data["schema"] == 2
        assert data["suite"] == "suite"
        assert data["axes"] == {
            "variants": ["base"],
            "models": ["fake"],
            "cases": ["good", "bad"],
        }
        assert data["summary"] == {"total": 2, "passed": 1, "failed": 1}

        good = data["results"][0]
        assert good["tool_sequence"] == ["draw"]
        turn = good["turns"][0]
        assert turn["expectation"]["tool"] == "draw"
        assert turn["expectation"]["params"]["prompt"] == {
            "mode": "exists",
            "value": None,
        }
        # Compiled regexes serialize as their pattern text
        assert turn["expectation"]["params"]["quality"] == {
            "mode": "regex",
            "value": "^hi?$",
        }
        capture = turn["tool_calls"][0]
        assert capture["executed"] is False
        assert capture["ok"] is True

        bad = data["results"][1]
        assert bad["turns"][0]["failures"] == ["expected no tool call, got 'draw'"]

    def test_timestamp_is_aware_utc(self, tmp_path: Path) -> None:
        """The artifact's clock is tz-aware UTC, and the filename rides the
        same instant (EC-3)."""
        path = save_report(_report(), output_dir=str(tmp_path))
        stamp = datetime.fromisoformat(json.loads(path.read_text())["timestamp"])

        assert stamp.tzinfo is not None
        assert stamp.utcoffset() == datetime.now(UTC).utcoffset()
        assert path.stem == stamp.strftime("%Y-%m-%d_%H-%M-%S")

    def test_directory_is_created(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "evals"
        path = save_report(_report(), output_dir=str(target))
        assert path.parent == target

    def test_memory_report_stays_schema_two(self, tmp_path: Path) -> None:
        """Transports occupy the variants axis (ledger #64) — the
        artifact shape never learns about kinds."""
        result = CaseResult(case="dedup", variant="function", model="fake", passed=True)
        report = EvalReport(
            suite="mem",
            variants=("function",),
            models=("fake",),
            cases=("dedup",),
            results=(result,),
        )
        data = json.loads(save_report(report, output_dir=str(tmp_path)).read_text())
        assert data["schema"] == 2
        assert data["axes"]["variants"] == ["function"]
        assert data["results"][0]["variant"] == "function"


@pytest.mark.unit
class TestTerminal:
    def test_render_carries_scores_and_failures(self) -> None:
        console = Console(record=True, width=120)
        print_report(_report(), console)
        text = console.export_text()

        assert "neosian eval" in text
        assert "suite" in text
        assert "Passed: 1" in text
        assert "1/2 50%" in text
        assert "FAILURES" in text
        assert "expected no tool call, got 'draw'" in text

    def test_all_passing_render(self) -> None:
        report = _report()
        passing_only = EvalReport(
            suite=report.suite,
            variants=report.variants,
            models=report.models,
            cases=("good",),
            results=(report.results[0],),
        )
        console = Console(record=True, width=120)
        print_report(passing_only, console)
        assert "All cases passed!" in console.export_text()
