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
        assert note.startswith("claude-code:") and "--url" in note

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
