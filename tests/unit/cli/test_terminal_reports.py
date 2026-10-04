"""Real command routing, terminal modes and partial reports stay lossless."""

import io
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from neosian._cli.display import console_for, one_line_warnings
from neosian._cli.main import app
from neosian._cli.render import render_status, run_rendered
from neosian._cli.render_reports import (
    render_configure,
    render_maintenance,
    render_setup,
    render_transfer,
    render_versions,
)
from neosian._cli.status import ClientStatus, Status


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _status() -> dict[str, Any]:
    return asdict(
        Status(
            version="1.1.1",
            home="/home/[blue]/state",
            home_exists=False,
            config_path="/home/config.toml",
            config_exists=True,
            config_error="bad [config]",
            providers=({"name": "openai", "env": "OPENAI_API_KEY", "source": None},),
            scopes={"/user": "user:demo", "/project": "user:demo/proj:demo"},
            scopes_error=None,
            clients=(
                ClientStatus(
                    client="muse-code",
                    label="Muse Code",
                    installed=True,
                    mcp_registered=True,
                    hooks_present=True,
                    level="both",
                    hook_files=("/user/hooks", "/project/hooks"),
                    interpreter="/very-long-directory/[red]/bin/python",
                    interpreter_resolves=False,
                    mcp_shadowed_by="/project/[bold]/shared.json",
                    root="/state",
                ),
            ),
            last_session={
                "agent": "cli",
                "conversation": "session-id",
                "updated_at": "2026-10-03T12:00:00Z",
                "path": "sessions/session-id",
            },
            one_writer=("one writer per root",),
            double_fire=("duplicate hooks",),
            shape="venv",
            upgrade="pip install neosian",
            update_mode="off",
        )
    )


@pytest.mark.parametrize("width", [48, 80, 120])
def test_status_keeps_findings_and_literal_paths(width: int) -> None:
    out = io.StringIO()
    render_status(_status(), Console(file=out, width=width, no_color=True))
    text = out.getvalue()
    compact = "".join(text.split())
    for value in (
        "/home/[blue]/state",
        "/project/[bold]/shared.json",
        "/very-long-directory/[red]/bin/python",
        "/project/sessions/session-id",
        "one writer per root",
        "duplicate hooks",
        "OPENAI_API_KEY",
        "bad [config]",
    ):
        assert "".join(value.split()) in compact
    for heading in (
        "Installation",
        "Providers",
        "Scopes",
        "Clients",
        "Last session",
        "Findings",
    ):
        assert heading in text
    assert "…" not in text
    assert max(len(line) for line in text.splitlines()) <= width


def test_no_color_preserves_layout_and_removes_color() -> None:
    out = Tty()
    console = console_for(out, {"NO_COLOR": "1"})
    assert console.no_color
    render_status(_status(), console)
    assert "Providers" in out.getvalue() and "Findings" in out.getvalue()
    assert "\x1b[38;" not in out.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        ["status", "--help"],
        ["search", "--help"],
        ["audit", "-h"],
        ["memory", "view", "--help"],
        ["setup", "--help"],
        ["configure", "--help"],
        ["import", "--help"],
        ["export", "--help"],
        ["docs", "--help"],
        ["chat", "--help"],
        ["eval", "--help"],
    ],
)
def test_terminal_help_never_becomes_a_json_report(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setattr("neosian._cli.render.rendered", lambda *_: True)
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert "usage:" in result.output.lower()


@pytest.mark.parametrize(
    "argv",
    [
        ["memory", "view", "--root", "ROOT", "/project/doc"],
        ["memory", "view", "/project/doc", "--root", "ROOT"],
    ],
)
def test_interleaved_flags_do_not_render_a_document_as_a_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]
) -> None:
    runner = CliRunner()
    root = str(tmp_path / "store")
    created = runner.invoke(
        app,
        [
            "memory",
            "create",
            "/project/doc",
            "--root",
            root,
            "--content",
            "# Exact [blue] document",
        ],
    )
    assert created.exit_code == 0
    monkeypatch.setattr("neosian._cli.render.rendered", lambda *_: True)
    result = runner.invoke(app, [root if a == "ROOT" else a for a in argv])
    assert result.exit_code == 0
    assert "# Exact [blue] document" in result.output
    assert "memory\n" not in result.output


