"""The rendered verbs (DESIGN §30.3, #204): a terminal gets markdown, a
table or a tree projected from the verb's own `--json` envelope; a pipe,
`NO_COLOR` or `--json` gets the engine's bytes untouched. The pickers on
`rich.prompt`."""

import io
import json
from typing import Any

from rich.console import Console

from neosian._cli.render import (
    render_audit,
    render_docs,
    render_index,
    render_status,
    rendered,
    run_rendered,
)
from neosian._cli.ui import pick


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, width=100), buffer


class TestTheGate:
    def test_only_a_terminal_without_no_color(self) -> None:
        assert rendered(_Tty(), {}) is True
        assert rendered(_Tty(), {"NO_COLOR": "1"}) is False
        assert rendered(io.StringIO(), {}) is False

    def test_a_pipe_runs_the_engine_on_the_real_streams(self) -> None:
        seen: list[list[str]] = []

        def engine(argv: list[str]) -> int:
            seen.append(argv)
            return 0

        def render(envelope: dict[str, Any], console: Console) -> None:
            del envelope, console
            raise AssertionError("never rendered under a pipe")

        assert run_rendered(engine, ["x"], render, out=io.StringIO(), env={}) == 0
        assert run_rendered(engine, ["x", "--json"], render, out=_Tty(), env={}) == 0
        assert seen == [["x"], ["x", "--json"]]

    def test_a_terminal_renders_the_json_envelope(self) -> None:
        def engine(argv: list[str]) -> int:
            assert argv == ["x", "--json"]
            print(json.dumps({"hello": "world"}))
            return 0

        rendered_with: list[dict[str, Any]] = []

        def render(envelope: dict[str, Any], console: Console) -> None:
            del console
            rendered_with.append(envelope)

        out = _Tty()
        assert run_rendered(engine, ["x"], render, out=out, env={}) == 0
        assert rendered_with == [{"hello": "world"}]

    def test_a_failing_engine_renders_nothing(self) -> None:
        def engine(argv: list[str]) -> int:
            del argv
            print(json.dumps({"error": "boom", "hint": None}))
            return 1

        def render(envelope: dict[str, Any], console: Console) -> None:
            del envelope, console
            raise AssertionError("never rendered on failure")

        out = _Tty()
        assert run_rendered(engine, ["x"], render, out=out, env={}) == 1
        assert out.getvalue() == ""  # the engine's stderr text stands alone


class TestTheProjections:
    def test_docs_is_markdown(self) -> None:
        console, buffer = _console()
        render_docs({"body": "# Title\n\nsome *words*\n"}, console)
        assert "Title" in buffer.getvalue() and "words" in buffer.getvalue()
        assert "# Title" not in buffer.getvalue()  # rendered, not raw

    def test_audit_is_a_table(self) -> None:
        console, buffer = _console()
        render_audit(
            {
                "scope": "user:me",
                "entries": [
                    {
                        "created_at": "2026-09-10T12:00:00Z",
                        "actor": "cli:local",
                        "event": "created",
                        "path": "notes",
                        "version": 1,
                        "redacted": False,
                        "count": None,
                        "conversation_id": None,
                        "turn": None,
                    },
                    {
                        "created_at": "2026-09-10T12:01:00Z",
                        "actor": "claude-code:s1#1",
                        "event": "turn",
                        "path": None,
                        "version": None,
                        "redacted": False,
                        "count": None,
                        "conversation_id": "s1",
                        "turn": 1,
                    },
                ],
            },
            console,
        )
        text = buffer.getvalue()
        assert "when" in text and "/notes v1" in text and "s1 turn 1" in text
        console, buffer = _console()
        render_audit({"scope": "user:me", "entries": []}, console)
        assert "no ledger entries" in buffer.getvalue()

    def test_the_index_is_a_tree(self) -> None:
        console, buffer = _console()
        render_index(
            {
                "success": True,
                "data": "## /user — durable facts\n- /user/prefs\n\n"
                "## /project — project facts\n- /project/sessions/abc",
            },
            console,
        )
        lines = buffer.getvalue().splitlines()
        assert lines[0] == "memory"
        assert any("/user — durable facts" in line for line in lines)
        assert any(line.strip().endswith("/project/sessions/abc") for line in lines)
        assert lines.index(next(line for line in lines if "/user/prefs" in line)) > 1

    def test_status_is_two_tables(self) -> None:
        console, buffer = _console()
        render_status(
            {
                "version": "1.0.0rc3",
                "shape": "uv-tool",
                "upgrade": "uv tool install neosian==<version>",
                "home": "/h",
                "home_exists": True,
                "config_path": "/h/config.toml",
                "config_exists": False,
                "config_error": None,
                "providers": [
                    {"name": "openai", "env": "OPENAI_API_KEY", "source": "env"}
                ],
                "scopes": {"/user": "user:me", "/project": "user:me/proj:p"},
                "scopes_error": None,
                "clients": [
                    {
                        "client": "claude-code",
                        "label": "Claude Code",
                        "installed": True,
                        "mcp_registered": True,
                        "hooks_present": False,
                        "interpreter": "/venv/bin/python",
                        "interpreter_resolves": False,
                        "root": None,
                    }
                ],
                "last_session": None,
                "one_writer": ["claude-code: both write /h"],
                "update_mode": "off",
            },
            console,
        )
        text = buffer.getvalue()
        assert "1.0.0rc3" in text and "openai (env)" in text
        assert "registered" in text and "missing: /venv/bin/python" in text
        assert "note: claude-code" in text


class TestThePicker:
    def test_a_numbered_menu_returns_the_index(self) -> None:
        console = Console(file=io.StringIO(), force_terminal=False)

        def two(*args: object, **kwargs: object) -> str:
            del args, kwargs
            return "2"

        console.input = two  # type: ignore[method-assign]
        assert pick(console, "Pick:", ["a", "b", "c"]) == 1

    def test_eof_cancels(self) -> None:
        console = Console(file=io.StringIO(), force_terminal=False)

        def eof(*args: object, **kwargs: object) -> str:
            del args, kwargs
            raise EOFError

        console.input = eof  # type: ignore[method-assign]
        assert pick(console, "Pick:", ["a", "b"]) is None
