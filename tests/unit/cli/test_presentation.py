"""Presentation changes preserve execution and machine output."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from neosian import AgentConfig, Model
from neosian._cli.chat_cmd import one_shot
from neosian._cli.main import app
from neosian._cli.render_eval import print_report
from neosian._cli.ui import pick, tool_result
from neosian._foundation.evaluation.results import CaseResult, EvalReport, TurnResult
from neosian._foundation.evaluation.types import Expectation
from neosian.fake import FakeClient, FakeScript, FakeTurn


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("json_output", [False, True])
async def test_one_shot_terminal_markdown_or_exact_json(json_output: bool) -> None:
    fake = FakeClient(
        FakeScript(turns=(FakeTurn(content="**Hello**\n\n- one\n- two"),))
    )
    config = AgentConfig(
        system_prompt="test",
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
    )
    out = _Tty()
    await one_shot(
        config, "hi", conversation_id="rendered", json_output=json_output, out=out
    )
    text = out.getvalue()
    if json_output:
        payload = json.loads(text)
        assert payload["text"] == "**Hello**\n\n- one\n- two"
        assert text.count("\n") == 1
    else:
        assert "Hello" in text and "one" in text and "two" in text
        assert "**Hello**" not in text


@pytest.mark.parametrize("width", [48, 80, 120])
def test_eval_retains_every_case_failure_and_literal_name(width: int) -> None:
    report = EvalReport(
        suite="[blue] suite",
        variants=("base",),
        models=("fake",),
        cases=("ok", "bad"),
        results=(
            CaseResult(case="ok", variant="base", model="fake", passed=True),
            CaseResult(
                case="bad",
                variant="base",
                model="fake",
                passed=False,
                error="error [red] literal",
                turns=(
                    TurnResult(
                        index=0,
                        passed=False,
                        expectation=Expectation(),
                        failures=("missing [value]",),
                    ),
                ),
            ),
        ),
    )
    out = io.StringIO()
    print_report(report, Console(file=out, width=width, no_color=True))
    text = out.getvalue()
    for expected in (
        "[blue] suite",
        "base",
        "fake",
        "ok",
        "bad",
        "passed",
        "failed",
        "error [red] literal",
        "missing [value]",
        "1/2 passed",
    ):
        assert expected in text
    assert max(len(line) for line in text.splitlines()) <= width


def test_picker_and_tool_results_treat_brackets_as_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    out = io.StringIO()
    console = Console(file=out, width=100)
    assert pick(console, "Pick [blue]", ["model [red]"]) == 0
    console.print(tool_result("tool [green]", True, {"key": ["[bold]", 3]}))
    text = out.getvalue()
    for literal in ("Pick [blue]", "model [red]", "tool [green]", "[bold]"):
        assert literal in text


def test_setup_adapter_executes_once_even_on_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from neosian._cli import setup

    calls = []

    def run(argv: list[str], *_: object, **kwargs: Any) -> int:
        calls.append(argv)
        kwargs["out"].write(
            json.dumps(
                {
                    "written": True,
                    "level": "user",
                    "clients": [
                        {
                            "label": "Test client",
                            "mcp": {
                                "success": True,
                                "applied": True,
                                "created": True,
                                "config_path": "/mcp",
                            },
                            "hooks": {
                                "success": False,
                                "error": "refused",
                                "config_path": "/hooks",
                            },
                        }
                    ],
                }
            )
            + "\n"
        )
        kwargs["err"].write("engine diagnostic\n")
        return 1

    monkeypatch.setattr(setup, "run_setup", run)
    monkeypatch.setattr("neosian._cli.render.rendered", lambda *_: True)
    result = CliRunner().invoke(app, ["setup", "--write", "--yes"])
    assert result.exit_code == 1
    assert calls == [["--write", "--yes", "--json"]]
    assert "Setup results" in result.stdout and "refused" in result.stdout
    assert result.stderr == "engine diagnostic\n"


def test_configure_and_docs_indexes_render_only_for_humans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    plain = runner.invoke(app, ["configure", "--list"])
    monkeypatch.setattr("neosian._cli.render.rendered", lambda *_: True)
    listing = runner.invoke(app, ["configure", "--list"])
    assert listing.exit_code == 0 and "Providers" in listing.stdout
    assert listing.stdout != plain.stdout
    payload = runner.invoke(app, ["configure", "--list", "--json"])
    assert payload.exit_code == 0 and "providers" in json.loads(payload.stdout)
    docs = runner.invoke(app, ["docs"])
    assert docs.exit_code == 0 and "Documentation" in docs.stdout
    assert docs.stderr == "hint: neosian docs <topic> prints a page\n"


def test_transfer_rendering_keeps_archive_counts_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    root = str(tmp_path / "store")
    assert (
        runner.invoke(
            app,
            ["memory", "create", "/project/doc", "--root", root, "--content", "hello"],
        ).exit_code
        == 0
    )
    monkeypatch.setattr("neosian._cli.render.rendered", lambda *_: True)
    archive = str(tmp_path / "archive")
    result = runner.invoke(app, ["export", archive, "--root", root])
    assert result.exit_code == 0 and "1 document" in result.stdout
    restored = runner.invoke(
        app, ["import", archive, "--root", str(tmp_path / "restored"), "--json"]
    )
    assert restored.exit_code == 0
    assert json.loads(restored.stdout)["units"][0]["documents"] == 1
