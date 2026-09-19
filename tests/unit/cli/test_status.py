"""`neosian status` (DESIGN §30) over an injected Environment: every
finding is data at exit 0, nothing under the home is created, and the
moved-venv failure is named."""

import io
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from neosian._cli.config import set_api_key, set_value
from neosian._cli.shape import (
    CONTAINER,
    PIPX,
    PROJECT,
    UNKNOWN,
    UV_TOOL,
    Shape,
    detect_shape,
)
from neosian._cli.status import collect, run
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import project_mounts
from neosian._foundation.record.span import sessions_document, sessions_path
from neosian._foundation.shared.client_config import Environment
from tests.unit.record.payloads import SESSION


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"NEOSIAN_HOME": str(tmp_path / "home"), **extra}


def _context(tmp_path: Path, env: Mapping[str, str] | None = None) -> Environment:
    project = tmp_path / "demo proj"
    project.mkdir(exist_ok=True)
    return Environment(
        home=tmp_path,
        cwd=project,
        platform="darwin",
        env={} if env is None else env,
        executable="/venv/bin/python",
    )


class TestShape:
    @pytest.mark.parametrize(
        ("marker", "kind"),
        [
            ("uv-receipt.toml", UV_TOOL),
            ("pipx_metadata.json", PIPX),
            ("pyvenv.cfg", PROJECT),
        ],
    )
    def test_the_marker_names_the_shape(
        self, tmp_path: Path, marker: str, kind: str
    ) -> None:
        (tmp_path / marker).write_text("")
        assert detect_shape(tmp_path, {}).kind == kind

    def test_the_container_marker_wins_and_nothing_is_unknown(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "uv-receipt.toml").write_text("")
        assert detect_shape(tmp_path, {"NEOSIAN_INSTALL": "container"}).kind == (
            CONTAINER
        )
        assert detect_shape(tmp_path / "nowhere", {}).kind == UNKNOWN

    def test_the_upgrade_line_per_shape(self) -> None:
        assert Shape(UV_TOOL).upgrade_line("1.2.3") == "uv tool install neosian==1.2.3"
        assert Shape(CONTAINER).upgrade_line("1.2.3").endswith("neosian:1.2.3")
        assert Shape(PROJECT).upgrade_line("1.2.3") == "uv add neosian==1.2.3"
        assert "pip install" in Shape(UNKNOWN).upgrade_line("1.2.3")


