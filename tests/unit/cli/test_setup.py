"""`neosian setup` (DESIGN §30.3, §22.6): the clients found, both installers
each, print first; `--write` lands the hooks and runs the client's own CLI
for the file that CLI owns; once per machine by default; the store flags
reach both installers."""

import asyncio
import io
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from neosian._cli.setup import _spawn, present_clients, run_setup
from neosian._cli.status import collect
from neosian._foundation.memory.settings import CLIENT_TOKEN_ENV
from neosian._foundation.shared.client_config import Environment


def _context(tmp_path: Path) -> Environment:
    project = tmp_path / "demo proj"
    project.mkdir(exist_ok=True)
    return Environment(
        home=tmp_path,
        cwd=project,
        platform="darwin",
        env={},  # no PATH: the real `claude` and `codex` are never found
        executable=sys.executable,  # a real file: the interpreter resolves
    )


def _env(tmp_path: Path) -> Mapping[str, str]:
    return {"NEOSIAN_HOME": str(tmp_path / "home")}


class _Cli:
    """The client's own CLI, faked: it records what it was asked to run and
    writes Claude Code's user scope the way `claude mcp add-json` does."""

    def __init__(self, home: Path, *, exits: Sequence[int] = ()) -> None:
        self.home, self.calls = home, list[list[str]]()
        self._exits = list(exits)

    def __call__(
        self, argv: Sequence[str], env: Mapping[str, str]  # noqa: ARG002 - the seam's
    ) -> tuple[int, str]:
        self.calls.append(list(argv))
        code = self._exits.pop(0) if self._exits else 0
        if code == 0 and argv[:3] == ["claude", "mcp", "add-json"]:
            entry = json.loads(argv[-1])
            document = {"mcpServers": {argv[-2]: entry}, "oauthAccount": "kept"}
            (self.home / ".claude.json").write_text(json.dumps(document))
        return code, "" if code == 0 else "MCP server neosian-memory already exists"


