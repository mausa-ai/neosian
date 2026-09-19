"""`neosian mcp install` — targets, entry, merge, tiers (DESIGN §14.5), and
the level: once per machine by default, this directory's file on request
(§22.6)."""

import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from neosian._foundation.mcp.install import (
    Environment,
    RegistrationEntry,
    build_entry,
    merge_entry,
    run_install,
)
from neosian._foundation.mcp.targets import (
    CLIENT_CHOICES,
    SERVER_NAME,
    resolve_target,
)
from neosian._foundation.memory.home import HOME_ENV
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.settings import StoreSettings
from neosian._foundation.shared.client_config import load_document

_EXECUTABLE = "/venv/bin/python3"


def _context(tmp_path: Path, platform: str = "darwin") -> Environment:
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    home.mkdir(exist_ok=True)
    cwd.mkdir(exist_ok=True)
    return Environment(
        home=home, cwd=cwd, platform=platform, env={}, executable=_EXECUTABLE
    )


def _settings(
    *,
    mounts: tuple[Mount, ...] = (Mount(scope="user:me", mount_path="memories"),),
    root: Path | None = Path("mem"),
    dsn: str | None = None,
    schema: str = "neosian",
    actor: str = "mcp:stdio",
) -> StoreSettings:
    return StoreSettings(mounts=mounts, root=root, dsn=dsn, schema=schema, actor=actor)