class TestCollect:
    async def test_a_fresh_machine(self, tmp_path: Path) -> None:
        status = await collect(_context(tmp_path), _env(tmp_path))
        assert status.home == str(tmp_path / "home") and not status.home_exists
        assert not status.config_exists
        assert all(p["source"] is None for p in status.providers)
        assert status.scopes is not None
        assert status.scopes["/project"].endswith("/proj:demo-proj")
        assert [c.installed for c in status.clients] == [False, False, False]
        assert status.last_session is None and status.one_writer == ()
        assert status.update_mode == "off"
        assert not (tmp_path / "home").exists()  # status creates nothing

    async def test_keys_by_source_never_by_value(self, tmp_path: Path) -> None:
        set_api_key("openai_api_key", "sk-file")
        status = await collect(
            _context(tmp_path), _env(tmp_path, XAI_API_KEY="xai-env")
        )
        by_name = {p["name"]: p["source"] for p in status.providers}
        assert by_name["openai"] == "file" and by_name["xai"] == "env"
        assert "sk-file" not in json.dumps([dict(p) for p in status.providers])

    async def test_a_registered_client_with_a_moved_interpreter(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (tmp_path / ".claude").mkdir()
        gone = str(tmp_path / "old-venv" / "bin" / "python")
        (context.cwd / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "neosian-memory": {
                            "command": gone,
                            "args": ["-m", "neosian.mcp", "--root", "/r"],
                        }
                    }
                }
            )
        )
        status = await collect(context, _env(tmp_path))
        claude = status.clients[0]
        assert claude.installed and claude.mcp_registered and not claude.hooks_present
        assert claude.interpreter == gone and claude.interpreter_resolves is False
        assert claude.level == "project"
        assert status.one_writer == ()  # hooks absent: one writer already

    async def test_hooks_and_a_registration_on_one_root_get_the_note(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (tmp_path / ".claude").mkdir()
        python = tmp_path / "python"
        python.write_text("")
        (context.cwd / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "neosian-memory": {
                            "command": str(python),
                            "args": ["-m", "neosian.mcp", "--root", "/r"],
                        }
                    }
                }
            )
        )
        (context.cwd / ".claude").mkdir()
        (context.cwd / ".claude" / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"{python} -m neosian.record "
                                        "--root /r --spool /s",
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
        )
        status = await collect(context, _env(tmp_path))
        claude = status.clients[0]
        assert claude.hooks_present and claude.interpreter_resolves is True
        assert claude.root == "/r"
        (note,) = status.one_writer
        assert note.startswith("claude-code:")
        assert "neosian setup --url URL --write" in note  # the fix is one command
        assert status.double_fire == ()  # one level, however many writers

    @staticmethod
    def _hooks(python: str, anchor: str = "") -> str:
        line = f"{python} -m neosian.record --root /r --spool /s{anchor}"
        return json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": line}]}]}}
        )

    async def test_a_machine_registered_once_is_green_in_any_directory(
        self, tmp_path: Path
    ) -> None:
        # §22.6: the user level. Claude Code's user scope is the top-level
        # `mcpServers` of `~/.claude.json`; a project's own table under
        # `projects` is its local scope, not ours to read.
        (tmp_path / ".claude").mkdir()
        python = tmp_path / "python"
        python.write_text("")
        ours = {"command": str(python), "args": ["-m", "neosian.mcp", "--root", "/r"]}
        state = {
            "mcpServers": {"neosian-memory": ours},
            "projects": {"/elsewhere": {"mcpServers": {"neosian-memory": {}}}},
        }
        (tmp_path / ".claude.json").write_text(json.dumps(state))
        (tmp_path / ".claude" / "settings.json").write_text(
            self._hooks(str(python), ' --project "$CLAUDE_PROJECT_DIR"')
        )
        for name in ("one", "two"):  # no file in either directory
            directory = tmp_path / name
            directory.mkdir()
            context = Environment(
                home=tmp_path,
                cwd=directory,
                platform="darwin",
                env={},
                executable="/venv/bin/python",
            )
            claude = (await collect(context, _env(tmp_path))).clients[0]
            assert claude.mcp_registered and claude.hooks_present
            assert claude.level == "user" and claude.interpreter_resolves is True
            assert list(directory.iterdir()) == []

    async def test_hooks_at_both_levels_are_a_finding(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (tmp_path / ".claude").mkdir()
        (context.cwd / ".claude").mkdir()
        user = tmp_path / ".claude" / "settings.json"
        project = context.cwd / ".claude" / "settings.json"
        for file in (user, project):
            file.write_text(self._hooks("/py"))
        status = await collect(context, _env(tmp_path))
        assert status.clients[0].level == "both"
        (note,) = status.double_fire
        assert str(user) in note and str(project) in note
        assert "every span lands twice" in note and "neosian setup --write" in note

    async def test_run_from_the_clients_home_the_levels_are_one_file(
        self, tmp_path: Path
    ) -> None:
        # `claude` started in `~`: `./.claude/settings.json` IS the user file.
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(self._hooks("/py"))
        context = Environment(
            home=tmp_path, cwd=tmp_path, platform="darwin", env={}, executable="/py"
        )
        status = await collect(context, _env(tmp_path))
        assert status.clients[0].level == "user" and status.double_fire == ()

    async def test_the_opencode_plugin_counts_as_hooks(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        plugin = context.cwd / ".opencode" / "plugins" / "neosian-record.js"
        plugin.parent.mkdir(parents=True)
        plugin.write_text(
            'const argv = ["/nowhere/python", "-m", "neosian.record", "--spool", "/s"];'
        )
        status = await collect(context, _env(tmp_path))
        opencode = status.clients[2]
        assert opencode.installed and opencode.hooks_present
        assert opencode.interpreter_resolves is False

    async def test_the_last_session_from_the_project_mount(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        store = FileStore(tmp_path / "home")
        scope = project_mounts(context.cwd)[1].scope
        from datetime import UTC, datetime

        body = sessions_document(
            agent="claude-code",
            session_id=SESSION,
            started=datetime(2026, 9, 10, tzinfo=UTC),
            last_prompt="hello",
            turns=1,
        )
        await store.write(scope, sessions_path(SESSION), body, actor="claude-code:x")
        status = await collect(context, _env(tmp_path))
        assert status.home_exists
        assert status.last_session is not None
        assert status.last_session["conversation"] == SESSION
        assert status.last_session["agent"] == "claude-code"

    async def test_the_update_knob_is_read(self, tmp_path: Path) -> None:
        set_value("update", "mode", "notify")
        assert (
            await collect(_context(tmp_path), _env(tmp_path))
        ).update_mode == "notify"

    async def test_a_broken_config_is_a_finding(self, tmp_path: Path) -> None:
        """EC-11: exit 0 still; the file is named, the knob reads off and
        the keys fall back to the environment."""
        config = tmp_path / "home" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("[update\n")
        status = await collect(
            _context(tmp_path), _env(tmp_path, OPENAI_API_KEY="sk-test")
        )
        assert status.config_path == str(config)
        assert status.config_error is not None
        assert status.config_error.startswith("not valid TOML")
        assert status.update_mode == "off"
        sources = {p["name"]: p["source"] for p in status.providers}
        assert sources["openai"] == "env" and sources["anthropic"] is None

    async def test_a_nameless_directory_reports_the_reason(
        self, tmp_path: Path
    ) -> None:
        context = Environment(
            home=tmp_path, cwd=Path("/"), platform="darwin", env={}, executable="p"
        )
        status = await collect(context, _env(tmp_path))
        assert status.scopes is None
        assert status.scopes_error is not None and "--scope" in status.scopes_error


class TestRun:
    async def _run(self, tmp_path: Path, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = await run(
            argv, _env(tmp_path), context=_context(tmp_path), out=out, err=err
        )
        return code, out.getvalue(), err.getvalue()

    async def test_text_is_aligned_and_names_the_fix(self, tmp_path: Path) -> None:
        code, out, err = await self._run(tmp_path, [])
        assert code == 0 and err == ""
        assert out.startswith("neosian ")
        assert "keys      none — neosian configure" in out
        assert "  codex       not installed" in out
        assert out.endswith("update    mode off\n")

    async def test_json_is_one_object(self, tmp_path: Path) -> None:
        code, out, _ = await self._run(tmp_path, ["--json"])
        assert code == 0 and out.count("\n") == 1
        payload = json.loads(out)
        assert payload["clients"][0]["client"] == "claude-code"
        assert payload["shape"] in {UV_TOOL, PIPX, CONTAINER, PROJECT, UNKNOWN}

    async def test_a_grammar_miss_exits_2(self, tmp_path: Path) -> None:
        code, out, err = await self._run(tmp_path, ["--nope"])
        assert code == 2 and out == "" and "usage" in err
