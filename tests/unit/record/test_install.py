"""`neosian record install` — target, command, merge, tiers (§20.9), and
the level: the client's own settings once per machine by default, this
directory's file on request (§22.6)."""

from __future__ import annotations

import io
import json
import shlex
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from neosian._foundation.memory.home import HOME_ENV
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.settings import StoreSettings
from neosian._foundation.record.install import (
    HOOK_EVENTS,
    build_command,
    hook_fragment,
    merge_hooks,
    run_install,
    strip_hooks,
)
from neosian._foundation.record.settings import RecordSettings
from neosian._foundation.record.targets import CLIENT_CHOICES, resolve_target
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
    mount = mounts[0] if mounts else None
    return RecordSettings(store=store, mount=mount, agent=agent, spool=spool)


def _run(
    argv: Sequence[str], context: Environment, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_install(argv, env or {}, context=context, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _settings_file(context: Environment) -> Path:
    """Claude Code's own settings: where the hooks land once per machine."""
    return context.home / ".claude" / "settings.json"


def _project_file(context: Environment) -> Path:
    return context.cwd / ".claude" / "settings.json"


class TestTarget:
    def test_the_rows_come_from_the_table(self, tmp_path: Path) -> None:
        assert CLIENT_CHOICES == ("claude-code", "codex", "opencode", "muse-code")
        context = _context(tmp_path)
        target = resolve_target("claude-code", context)
        assert target.config_path == _settings_file(context)
        assert target.evidence_dir == context.home / ".claude"
        assert target.trust_hint is None and target.level == "user"
        assert target.project_token == '--project "$CLAUDE_PROJECT_DIR"'
        project = resolve_target("claude-code", context, "project")
        assert project.config_path == _project_file(context)
        assert project.project_token is None and project.level == "project"

    def test_claude_config_dir_moves_the_user_file(self, tmp_path: Path) -> None:
        moved = replace(_context(tmp_path), env={"CLAUDE_CONFIG_DIR": str(tmp_path)})
        target = resolve_target("claude-code", moved)
        assert target.config_path == tmp_path / "settings.json"
        assert target.evidence_dir == tmp_path

    def test_codex_is_its_own_hooks_file_reviewed_once(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        target = resolve_target("codex", context)
        assert target.config_path == context.home / ".codex" / "hooks.json"
        assert target.evidence_dir == context.home / ".codex"
        assert target.trust_hint is not None and "/hooks" in target.trust_hint
        assert target.project_token is None  # Codex runs hooks in the session cwd

    def test_codex_is_the_project_hooks_file_under_trust(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        target = resolve_target("codex", context, "project")
        assert target.config_path == context.cwd / ".codex" / "hooks.json"
        assert target.evidence_dir == context.home / ".codex"
        assert target.trust_hint is not None and "trusted" in target.trust_hint

    def test_codex_home_moves_the_evidence(self, tmp_path: Path) -> None:
        context = Environment(
            home=tmp_path,
            cwd=tmp_path,
            platform="darwin",
            env={"CODEX_HOME": str(tmp_path / "elsewhere")},
            executable=_EXECUTABLE,
        )
        assert resolve_target("codex", context).evidence_dir == tmp_path / "elsewhere"


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

    def test_the_project_token_rides_raw_on_a_line_without_mounts(self) -> None:
        token = '--project "$CLAUDE_PROJECT_DIR"'
        bare = build_command(
            _settings(mounts=()), executable=_EXECUTABLE, project_token=token
        )
        # Raw: `shlex.join` would single-quote the variable and the shell
        # would never expand it.
        assert bare.endswith(f" {token}") and "'$CLAUDE" not in bare
        assert shlex.split(bare)[-2:] == ["--project", "$CLAUDE_PROJECT_DIR"]
        named = build_command(_settings(), executable=_EXECUTABLE, project_token=token)
        assert "--project" not in named  # the mounts were named: nothing to derive

    def test_the_line_is_shell_quoted(self) -> None:
        command = build_command(
            _settings(spool=Path("my spool")), executable="/opt/py 3/bin/python"
        )
        assert "'/opt/py 3/bin/python'" in command and "/my spool'" in command

    def test_the_fragment_names_the_four_events(self) -> None:
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
        assert not _settings_file(context).exists()
        assert not (context.cwd / ".claude").exists()
        fragment = json.loads(out)  # stdout is valid JSON on its own
        assert tuple(fragment["hooks"]) == HOOK_EVENTS
        assert "hint: re-run with --write" in err and "one writer" in err

    def test_write_creates_the_user_file_privately(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run([*_ARGV, "--write"], context)
        assert code == 0
        assert out == f"created {_settings_file(context)}\n"
        assert not (context.cwd / ".claude").exists()  # nothing per project
        written = json.loads(_settings_file(context).read_text())
        command = written["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert command.startswith(f"{_EXECUTABLE} -m neosian.record ")
        if sys.platform != "win32":
            assert _settings_file(context).stat().st_mode & 0o777 == 0o600

    def test_the_project_level_writes_this_directorys_file(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run([*_ARGV, "--level", "project", "--write"], context)
        assert code == 0 and out == f"created {_project_file(context)}\n"
        assert not _settings_file(context).exists()

    def test_write_updates_an_existing_file(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        _settings_file(context).write_text('{"keep": 1, "hooks": {"Stop": []}}\n')
        code, out, _ = _run([*_ARGV, "--write"], context)
        assert code == 0
        assert out == f"updated {_settings_file(context)}\n"
        written = json.loads(_settings_file(context).read_text())
        assert written["keep"] == 1 and len(written["hooks"]["Stop"]) == 1

    def test_unparseable_json_is_refused_and_untouched(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        _settings_file(context).write_text("{not json")
        code, _, err = _run([*_ARGV, "--write"], context)
        assert code == 1 and "not valid JSON" in err
        assert _settings_file(context).read_text() == "{not json"

    @pytest.mark.parametrize(
        "argv",
        [
            ["--root", "m", "--scope", "user:me"],  # no client
            ["--client", "cursor", "--root", "m", "--scope", "user:me"],
            ["--client", "claude-code", "--root", "m", "--url", "http://x"],
            ["--client", "claude-code", "--root", "m", "--scope", "NOT A SCOPE"],
        ],
    )
    def test_bad_invocations_exit_2(self, tmp_path: Path, argv: list[str]) -> None:
        code, out, _ = _run(argv, _context(tmp_path))
        assert code == 2 and out == ""


class TestTheHome:
    """DESIGN §22: no store flags — the home is the root. Once per machine
    (§22.6) the line names no mount and ends on the client's project
    directory; at the project level this directory's layout is rendered
    visibly into it."""

    def test_no_flags_render_the_home_and_the_project_anchor(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        env = {HOME_ENV: str(tmp_path / "nh")}
        code, out, err = _run(["--client", "claude-code", "--json"], context, env)
        assert code == 0, err
        command = json.loads(out)["command"]
        assert f"--root {tmp_path / 'nh'}" in command and "--mount" not in command
        assert f"--spool {tmp_path / 'nh' / 'spool'}" in command
        assert command.endswith(' --project "$CLAUDE_PROJECT_DIR"')
        assert not (tmp_path / "nh").exists()  # print mode builds nothing

    def test_a_nameless_directory_is_fine_once_per_machine(
        self, tmp_path: Path
    ) -> None:
        context = replace(_context(tmp_path), cwd=Path("/"))
        (context.home / ".claude").mkdir()
        assert _run(["--client", "claude-code"], context)[0] == 0

    def test_an_unreadable_login_is_caught_at_install_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_login() -> str:
            raise OSError("no passwd entry")

        monkeypatch.setattr("getpass.getuser", no_login)
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, _, err = _run(["--client", "claude-code"], context)
        assert code == 2 and "login" in err  # not inside the hook, later

    def test_the_project_level_renders_the_derived_layout(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        env = {HOME_ENV: str(tmp_path / "nh")}
        code, out, err = _run(
            ["--client", "claude-code", "--level", "project"], context, env
        )
        assert code == 0, err
        command = out  # the hooks fragment carries the one shell line
        assert f"--root {tmp_path / 'nh'}" in command and "--project" not in command
        assert "--mount scope=user:" in command
        assert ",path=user " in command
        assert "/proj:proj,path=project" in command  # the cwd's name, slugged
        assert f"--spool {tmp_path / 'nh' / 'spool'}" in command
        assert "one root shared by every project" in err  # the one-writer hint
        assert not (tmp_path / "nh").exists()  # print mode builds nothing

    def test_explicit_mounts_win(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run(
            ["--client", "claude-code", "--scope", "user:me", "--json"], context
        )
        assert code == 0
        command = json.loads(out)["command"]
        assert (
            "scope=user:me,path=memories" in command and "path=project" not in command
        )

    def test_a_nameless_directory_exits_2_at_the_project_level(
        self, tmp_path: Path
    ) -> None:
        context = replace(_context(tmp_path), cwd=Path("/"))
        code, out, err = _run(
            ["--client", "claude-code", "--level", "project"], context
        )
        assert code == 2 and out == ""
        assert "--scope" in err


class TestOneLevel:
    """§22.6: every client merges its hook sources, so ours at two levels
    fires twice and lands each span twice. One level per client."""

    @staticmethod
    def _theirs() -> dict[str, Any]:
        return {"hooks": [{"type": "command", "command": "their-linter"}]}

    def test_a_user_level_write_removes_this_directorys_entry(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        assert _run([*_ARGV, "--level", "project", "--write"], context)[0] == 0
        project = json.loads(_project_file(context).read_text())
        project["hooks"]["Stop"].insert(0, self._theirs())
        project["model"] = "sonnet"
        _project_file(context).write_text(json.dumps(project))
        # The user level arrives: the project's entry would now fire beside it.
        code, out, _ = _run([*_ARGV, "--write", "--json"], context)
        assert code == 0
        assert json.loads(out)["displaced"] == str(_project_file(context))
        left = json.loads(_project_file(context).read_text())
        assert left["model"] == "sonnet"  # every other key survives
        assert left["hooks"] == {"Stop": [self._theirs()]}  # theirs kept, ours gone
        assert "neosian.record" in _settings_file(context).read_text()

    def test_print_mode_names_it_and_touches_nothing(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        assert _run([*_ARGV, "--level", "project", "--write"], context)[0] == 0
        before = _project_file(context).read_text()
        code, _, err = _run(_ARGV, context)
        assert code == 0
        assert f"--write also removes ours from {_project_file(context)}" in err
        assert _project_file(context).read_text() == before

    def test_the_project_plugin_is_ours_whole_and_is_unlinked(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".config" / "opencode").mkdir(parents=True)
        argv = ["--client", "opencode", "--root", "m"]
        assert _run([*argv, "--level", "project", "--write"], context)[0] == 0
        plugin = context.cwd / ".opencode" / "plugins" / "neosian-record.js"
        assert plugin.is_file()
        code, out, _ = _run([*argv, "--write"], context)
        assert code == 0 and f"removed ours from {plugin}" in out
        assert not plugin.exists()

    @pytest.mark.parametrize("write", [[], ["--write"]])
    def test_the_project_level_is_refused_beside_user_level_hooks(
        self, tmp_path: Path, write: list[str]
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        assert _run([*_ARGV, "--write"], context)[0] == 0
        # In print mode too: it never promises a write that would be refused.
        code, out, err = _run([*_ARGV, "--level", "project", *write], context)
        assert code == 1 and out == ""
        assert "both would fire" in err and str(_settings_file(context)) in err
        assert "NEOSIAN_SCOPE" in err  # the override that needs no second hook
        assert not _project_file(context).exists()

    def test_a_user_level_rerun_is_idempotent(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        assert _run([*_ARGV, "--write"], context)[0] == 0
        code, out, _ = _run([*_ARGV, "--write", "--json"], context)
        assert code == 0 and json.loads(out)["displaced"] is None

    def test_run_from_the_clients_home_the_levels_are_one_file(
        self, tmp_path: Path
    ) -> None:
        # `claude` started in `~`: `./.claude/settings.json` IS the user file.
        context = replace(_context(tmp_path), cwd=tmp_path / "home")
        (context.home / ".claude").mkdir()
        assert _run([*_ARGV, "--write"], context)[0] == 0
        code, out, _ = _run(
            [*_ARGV, "--level", "project", "--write", "--json"], context
        )
        assert code == 0 and json.loads(out)["displaced"] is None  # not refused

    def test_strip_hooks_drops_an_emptied_event_and_keeps_the_rest(self) -> None:
        ours = hook_fragment("/py -m neosian.record")["hooks"]
        document = {"hooks": {**ours, "Stop": [self._theirs(), *ours["Stop"]]}, "k": 1}
        assert strip_hooks(document) == {"hooks": {"Stop": [self._theirs()]}, "k": 1}
        assert strip_hooks({"hooks": "not an object"}) == {"hooks": "not an object"}


class TestCodex:
    _ARGV = ["--client", "codex", "--root", "m", "--scope", "user:me"]

    def test_the_agent_kind_follows_the_client(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".codex").mkdir()
        code, out, err = _run(self._ARGV, context)
        assert code == 0, err
        command = json.loads(out)["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "--agent codex" in command and "--project" not in command
        assert "hint: Codex runs a new hook once you have reviewed it" in err
        assert not (context.home / ".codex" / "hooks.json").exists()  # print mode
        _, _, err = _run([*self._ARGV, "--level", "project"], context)
        assert "hint: Codex loads project hooks only for a trusted project" in err

    def test_an_explicit_agent_wins(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".codex").mkdir()
        code, out, _ = _run([*self._ARGV, "--agent", "openai-codex"], context)
        assert code == 0
        assert (
            "--agent openai-codex"
            in json.loads(out)["hooks"]["Stop"][0]["hooks"][0]["command"]
        )

    @pytest.mark.parametrize("level", ["user", "project"])
    def test_write_lands_the_levels_hooks_file(
        self, tmp_path: Path, level: str
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".codex").mkdir()
        code, out, _ = _run([*self._ARGV, "--level", level, "--write"], context)
        assert code == 0
        where = context.home if level == "user" else context.cwd
        target = where / ".codex" / "hooks.json"
        assert out == f"created {target}\n"
        assert set(json.loads(target.read_text())["hooks"]) == set(HOOK_EVENTS)

    def test_a_missing_codex_home_is_refused(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        code, _, err = _run([*self._ARGV, "--write"], context)
        assert code == 1 and str(context.home / ".codex") in err
        assert not (context.cwd / ".codex").exists()


class TestOpenCode:
    """OpenCode has no shell hooks: the row is a plugin file, ours whole."""

    _ARGV = ["--client", "opencode", "--root", "m", "--scope", "user:me"]

    def test_the_target_is_the_plugin_file_at_its_level(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        config_dir = context.home / ".config" / "opencode"
        assert resolve_target("opencode", context).config_path == (
            config_dir / "plugins" / "neosian-record.js"
        )
        target = resolve_target("opencode", context, "project")
        assert (
            target.config_path
            == context.cwd / ".opencode" / "plugins" / "neosian-record.js"
        )
        assert target.evidence_dir == context.home / ".config" / "opencode"
        assert target.plugin
        moved = Environment(
            home=tmp_path,
            cwd=tmp_path,
            platform="darwin",
            env={"OPENCODE_CONFIG_DIR": str(tmp_path / "oc")},
            executable=_EXECUTABLE,
        )
        assert resolve_target("opencode", moved).evidence_dir == tmp_path / "oc"

    def test_print_mode_is_the_plugin_source(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".config" / "opencode").mkdir(parents=True)
        code, out, err = _run(self._ARGV, context)
        assert code == 0, err
        assert out.startswith("// neosian record — the OpenCode plugin.")
        assert '"chat.message"' in out and '"tool.execute.after"' in out
        assert "session.idle" in out and "__NEOSIAN" not in out
        argv = json.loads(out.split("const ARGV = ", 1)[1].split(";", 1)[0])
        assert argv[:3] == [_EXECUTABLE, "-m", "neosian.record"]
        assert argv[argv.index("--agent") + 1] == "opencode"
        # One plugin serves every project: it hands the verb the directory
        # OpenCode opened (the argv itself names no mount once per machine).
        assert "async ({ client, $, directory })" in out
        assert '[...ARGV, "--project", directory]' in out
        assert "hint: re-run with --write" in err
        assert not (context.cwd / ".opencode").exists()

    def test_write_lands_the_plugin_and_overwrites_it(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".config" / "opencode").mkdir(parents=True)
        code, out, _ = _run([*self._ARGV, "--write"], context)
        # `plugins/` is created inside OpenCode's own config directory, which
        # had to exist already; nothing lands in the project.
        target = context.home / ".config" / "opencode" / "plugins" / "neosian-record.js"
        assert code == 0 and out == f"created {target}\n"
        assert not (context.cwd / ".opencode").exists()
        assert "export const NeosianRecord" in target.read_text()
        code, out, _ = _run([*self._ARGV, "--agent", "oc", "--write"], context)
        assert code == 0 and out == f"updated {target}\n"
        assert '"--agent", "oc"' in target.read_text()

    def test_json_envelope_carries_the_plugin(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".config" / "opencode").mkdir(parents=True)
        code, out, _ = _run([*self._ARGV, "--json"], context)
        assert code == 0
        payload = json.loads(out)
        assert payload["hooks"] is None and "NeosianRecord" in payload["plugin"]

    def test_a_missing_config_dir_is_refused(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        code, _, err = _run([*self._ARGV, "--write"], context)
        assert code == 1 and "OpenCode is not installed here" in err
        assert not (context.cwd / ".opencode").exists()


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
        assert tuple(payload["hooks"]) == HOOK_EVENTS and payload["plugin"] is None
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
