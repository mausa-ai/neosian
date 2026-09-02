"""`neosian mcp install` — targets, entry, merge, tiers (DESIGN §14.5)."""

import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from neosian._foundation.mcp.install import (
    CLIENT_CHOICES,
    SERVER_NAME,
    Environment,
    RegistrationEntry,
    build_entry,
    load_document,
    merge_entry,
    resolve_target,
    run_install,
)
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.settings import StoreSettings

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
    actor: str = "mcp",
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
        assert CLIENT_CHOICES == ("claude-code", "claude-desktop", "cursor")

    def test_claude_code_is_the_project_file(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        target = resolve_target("claude-code", context)
        assert target.config_path == context.cwd / ".mcp.json"
        assert target.evidence_dir == context.home / ".claude"
        assert target.servers_key == "mcpServers"

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

    def test_no_store_exits_2(self, tmp_path: Path) -> None:
        code, _, err = _run(["--client", "cursor"], _context(tmp_path))
        assert code == 2
        assert "store is required" in err

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
