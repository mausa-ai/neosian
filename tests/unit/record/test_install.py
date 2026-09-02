"""`neosian record install` — target, command, merge, tiers (§20.9)."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.settings import StoreSettings
from neosian._foundation.record.install import (
    CLIENT_CHOICES,
    HOOK_EVENTS,
    build_command,
    hook_fragment,
    merge_hooks,
    resolve_target,
    run_install,
)
from neosian._foundation.record.settings import RecordSettings
from neosian._foundation.shared.client_config import Environment

_EXECUTABLE = "/venv/bin/python3"
_ARGV = ["--client", "claude-code", "--root", "m", "--scope", "user:me"]


def _context(tmp_path: Path) -> Environment:
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    home.mkdir(exist_ok=True)
    cwd.mkdir(exist_ok=True)
    return Environment(
        home=home, cwd=cwd, platform="darwin", env={}, executable=_EXECUTABLE
    )


def _settings(
    *,
    mounts: tuple[Mount, ...] = (Mount(scope="user:me", mount_path="memories"),),
    root: Path | None = Path("mem"),
    dsn: str | None = None,
    url: str | None = None,
    agent: str = "claude-code",
    spool: Path = Path("spool"),
) -> RecordSettings:
    store = StoreSettings(
        mounts=mounts, root=root, dsn=dsn, schema="neosian", actor="cli:record", url=url
    )
    return RecordSettings(store=store, mount=mounts[0], agent=agent, spool=spool)


def _run(
    argv: Sequence[str], context: Environment, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_install(argv, env or {}, context=context, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _settings_file(context: Environment) -> Path:
    return context.cwd / ".claude" / "settings.json"


class TestTarget:
    def test_claude_code_is_the_only_row(self, tmp_path: Path) -> None:
        assert CLIENT_CHOICES == ("claude-code",)
        context = _context(tmp_path)
        target = resolve_target("claude-code", context)
        assert target.config_path == _settings_file(context)
        assert target.evidence_dir == context.home / ".claude"


class TestCommand:
    def test_the_module_path_root_mount_and_spool_are_rendered(self) -> None:
        command = build_command(_settings(), executable=_EXECUTABLE)
        parts = command.split()
        assert parts[:3] == [_EXECUTABLE, "-m", "neosian.record"]
        assert Path(parts[parts.index("--root") + 1]).is_absolute()
        assert parts[parts.index("--mount") + 1] == "scope=user:me,path=memories"
        assert Path(parts[parts.index("--spool") + 1]).is_absolute()
        assert "--agent" not in parts  # the default is not spelled out

    def test_a_url_and_a_kind_ride_the_dsn_never(self) -> None:
        command = build_command(
            _settings(root=None, url="http://127.0.0.1:6367", agent="codex"),
            executable=_EXECUTABLE,
        )
        parts = command.split()
        assert parts[parts.index("--url") + 1] == "http://127.0.0.1:6367"
        assert parts[parts.index("--agent") + 1] == "codex" and "--root" not in parts
        dsn = "postgresql://u:secret@db/x"
        assert dsn not in build_command(
            _settings(root=None, dsn=dsn), executable=_EXECUTABLE
        )

    def test_the_line_is_shell_quoted(self) -> None:
        command = build_command(
            _settings(spool=Path("my spool")), executable="/opt/py 3/bin/python"
        )
        assert "'/opt/py 3/bin/python'" in command and "/my spool'" in command

    def test_the_fragment_names_the_three_events(self) -> None:
        fragment = hook_fragment("cmd")
        assert tuple(fragment["hooks"]) == HOOK_EVENTS
        assert fragment["hooks"]["Stop"] == [
            {"hooks": [{"type": "command", "command": "cmd"}]}
        ]


class TestMerge:
    def test_other_keys_events_and_groups_survive_and_ours_is_replaced(
        self, tmp_path: Path
    ) -> None:
        theirs = {"matcher": "Bash", "hooks": [{"type": "command", "command": "lint"}]}
        stale = {
            "hooks": [{"type": "command", "command": "py -m neosian.record --old"}]
        }
        document: dict[str, Any] = {
            "permissions": {"allow": ["Read"]},
            "hooks": {"PostToolUse": [theirs, stale], "PreCompact": [theirs]},
        }
        new = "py -m neosian.record --root /new"
        merged = merge_hooks(document, hook_fragment(new), path=tmp_path / "s.json")
        assert merged["permissions"] == {"allow": ["Read"]}
        assert merged["hooks"]["PreCompact"] == [theirs]
        assert merged["hooks"]["PostToolUse"] == [
            theirs,
            {"hooks": [{"type": "command", "command": new}]},
        ]
        assert set(merged["hooks"]) == {"PreCompact", *HOOK_EVENTS}
        assert document["hooks"]["PostToolUse"] == [theirs, stale]  # never mutated

    def test_a_rerun_is_idempotent(self, tmp_path: Path) -> None:
        command = "py -m neosian.record --root /r --mount scope=user:me,path=memories"
        once = merge_hooks({}, hook_fragment(command), path=tmp_path / "s.json")
        twice = merge_hooks(once, hook_fragment(command), path=tmp_path / "s.json")
        assert twice == once

    @pytest.mark.parametrize(
        "document", [{"hooks": ["not", "an", "object"]}, {"hooks": {"Stop": {"a": 1}}}]
    )
    def test_a_malformed_hooks_key_is_refused(
        self, tmp_path: Path, document: dict[str, object]
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        _settings_file(context).parent.mkdir()
        _settings_file(context).write_text(json.dumps(document))
        code, _, err = _run([*_ARGV, "--write"], context)
        assert code == 1 and "refusing to rewrite" in err
        assert json.loads(_settings_file(context).read_text()) == document


class TestExitTiers:
    def test_missing_client_dir_exits_1_and_creates_nothing(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)  # no home/.claude
        code, out, err = _run([*_ARGV, "--write"], context)
        assert code == 1 and out == ""
        assert "error:" in err and str(context.home / ".claude") in err
        assert not (context.home / ".claude").exists()
        assert not (context.cwd / ".claude").exists()

    def test_print_mode_writes_nothing(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, err = _run(_ARGV, context)
        assert code == 0
        assert not (context.cwd / ".claude").exists()
        fragment = json.loads(out)  # stdout is valid JSON on its own
        assert tuple(fragment["hooks"]) == HOOK_EVENTS
        assert "hint: re-run with --write" in err and "one writer" in err

    def test_write_creates_the_project_file_privately(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run([*_ARGV, "--write"], context)
        assert code == 0
        assert out == f"created {_settings_file(context)}\n"
        written = json.loads(_settings_file(context).read_text())
        command = written["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert command.startswith(f"{_EXECUTABLE} -m neosian.record ")
        if sys.platform != "win32":
            assert _settings_file(context).stat().st_mode & 0o777 == 0o600

    def test_write_updates_an_existing_file(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        _settings_file(context).parent.mkdir()
        _settings_file(context).write_text('{"keep": 1, "hooks": {"Stop": []}}\n')
        code, out, _ = _run([*_ARGV, "--write"], context)
        assert code == 0
        assert out == f"updated {_settings_file(context)}\n"
        written = json.loads(_settings_file(context).read_text())
        assert written["keep"] == 1 and len(written["hooks"]["Stop"]) == 1

    def test_unparseable_json_is_refused_and_untouched(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        _settings_file(context).parent.mkdir()
        _settings_file(context).write_text("{not json")
        code, _, err = _run([*_ARGV, "--write"], context)
        assert code == 1 and "not valid JSON" in err
        assert _settings_file(context).read_text() == "{not json"

    @pytest.mark.parametrize(
        "argv",
        [
            ["--root", "m", "--scope", "user:me"],  # no client
            ["--client", "cursor", "--root", "m", "--scope", "user:me"],
            ["--client", "claude-code"],  # no store
            ["--client", "claude-code", "--root", "m", "--scope", "NOT A SCOPE"],
        ],
    )
    def test_bad_invocations_exit_2(self, tmp_path: Path, argv: list[str]) -> None:
        code, out, _ = _run(argv, _context(tmp_path))
        assert code == 2 and out == ""


class TestRendering:
    def test_json_success_and_failure_envelopes(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        code, out, err = _run([*_ARGV, "--json"], context)  # no ~/.claude
        assert code == 1 and err == ""
        assert json.loads(out)["success"] is False
        (context.home / ".claude").mkdir()
        code, out, err = _run([*_ARGV, "--json"], context)
        assert code == 0 and err == "" and out.count("\n") == 1
        payload = json.loads(out)
        assert payload["success"] is True and payload["written"] is False
        assert tuple(payload["hooks"]) == HOOK_EVENTS
        assert payload["command"].startswith(_EXECUTABLE)

    def test_the_url_is_rendered_and_the_token_hinted(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, err = _run(
            [
                "--client",
                "claude-code",
                "--url",
                "http://127.0.0.1:6367",
                "--scope",
                "user:me",
            ],
            context,
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert code == 0, err
        assert "--url http://127.0.0.1:6367" in out and "abc" not in out
        assert "NEOSIAN_CLIENT_TOKEN" in err and "one writer" not in err