def _run(
    argv: Sequence[str], context: Environment, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_install(argv, env or {}, context=context, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


_WRITE_ARGV = (
    "--client",
    "claude-code",
    "--level",
    "project",  # the file neosian merges; the user level is Claude Code's CLI's
    "--root",
    "m",
    "--scope",
    "user:me",
    "--write",
)


def _posix_only() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class TestTargets:
    def test_choices_come_from_the_table(self) -> None:
        assert CLIENT_CHOICES == (
            "claude-code",
            "claude-desktop",
            "cursor",
            "codex",
            "opencode",
        )

    def test_codex_is_its_home_toml_or_codex_home(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        target = resolve_target("codex", context)
        assert target.config_path == context.home / ".codex" / "config.toml"
        assert target.evidence_dir == context.home / ".codex"
        assert target.cli == "codex" and target.level == "user"
        moved = Environment(
            home=context.home,
            cwd=context.cwd,
            platform="darwin",
            env={"CODEX_HOME": str(tmp_path / "ch")},
            executable=_EXECUTABLE,
        )
        assert resolve_target("codex", moved).evidence_dir == tmp_path / "ch"

    def test_claude_code_is_the_project_file_at_the_project_level(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        target = resolve_target("claude-code", context, "project")
        assert target.config_path == context.cwd / ".mcp.json"
        assert target.evidence_dir == context.home / ".claude"
        assert target.servers_key == "mcpServers"
        assert target.cli is None and target.level == "project"

    def test_claude_code_is_its_own_clis_file_at_the_user_level(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        target = resolve_target("claude-code", context)
        assert target.config_path == context.home / ".claude.json"
        assert target.evidence_dir == context.home / ".claude"
        assert target.cli == "claude" and target.level == "user"
        moved = Environment(
            home=context.home,
            cwd=context.cwd,
            platform="darwin",
            env={"CLAUDE_CONFIG_DIR": str(tmp_path / "cc")},
            executable=_EXECUTABLE,
        )
        sandboxed = resolve_target("claude-code", moved)
        assert sandboxed.config_path == tmp_path / "cc" / ".claude.json"
        assert sandboxed.evidence_dir == tmp_path / "cc"

    @pytest.mark.parametrize("client", ["codex", "cursor", "claude-desktop"])
    def test_a_one_file_client_is_always_the_user_level(
        self, tmp_path: Path, client: str
    ) -> None:
        context = _context(tmp_path)
        asked = resolve_target(client, context, "project")
        assert asked.level == "user"
        assert asked.config_path == resolve_target(client, context).config_path

    def test_cursor_is_the_user_file(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        target = resolve_target("cursor", context)
        assert target.config_path == context.home / ".cursor" / "mcp.json"
        assert target.evidence_dir == context.home / ".cursor"

    @pytest.mark.parametrize(
        ("platform", "suffix"),
        [
            ("darwin", "Library/Application Support/Claude"),
            ("linux", ".config/Claude"),
        ],
    )
    def test_claude_desktop_follows_the_platform(
        self, tmp_path: Path, platform: str, suffix: str
    ) -> None:
        context = _context(tmp_path, platform=platform)
        target = resolve_target("claude-desktop", context)
        assert target.evidence_dir == context.home / suffix
        assert target.config_path.name == "claude_desktop_config.json"

    def test_claude_desktop_on_windows_reads_appdata(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        appdata = tmp_path / "Roaming"
        context = Environment(
            home=home,
            cwd=tmp_path,
            platform="win32",
            env={"APPDATA": str(appdata)},
            executable=_EXECUTABLE,
        )
        target = resolve_target("claude-desktop", context)
        assert target.evidence_dir == appdata / "Claude"


class TestEntry:
    def test_the_module_path_is_the_argv_prefix(self) -> None:
        entry = build_entry(_settings(), executable=_EXECUTABLE)
        assert entry.command == _EXECUTABLE
        assert entry.args[:2] == ("-m", "neosian.mcp")

    def test_scope_is_rendered_back_as_a_mount_token(self) -> None:
        entry = build_entry(_settings(), executable=_EXECUTABLE)
        assert "--mount" in entry.args
        token = entry.args[entry.args.index("--mount") + 1]
        assert token == "scope=user:me,path=memories"

    def test_root_is_absolutised(self) -> None:
        entry = build_entry(
            _settings(root=Path("relative/mem")), executable=_EXECUTABLE
        )
        root = entry.args[entry.args.index("--root") + 1]
        assert Path(root).is_absolute()

    def test_read_only_mounts_round_trip(self) -> None:
        mounts = (
            Mount(scope="user:me", mount_path="memories"),
            Mount(scope="tenant:acme/kb:shared", mount_path="kb", read_only=True),
        )
        entry = build_entry(_settings(mounts=mounts), executable=_EXECUTABLE)
        tokens = [entry.args[i + 1] for i, a in enumerate(entry.args) if a == "--mount"]
        assert tokens == [
            "scope=user:me,path=memories",
            "scope=tenant:acme/kb:shared,path=kb,ro",
        ]

    def test_the_dsn_is_never_in_the_args(self) -> None:
        dsn = "postgresql://u:secret@db/neosian"
        entry = build_entry(
            _settings(root=None, dsn=dsn, schema="my_app"), executable=_EXECUTABLE
        )
        assert dsn not in " ".join(entry.args)
        assert "--root" not in entry.args
        assert entry.args[entry.args.index("--schema") + 1] == "my_app"

    def test_a_non_default_actor_rides(self) -> None:
        entry = build_entry(_settings(actor="mcp:desk"), executable=_EXECUTABLE)
        assert entry.args[entry.args.index("--actor") + 1] == "mcp:desk"
        default = build_entry(_settings(), executable=_EXECUTABLE)
        assert "--actor" not in default.args


class TestMerge:
    _ENTRY = RegistrationEntry(command=_EXECUTABLE, args=("-m", "neosian.mcp"))

    def test_unknown_top_level_keys_survive(self, tmp_path: Path) -> None:
        document = {"theme": "dark", "mcpServers": {}}
        merged = merge_entry(
            document,
            servers_key="mcpServers",
            name=SERVER_NAME,
            entry=self._ENTRY,
            path=tmp_path / "x.json",
        )
        assert merged["theme"] == "dark"
        assert document["mcpServers"] == {}  # the input is never mutated

    def test_other_servers_survive_and_ours_is_replaced(self, tmp_path: Path) -> None:
        document = {
            "mcpServers": {
                "other": {"command": "x"},
                SERVER_NAME: {"command": "stale"},
            }
        }
        merged = merge_entry(
            document,
            servers_key="mcpServers",
            name=SERVER_NAME,
            entry=self._ENTRY,
            path=tmp_path / "x.json",
        )
        assert merged["mcpServers"]["other"] == {"command": "x"}
        assert merged["mcpServers"][SERVER_NAME]["command"] == _EXECUTABLE

    def test_a_missing_file_loads_as_empty(self, tmp_path: Path) -> None:
        assert load_document(tmp_path / "absent.json") == {}


class TestExitTiers:
    def test_missing_client_dir_exits_1_and_creates_nothing(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)  # no home/.cursor
        code, out, err = _run(
            ["--client", "cursor", "--root", "m", "--scope", "user:me", "--write"],
            context,
        )
        assert code == 1
        assert out == ""
        assert "error:" in err and str(context.home / ".cursor") in err
        assert not (context.home / ".cursor").exists()

    def test_print_mode_writes_nothing(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run(
            ["--client", "claude-code", "--root", "m", "--scope", "user:me"],
            context,
        )
        assert code == 0
        assert not (context.cwd / ".mcp.json").exists()
        fragment = json.loads(out)  # stdout is valid JSON on its own
        assert fragment["mcpServers"][SERVER_NAME]["command"] == _EXECUTABLE

    def test_write_creates_the_file_when_the_dir_exists(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run(
            [
                "--client",
                "claude-code",
                "--level",
                "project",
                "--root",
                "m",
                "--scope",
                "user:me",
                "--write",
            ],
            context,
        )
        assert code == 0
        assert out == f"created {context.cwd / '.mcp.json'}\n"
        written = json.loads((context.cwd / ".mcp.json").read_text())
        assert written["mcpServers"][SERVER_NAME]["args"][:2] == ["-m", "neosian.mcp"]

    def test_write_updates_an_existing_file(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        config = context.cwd / ".mcp.json"
        config.write_text('{"mcpServers": {"other": {"command": "x"}}, "keep": 1}\n')
        code, out, _ = _run(
            [
                "--client",
                "claude-code",
                "--level",
                "project",
                "--root",
                "m",
                "--scope",
                "user:me",
                "--write",
            ],
            context,
        )
        assert code == 0
        assert out == f"updated {config}\n"
        written = json.loads(config.read_text())
        assert written["keep"] == 1
        assert written["mcpServers"]["other"] == {"command": "x"}
        assert SERVER_NAME in written["mcpServers"]

    @pytest.mark.parametrize("mode", [0o600, 0o644])
    def test_write_keeps_the_existing_files_mode(
        self, tmp_path: Path, mode: int
    ) -> None:
        # MCP configs carry other servers' credentials: a hand-tightened
        # 0600 must not widen to the umask default on rewrite.
        _posix_only()
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        config = context.cwd / ".mcp.json"
        config.write_text('{"mcpServers": {}}\n')
        config.chmod(mode)
        code, _, _ = _run(_WRITE_ARGV, context)
        assert code == 0
        assert _mode(config) == mode

    def test_write_creates_a_private_file(self, tmp_path: Path) -> None:
        _posix_only()
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, _, _ = _run(_WRITE_ARGV, context)
        assert code == 0
        assert _mode(context.cwd / ".mcp.json") == 0o600

    def test_unparseable_json_is_refused_and_the_file_untouched(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        config = context.cwd / ".mcp.json"
        config.write_text("{not json")
        code, out, err = _run(
            [
                "--client",
                "claude-code",
                "--level",
                "project",
                "--root",
                "m",
                "--scope",
                "user:me",
                "--write",
            ],
            context,
        )
        assert code == 1
        assert out == ""
        assert "not valid JSON" in err
        assert config.read_text() == "{not json"

    def test_a_non_object_servers_key_is_refused(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        config = context.cwd / ".mcp.json"
        config.write_text('{"mcpServers": ["not", "an", "object"]}')
        code, _, err = _run(
            [
                "--client",
                "claude-code",
                "--level",
                "project",
                "--root",
                "m",
                "--scope",
                "user:me",
                "--write",
            ],
            context,
        )
        assert code == 1
        assert "mcpServers" in err
        assert json.loads(config.read_text()) == {"mcpServers": ["not", "an", "object"]}

    def test_missing_client_exits_2(self, tmp_path: Path) -> None:
        code, out, _ = _run(["--root", "m", "--scope", "user:me"], _context(tmp_path))
        assert code == 2
        assert out == ""

    def test_unknown_client_exits_2(self, tmp_path: Path) -> None:
        code, _, err = _run(
            ["--client", "zed", "--root", "m", "--scope", "user:me"],
            _context(tmp_path),
        )
        assert code == 2
        assert "claude-code" in err  # choices named in the argparse error

    def test_no_flags_render_the_home_and_no_mount(self, tmp_path: Path) -> None:
        # DESIGN §22.6: once per machine, the line names the home and no
        # mount; the server derives each session's layout where the client
        # spawns it. Print mode builds nothing.
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run(
            ["--client", "claude-code"], context, {HOME_ENV: str(tmp_path / "nh")}
        )
        assert code == 0
        args = json.loads(out)["mcpServers"]["neosian-memory"]["args"]
        assert args[args.index("--root") + 1] == str(tmp_path / "nh")
        assert "--mount" not in args and "--project" not in args
        assert not (tmp_path / "nh").exists()

    def test_the_project_level_renders_the_derived_layout(self, tmp_path: Path) -> None:
        # DESIGN §22.2: this directory's two-mount layout, the scope explicit
        # in the file, its spelling derived.
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, _ = _run(
            ["--client", "claude-code", "--level", "project"],
            context,
            {HOME_ENV: str(tmp_path / "nh")},
        )
        assert code == 0
        args = json.loads(out)["mcpServers"]["neosian-memory"]["args"]
        assert args[args.index("--root") + 1] == str(tmp_path / "nh")
        tokens = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]
        assert [t.split(",")[1] for t in tokens] == ["path=user", "path=project"]
        assert tokens[1].split(",")[0].endswith("/proj:proj")
        assert not (tmp_path / "nh").exists()

    def test_two_stores_exit_2(self, tmp_path: Path) -> None:
        code, _, err = _run(
            ["--client", "cursor", "--root", "m", "--url", "http://x"],
            _context(tmp_path),
        )
        assert code == 2
        assert "mutually exclusive" in err

    def test_a_bad_scope_exits_2_with_the_code(self, tmp_path: Path) -> None:
        code, _, err = _run(
            ["--client", "cursor", "--root", "m", "--scope", "NOT A SCOPE"],
            _context(tmp_path),
        )
        assert code == 2
        assert "[memory_scope_invalid]" in err

    def test_help_exits_0_and_names_the_flags(self, tmp_path: Path) -> None:
        code, out, _ = _run(["--help"], _context(tmp_path))
        assert code == 0
        assert "--client" in out
        assert "--write" in out


class TestRendering:
    def test_print_guidance_lands_on_stderr(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, out, err = _run(
            ["--client", "cursor", "--root", "m", "--scope", "user:me"], context
        )
        assert code == 0
        json.loads(out)
        assert "hint: re-run with --write" in err
        assert str(context.home / ".cursor" / "mcp.json") in err

    def test_json_success_envelope_is_one_line(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, out, err = _run(
            ["--client", "cursor", "--root", "m", "--scope", "user:me", "--json"],
            context,
        )
        assert code == 0
        assert err == ""
        assert out.count("\n") == 1
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["written"] is False
        assert payload["entry"]["command"] == _EXECUTABLE

    def test_json_failure_envelope_and_empty_stderr(self, tmp_path: Path) -> None:
        context = _context(tmp_path)  # cursor dir missing
        code, out, err = _run(
            ["--client", "cursor", "--root", "m", "--scope", "user:me", "--json"],
            context,
        )
        assert code == 1
        assert err == ""
        payload = json.loads(out)
        assert payload["success"] is False
        assert payload["error"] and payload["hint"]

    def test_the_postgres_hint_names_the_env_key(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, out, err = _run(
            ["--client", "cursor", "--scope", "user:me"],
            context,
            env={"NEOSIAN_POSTGRES_DSN": "postgresql://u:secret@db/x"},
        )
        assert code == 0
        assert "secret" not in out
        assert "NEOSIAN_POSTGRES_DSN" in err

    def test_the_write_hint_names_the_one_writer_rule(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, _, err = _run(
            ["--client", "cursor", "--root", "m", "--scope", "user:me", "--write"],
            context,
        )
        assert code == 0
        assert "one writer per FileStore root" in err


class TestCodex:
    """Codex's config is TOML its own CLI writes: print mode renders the
    table and the `codex mcp add` line; --write is refused with that line."""

    _ARGV = ["--client", "codex", "--root", "m", "--scope", "user:me"]

    def test_print_mode_renders_toml_and_the_apply_line(self, tmp_path: Path) -> None:
        import tomllib

        context = _context(tmp_path)
        (context.home / ".codex").mkdir()
        code, out, err = _run(self._ARGV, context)
        assert code == 0, err
        table = tomllib.loads(out)["mcp_servers"][SERVER_NAME]
        assert table["command"] == _EXECUTABLE
        assert table["args"][:2] == ["-m", "neosian.mcp"]
        assert "--actor mcp:codex" in " ".join(table["args"]) or (
            table["args"][table["args"].index("--actor") + 1] == "mcp:codex"
        )
        assert (
            f"hint: apply it with: codex mcp add {SERVER_NAME} -- {_EXECUTABLE}" in err
        )
        assert "re-run with --write" not in err

    def test_write_is_refused_with_the_apply_line(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".codex").mkdir()
        code, out, err = _run([*self._ARGV, "--write"], context)
        assert code == 1 and out == ""
        assert "own CLI writes" in err and "codex mcp add" in err
        assert not (context.home / ".codex" / "config.toml").exists()

    def test_json_envelope_carries_the_apply_line(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".codex").mkdir()
        code, out, _ = _run([*self._ARGV, "--json"], context)
        assert code == 0
        payload = json.loads(out)
        assert payload["apply"].startswith(f"codex mcp add {SERVER_NAME} -- ")
        assert payload["servers_key"] == "mcp_servers"

    def test_a_json_client_has_no_apply_line(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, out, _ = _run(
            ["--client", "cursor", "--root", "m", "--scope", "user:me", "--json"],
            context,
        )
        assert code == 0 and json.loads(out)["apply"] is None


class TestLevel:
    """§22.6: the user level is the default; a one-file client has no other."""

    @pytest.mark.parametrize("client", ["codex", "cursor", "claude-desktop"])
    def test_the_project_level_is_refused_on_a_one_file_client(
        self, tmp_path: Path, client: str
    ) -> None:
        code, out, err = _run(
            ["--client", client, "--level", "project", "--root", "m"],
            _context(tmp_path),
        )
        assert code == 2 and out == ""
        assert "keeps one file for every project" in err

    def test_an_explicit_scope_is_rendered_at_the_user_level(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, out, _ = _run(
            ["--client", "cursor", "--root", "m", "--scope", "user:me"], context
        )
        assert code == 0
        args = json.loads(out)["mcpServers"][SERVER_NAME]["args"]
        assert args[args.index("--mount") + 1] == "scope=user:me,path=memories"

    def test_a_one_file_client_no_longer_carries_one_projects_scope(
        self, tmp_path: Path
    ) -> None:
        # The defect the ruling fixed: ~/.cursor/mcp.json serves every project,
        # and used to carry the scope of wherever the installer ran.
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, out, _ = _run(["--client", "cursor", "--root", "m"], context)
        assert code == 0
        assert "--mount" not in json.loads(out)["mcpServers"][SERVER_NAME]["args"]

    def test_a_nameless_directory_is_fine_at_the_user_level(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        rootless = Environment(
            home=context.home,
            cwd=Path("/"),
            platform="darwin",
            env={},
            executable=_EXECUTABLE,
        )
        code, _, err = _run(["--client", "cursor", "--root", "m"], rootless)
        assert code == 0, err

    def test_an_unreadable_login_is_caught_at_install_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_login() -> str:
            raise OSError("no passwd entry")

        monkeypatch.setattr("getpass.getuser", no_login)
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        code, _, err = _run(["--client", "cursor", "--root", "m"], context)
        assert code == 2 and "login" in err  # not inside a hook, later


class TestClaudeCodeUser:
    """Claude Code's user scope lives in `~/.claude.json`, which its own CLI
    writes: print mode renders the entry and the `claude mcp add-json` line;
    --write is refused with that line (Codex's shape, #135)."""

    _ARGV = ["--client", "claude-code", "--root", "m"]

    def test_print_mode_renders_the_fragment_and_the_apply_line(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, err = _run(self._ARGV, context)
        assert code == 0, err
        entry = json.loads(out)["mcpServers"][SERVER_NAME]
        assert entry["command"] == _EXECUTABLE
        prefix = f"hint: apply it with: claude mcp add-json --scope user {SERVER_NAME} "
        (line,) = [ln for ln in err.splitlines() if ln.startswith(prefix)]
        import shlex

        assert json.loads(
            shlex.split(line.removeprefix("hint: apply it with: "))[-1]
        ) == (entry)
        assert "re-run with --write" not in err

    def test_write_is_refused_with_the_apply_line(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code, out, err = _run([*self._ARGV, "--write"], context)
        assert code == 1 and out == ""
        assert "own CLI writes" in err and "claude mcp add-json" in err
        assert not (context.home / ".claude.json").exists()

    def test_the_envelope_carries_the_level_and_the_apply_line(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        payload = json.loads(_run([*self._ARGV, "--json"], context)[1])
        assert payload["level"] == "user"
        assert payload["apply"].startswith("claude mcp add-json --scope user ")
        assert payload["config_path"] == str(context.home / ".claude.json")


class TestOpenCode:
    """OpenCode's MCP config is JSON under `mcp`, the entry in its own
    shape: its own config directory once per machine, the project's
    opencode.json at the project level."""

    _ARGV = ["--client", "opencode", "--root", "m", "--scope", "user:me"]

    def test_the_target_and_the_entry_shape(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        config_dir = context.home / ".config" / "opencode"
        assert resolve_target("opencode", context).config_path == (
            config_dir / "opencode.json"
        )
        target = resolve_target("opencode", context, "project")
        assert target.config_path == context.cwd / "opencode.json"
        assert target.evidence_dir == context.home / ".config" / "opencode"
        assert target.servers_key == "mcp" and target.style == "opencode"
        (context.home / ".config" / "opencode").mkdir(parents=True)
        code, out, err = _run(self._ARGV, context)
        assert code == 0, err
        entry = json.loads(out)["mcp"][SERVER_NAME]
        assert entry["type"] == "local" and entry["enabled"] is True
        assert entry["command"][:3] == [_EXECUTABLE, "-m", "neosian.mcp"]
        assert "--actor" in entry["command"] and "args" not in entry

    def test_write_merges_into_opencode_json(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".config" / "opencode").mkdir(parents=True)
        config = context.cwd / "opencode.json"
        config.write_text(
            '{"$schema": "https://opencode.ai/config.json", '
            '"mcp": {"other": {"type": "remote", "url": "x"}}}\n'
        )
        code, out, _ = _run([*self._ARGV, "--level", "project", "--write"], context)
        assert code == 0 and out == f"updated {config}\n"
        written = json.loads(config.read_text())
        assert written["$schema"].startswith("https://")
        assert written["mcp"]["other"]["url"] == "x"
        assert written["mcp"][SERVER_NAME]["type"] == "local"

    def test_the_user_level_writes_its_own_config_directory(
        self, tmp_path: Path
    ) -> None:
        context = _context(tmp_path)
        config_dir = context.home / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        code, out, err = _run([*self._ARGV, "--write"], context)
        assert code == 0, err
        assert out == f"created {config_dir / 'opencode.json'}\n"
        assert not (context.cwd / "opencode.json").exists()  # nothing per project

    def test_a_commented_config_beside_it_is_refused(self, tmp_path: Path) -> None:
        # OpenCode reads opencode.jsonc too: writing opencode.json beside it
        # would be a second config, and merging into it would lose comments.
        context = _context(tmp_path)
        config_dir = context.home / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        commented = config_dir / "opencode.jsonc"
        commented.write_text('{\n  // mine\n  "mcp": {}\n}\n')
        code, out, err = _run([*self._ARGV, "--write"], context)
        assert code == 1 and out == ""
        assert "comments" in err and "paste" in err
        assert not (config_dir / "opencode.json").exists()
        assert "// mine" in commented.read_text()  # untouched
        assert _run(self._ARGV, context)[0] == 0  # print mode still prints


class TestDisplace:
    """§22.6: a name connects once, so two entries never fire twice — but
    the project's entry shadows the user's, and a stale one would win."""

    _ARGV = ["--client", "opencode", "--root", "m"]

    def _registered_in_the_project(self, tmp_path: Path) -> Environment:
        context = _context(tmp_path)
        (context.home / ".config" / "opencode").mkdir(parents=True)
        (context.cwd / "opencode.json").write_text(
            '{"theme": "dark", "mcp": {"other": {"type": "remote", "url": "x"}}}'
        )
        assert _run([*self._ARGV, "--level", "project", "--write"], context)[0] == 0
        return context

    def test_a_user_level_write_removes_the_shadow(self, tmp_path: Path) -> None:
        context = self._registered_in_the_project(tmp_path)
        project = context.cwd / "opencode.json"
        code, out, _ = _run([*self._ARGV, "--write"], context)
        assert code == 0 and f"removed ours from {project}" in out
        left = json.loads(project.read_text())
        assert left == {
            "theme": "dark",
            "mcp": {"other": {"type": "remote", "url": "x"}},
        }
        user = context.home / ".config" / "opencode" / "opencode.json"
        assert SERVER_NAME in json.loads(user.read_text())["mcp"]

    def test_print_mode_names_it_and_touches_nothing(self, tmp_path: Path) -> None:
        context = self._registered_in_the_project(tmp_path)
        before = (context.cwd / "opencode.json").read_text()
        code, out, err = _run([*self._ARGV, "--json"], context)
        assert code == 0
        assert json.loads(out)["displaced"] == str(context.cwd / "opencode.json")
        assert (context.cwd / "opencode.json").read_text() == before
        assert "shadows the user level" in _run(self._ARGV, context)[2]
        assert err == ""

    def test_a_file_the_clients_cli_writes_only_names_the_shadow(
        self, tmp_path: Path
    ) -> None:
        # Claude Code's user scope is applied by `neosian setup` through
        # `claude`; the installer never removes what it has not replaced.
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        argv = ["--client", "claude-code", "--root", "m"]
        assert _run([*argv, "--level", "project", "--write"], context)[0] == 0
        before = (context.cwd / ".mcp.json").read_text()
        payload = json.loads(_run([*argv, "--json"], context)[1])
        assert payload["displaced"] == str(context.cwd / ".mcp.json")
        assert (context.cwd / ".mcp.json").read_text() == before

    def test_nothing_to_displace_is_none(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        (context.home / ".cursor").mkdir()
        payload = json.loads(
            _run(["--client", "cursor", "--root", "m", "--json"], context)[1]
        )
        assert payload["displaced"] is None  # a one-file client has one level


class TestTheClientActor:
    def test_the_default_actor_names_the_client(self, tmp_path: Path) -> None:
        """DESIGN §20: the installer knows the client, the stdio default does
        not — `mcp:<client>` is rendered unless --actor was given."""
        out, err = io.StringIO(), io.StringIO()
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code = run_install(
            ["--client", "claude-code", "--root", "m", "--scope", "user:me", "--json"],
            {},
            context=context,
            out=out,
            err=err,
        )
        assert code == 0
        args = json.loads(out.getvalue())["entry"]["args"]
        assert args[args.index("--actor") + 1] == "mcp:claude-code"


class TestTheDaemonUrl:
    def test_url_is_rendered_and_the_token_is_hinted(self, tmp_path: Path) -> None:
        """NL: an agent's MCP server behind the state process — the URL in
        the registration, the token in the client's own environment."""
        out, err = io.StringIO(), io.StringIO()
        context = _context(tmp_path)
        (context.home / ".claude").mkdir()
        code = run_install(
            [
                "--client",
                "claude-code",
                "--url",
                "http://127.0.0.1:6367",
                "--scope",
                "user:me",
            ],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
            context=context,
            out=out,
            err=err,
        )
        assert code == 0, err.getvalue()
        args = json.loads(out.getvalue())["mcpServers"][SERVER_NAME]["args"]
        assert args[args.index("--url") + 1] == "http://127.0.0.1:6367"
        assert "--root" not in args
        assert "abc" not in out.getvalue()
        assert "NEOSIAN_CLIENT_TOKEN" in err.getvalue()