def test_terminal_versions_and_index_use_the_existing_grammar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    root = str(tmp_path / "store")
    assert (
        runner.invoke(
            app,
            [
                "memory",
                "create",
                "/project/doc",
                "--root",
                root,
                "--content",
                "history-secret",
            ],
        ).exit_code
        == 0
    )
    monkeypatch.setattr("neosian._cli.render.rendered", lambda *_: True)
    index = runner.invoke(app, ["memory", "view", "--root", root, "/"])
    assert index.exit_code == 0 and "/project/doc" in index.output
    versions = runner.invoke(
        app, ["memory", "versions", "--root", root, "/project/doc"]
    )
    assert versions.exit_code == 0 and "Version history" in versions.output
    assert "history-secret" not in versions.output


@pytest.mark.parametrize("code", [0, 1])
def test_partial_reports_execute_once_and_keep_stderr(
    code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    calls = []

    def engine(argv: list[str]) -> int:
        calls.append(argv)
        print(
            json.dumps(
                {
                    "writes": [],
                    "usage": None,
                    "degraded": "provider down" if code else None,
                }
            )
        )
        print("diagnostic [literal]", file=sys.stderr)
        return code

    out = Tty()
    assert (
        run_rendered(
            engine, ["--write"], render_maintenance, out=out, env={}, partial=True
        )
        == code
    )
    assert calls == [["--write", "--json"]]
    assert "nothing to do" in out.getvalue()
    assert capsys.readouterr().err == "diagnostic [literal]\n"


def test_unexpected_text_is_replayed_without_rerunning() -> None:
    calls = []

    def engine(argv: list[str]) -> int:
        calls.append(argv)
        print("usage: example")
        return 0

    out = Tty()
    assert run_rendered(engine, [], render_status, out=out, env={}) == 0
    assert out.getvalue() == "usage: example\n" and len(calls) == 1


def test_setup_partial_results_keep_previews_hints_and_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = io.StringIO()
    mcp = {
        "success": True,
        "config_path": "/p/[red]",
        "apply": "client add [entry]",
        "mcp_shadowed_by": "/project/shared",
        "note": "review once",
    }
    hooks = {
        "success": False,
        "error": "cannot write",
        "hint": "fix permissions",
        "trust_hint": "review hooks",
        "files": [{"content": "literal [blue]"}],
    }
    render_setup(
        {
            "written": False,
            "level": "user",
            "clients": [{"label": "Client", "mcp": mcp, "hooks": hooks}],
        },
        Console(file=out, width=120),
    )
    text = out.getvalue()
    for expected in (
        "would run: client add [entry]",
        "refused: cannot write",
        "review once",
        "review hooks",
        "fix permissions",
        '"content": "literal [blue]"',
    ):
        assert expected in text
    err = capsys.readouterr().err
    assert "re-run with --write" in err and "/project/shared" in err


def test_maintenance_counts_all_token_classes() -> None:
    out = io.StringIO()
    render_maintenance(
        {
            "writes": [{"command": "delete", "path": "/p/[a]", "version": 2}],
            "model": "fake",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_tokens": 4,
                "cache_write_tokens": 8,
            },
            "cost_micro_usd": 1000000,
            "degraded": None,
        },
        Console(file=out, width=100),
    )
    text = out.getvalue()
    assert "15" in text and "$1.00" in text and "/p/[a]" in text


def test_empty_reports_and_transfer_counts() -> None:
    out = io.StringIO()
    console = Console(file=out, width=48)
    render_versions({"path": "/p", "versions": []}, console)
    render_transfer({"verb": "export", "archive": "/a", "units": []}, console)
    render_transfer(
        {
            "verb": "import",
            "archive": "/a",
            "units": [
                {
                    "kind": "scope",
                    "name": "user:demo",
                    "documents": 2,
                    "versions": 3,
                    "redactions": 1,
                },
                {"kind": "conversation", "name": "s1", "turns": 4, "projections": 2},
            ],
        },
        console,
    )
    render_configure({"config_path": "/c", "providers": []}, console)
    text = out.getvalue()
    assert "no history" in text and "nothing to export" in text
    assert "2 documents" in text and "4 turns" in text and "No keys" in text


def test_a_library_warning_is_one_line_while_open(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A degraded reflection at chat's close: the cause, not a traceback."""
    logger = logging.getLogger("neosian._foundation.shared.structured")
    with one_line_warnings():
        try:
            raise RuntimeError("Connection error.")
        except RuntimeError:
            logger.warning("%s failed; degrading", "Reflection", exc_info=True)
    logger.warning("after the close")  # the handler left with the block
    assert capsys.readouterr().err == (
        "neosian: Reflection failed; degrading: RuntimeError: Connection error.\n"
    )