def _run(
    tmp_path: Path,
    argv: list[str],
    *,
    runner: object = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    extra = {} if runner is None else {"runner": runner}
    code = run_setup(
        argv,
        env or _env(tmp_path),
        context=_context(tmp_path),
        out=out,
        err=err,
        **extra,  # type: ignore[arg-type]
    )
    return code, out.getvalue(), err.getvalue()


class TestDetection:
    def test_no_client_is_an_environment_error(self, tmp_path: Path) -> None:
        assert present_clients(_context(tmp_path)) == []
        code, out, err = _run(tmp_path, ["--json"])
        assert code == 1 and "no client found" in json.loads(out)["error"]
        assert "error:" in err

    def test_the_evidence_directories_name_the_clients(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        assert present_clients(_context(tmp_path)) == ["claude-code", "opencode"]


class TestOncePerMachine:
    def test_print_mode_writes_nothing_and_names_the_users_files(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".claude").mkdir()
        cli = _Cli(tmp_path)
        code, out, err = _run(tmp_path, [], runner=cli)
        assert code == 0, err
        assert out.splitlines()[0] == "Claude Code"
        assert (
            f"  mcp   {tmp_path / '.claude.json'}  would run: claude mcp add-json"
            in out
        )
        assert f"  hooks {tmp_path / '.claude' / 'settings.json'}  would write" in out
        assert "re-run with --write" in err and err.count("hint: one writer") == 1
        assert cli.calls == []  # print mode runs nothing
        assert not (tmp_path / ".claude" / "settings.json").exists()

    def test_write_lands_the_hooks_and_runs_the_clients_own_cli(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".claude").mkdir()
        cli = _Cli(tmp_path)
        code, out, err = _run(tmp_path, ["--write", "--json"], runner=cli)
        assert code == 0, err
        payload = json.loads(out)
        assert payload["written"] is True and payload["level"] == "user"
        (row,) = payload["clients"]
        assert row["mcp"]["applied"] and row["mcp"]["apply_error"] is None
        assert row["hooks"]["applied"] and row["hooks"]["written"]
        (add,) = cli.calls  # nothing was there: no `remove` needed
        assert add[:6] == [
            "claude",
            "mcp",
            "add-json",
            "--scope",
            "user",
            "neosian-memory",
        ]
        assert "--mount" not in json.loads(add[-1])["args"]
        hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert hooks["hooks"]["Stop"][0]["hooks"][0]["command"].endswith(
            ' --project "$CLAUDE_PROJECT_DIR"'
        )
        assert list(_context(tmp_path).cwd.iterdir()) == []  # nothing per project

    def test_an_entry_already_there_is_forgotten_and_added_again(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".claude").mkdir()
        cli = _Cli(tmp_path, exits=[1, 0, 0])  # add refuses, remove, add
        code, out, _ = _run(tmp_path, ["--write", "--json"], runner=cli)
        assert code == 0 and json.loads(out)["clients"][0]["mcp"]["applied"]
        assert [call[2] for call in cli.calls] == ["add-json", "remove", "add-json"]
        assert cli.calls[1] == [
            "claude",
            "mcp",
            "remove",
            "--scope",
            "user",
            "neosian-memory",
        ]

    def test_a_cli_off_path_leaves_the_line_to_the_user(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        code, out, _ = _run(tmp_path, ["--write"])  # the default runner, no PATH
        assert code == 1  # something is left to do, and the exit says so
        assert "not applied (claude is not on PATH); run: claude mcp add-json" in out
        assert (tmp_path / ".claude" / "settings.json").is_file()  # the hooks landed

    def test_a_failing_cli_is_reported_with_its_own_words(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        cli = _Cli(tmp_path, exits=[1, 0, 1])
        code, out, _ = _run(tmp_path, ["--write", "--json"], runner=cli)
        mcp = json.loads(out)["clients"][0]["mcp"]
        assert code == 1 and mcp["applied"] is False
        assert mcp["apply_error"] == "MCP server neosian-memory already exists"

    def test_codex_applies_through_its_own_cli(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        cli = _Cli(tmp_path)
        code, out, err = _run(tmp_path, ["--client", "codex", "--write"], runner=cli)
        assert code == 0, err
        assert cli.calls[0][:5] == ["codex", "mcp", "add", "neosian-memory", "--"]
        assert "applied: codex mcp add neosian-memory" in out
        assert (tmp_path / ".codex" / "hooks.json").is_file()

    def test_the_projects_shadow_goes_once_the_apply_succeeds(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".claude").mkdir()
        context = _context(tmp_path)
        assert _run(tmp_path, ["--level", "project", "--write"])[0] == 0
        assert "neosian-memory" in (context.cwd / ".mcp.json").read_text()
        (context.cwd / ".claude" / "settings.json").unlink()  # its hooks: another pin
        failing = _Cli(tmp_path, exits=[1, 0, 1])
        _run(tmp_path, ["--write"], runner=failing)
        assert "neosian-memory" in (context.cwd / ".mcp.json").read_text()  # kept
        assert _run(tmp_path, ["--write"], runner=_Cli(tmp_path))[0] == 0
        assert "neosian-memory" not in (context.cwd / ".mcp.json").read_text()


class TestTheStoreFlags:
    def test_the_daemon_is_one_command_for_every_client(self, tmp_path: Path) -> None:
        # §8's answer for many projects on one home, in one run (#266).
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        env = {**_env(tmp_path), CLIENT_TOKEN_ENV: "t"}
        url = "http://127.0.0.1:6367"
        cli = _Cli(tmp_path)
        code, out, err = _run(
            tmp_path, ["--url", url, "--write", "--json"], runner=cli, env=env
        )
        assert code == 0, err
        for row in json.loads(out)["clients"]:
            assert url in json.dumps(row["mcp"]["entry"]) and url in json.dumps(
                row["hooks"]["command"] or row["hooks"]["plugin"]
            )
            assert "--root" not in json.dumps(row["mcp"]["entry"])
        assert err.count(CLIENT_TOKEN_ENV) == 1 and "one writer" not in err
        assert (
            "t"
            not in json.loads((tmp_path / ".claude.json").read_text())["mcpServers"][
                "neosian-memory"
            ]["args"]
        )  # the token never lands in a file

    def test_a_root_reaches_both_installers(self, tmp_path: Path) -> None:
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        code, out, _ = _run(tmp_path, ["--root", str(tmp_path / "r"), "--json"])
        (row,) = json.loads(out)["clients"]
        assert code == 0 and str(tmp_path / "r") in json.dumps(row["mcp"]["entry"])
        assert str(tmp_path / "r") in row["hooks"]["plugin"]


class TestTheProjectLevel:
    def test_write_lands_this_directorys_files_and_status_is_green(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".claude").mkdir()
        context = _context(tmp_path)
        code, out, err = _run(tmp_path, ["--level", "project", "--write", "--json"])
        assert code == 0, err
        (row,) = json.loads(out)["clients"]
        assert row["mcp"]["written"] and row["hooks"]["written"]
        registered = json.loads((context.cwd / ".mcp.json").read_text())
        assert "--mount" in registered["mcpServers"]["neosian-memory"]["args"]
        hooks = json.loads((context.cwd / ".claude" / "settings.json").read_text())
        assert "Stop" in hooks["hooks"]
        assert not (tmp_path / ".claude" / "settings.json").exists()

        status = asyncio.run(collect(context, _env(tmp_path)))
        claude = status.clients[0]
        assert claude.installed and claude.mcp_registered and claude.hooks_present
        assert claude.interpreter_resolves is True
        assert claude.root == str(tmp_path / "home")
        assert len(status.one_writer) == 1  # hooks + MCP on one root, named

    def test_a_one_file_client_is_refused_by_name_not_as_usage(
        self, tmp_path: Path
    ) -> None:
        # Codex keeps one config for every project: asked for this directory
        # alone, its MCP half is refused, and says why; the hooks still land.
        (tmp_path / ".codex").mkdir()
        argv = ["--client", "codex", "--level", "project", "--write"]
        code, out, _ = _run(tmp_path, argv, runner=_Cli(tmp_path))
        assert code == 1
        assert "refused: " in out and "keeps one file for every project" in out
        assert "refused: usage" not in out
        assert (_context(tmp_path).cwd / ".codex" / "hooks.json").is_file()

    def test_client_narrows_to_one(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        code, out, _ = _run(tmp_path, ["--client", "opencode", "--json"])
        assert code == 0
        assert [r["client"] for r in json.loads(out)["clients"]] == ["opencode"]


class TestTheRunner:
    def test_off_path_is_none_and_nothing_is_spawned(self, tmp_path: Path) -> None:
        assert _spawn(["claude", "mcp", "list"], {"PATH": str(tmp_path)}) is None
        assert _spawn(["claude", "mcp", "list"], {}) is None

    def test_a_real_process_reports_its_exit_and_stderr(self) -> None:
        bin_dir = str(Path(sys.executable).parent)
        script = "import sys; sys.stderr.write('no'); raise SystemExit(3)"
        argv = [Path(sys.executable).name, "-c", script]
        assert _spawn(argv, {"PATH": bin_dir}) == (3, "no")
