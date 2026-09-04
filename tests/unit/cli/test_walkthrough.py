"""The scripted keyless walkthrough — NA's done-when (DESIGN §14.5).

A coding agent with shell access alone can discover (llms.txt), learn
(`neosian docs`), operate memory (all six commands, `--json`, and the
NP redaction leg over the operator verbs), offer the MCP upgrade, and
record a foreign agent's session from replayed hook payloads (NL) —
driven through the LITERAL `neosian` binary, closing §14.3's honest
limit (the process boundary the in-process cli transport deliberately
skips, ledger #78). ~25 interpreter starts, once per gate.

No skip: if the console script ever stops being installed, this must
go red, not green-by-skip.
"""

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from neosian._foundation.memory.file import FileStore
from tests.unit.record.payloads import SESSION, prompt, session_start, stop, tool

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _binary() -> Path:
    name = "neosian.exe" if sys.platform == "win32" else "neosian"
    candidate = Path(sys.executable).parent / name
    assert candidate.is_file(), f"console script missing: {candidate} (run `uv sync`)"
    return candidate


def _env(home: Path) -> dict[str, str]:
    # Copy-and-delete, never a fresh dict (the launcher needs the rest);
    # HOME is overridden so install can never touch the developer's real
    # client configs.
    env = {k: v for k, v in os.environ.items() if k != "NEOSIAN_POSTGRES_DSN"}
    env["HOME"] = str(home)
    env["NEOSIAN_HOME"] = str(home / "home")  # the default store (DESIGN §22)
    env["NO_COLOR"] = "1"
    return env


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_binary()), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=dict(env),
        timeout=60,
        check=False,
    )


def _store_flags(root: Path) -> list[str]:
    return [
        "--root",
        str(root),
        "--scope",
        "user:walkthrough",
        "--actor",
        "cli:walkthrough",
    ]


class TestDiscover:
    def test_the_repo_root_llms_txt_opens_the_doors(self) -> None:
        text = (_REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
        assert text.startswith("# neosian\n\n> ")
        assert "neosian docs" in text
        assert "neosian memory" in text
        assert "neosian mcp install" in text


class TestLearn:
    def test_docs_lists_the_topics(self, tmp_path: Path) -> None:
        result = _run(["docs"], cwd=tmp_path, env=_env(tmp_path))
        assert result.returncode == 0
        assert "topology" in result.stdout
        assert "hint:" in result.stderr
        assert "hint:" not in result.stdout

    def test_the_topology_page_carries_the_rules(self, tmp_path: Path) -> None:
        result = _run(["docs", "topology"], cwd=tmp_path, env=_env(tmp_path))
        assert result.returncode == 0
        assert "who runs neosian code" in result.stdout
        assert "where the bytes live" in result.stdout
        assert "one writer at a time" in result.stdout

    def test_an_unknown_topic_exits_2(self, tmp_path: Path) -> None:
        result = _run(["docs", "nope"], cwd=tmp_path, env=_env(tmp_path))
        assert result.returncode == 2
        assert result.stdout == ""
        assert "error:" in result.stderr
        assert "topology" in result.stderr


class TestOperate:
    async def test_the_six_commands_over_one_root(self, tmp_path: Path) -> None:
        env = _env(tmp_path)
        root = tmp_path / "mem"
        flags = _store_flags(root)

        # create — the payload through a real pipe (the one thing the
        # in-process transport cannot prove).
        created = _run(
            ["memory", "create", "/memories/notes", "--content", "-", *flags],
            cwd=tmp_path,
            env=env,
            stdin="User prefers concise answers.\n",
        )
        assert created.returncode == 0, created.stderr

        index = _run(["memory", "view", "/", *flags], cwd=tmp_path, env=env)
        assert index.returncode == 0
        assert "notes" in index.stdout

        replaced = _run(
            [
                "memory",
                "str_replace",
                "/memories/notes",
                "--old-str",
                "concise",
                "--new-str",
                "terse",
                *flags,
            ],
            cwd=tmp_path,
            env=env,
        )
        assert replaced.returncode == 0, replaced.stderr

        inserted = _run(
            [
                "memory",
                "insert",
                "/memories/notes",
                "--insert-line",
                "0",
                "--insert-text",
                "Confirmed by the user.",
                *flags,
            ],
            cwd=tmp_path,
            env=env,
        )
        assert inserted.returncode == 0, inserted.stderr

        viewed = _run(
            ["memory", "view", "/memories/notes", *flags], cwd=tmp_path, env=env
        )
        assert viewed.returncode == 0
        assert "terse" in viewed.stdout
        assert "Confirmed by the user." in viewed.stdout

        renamed = _run(
            ["memory", "rename", "/memories/notes", "/memories/prefs", *flags],
            cwd=tmp_path,
            env=env,
        )
        assert renamed.returncode == 0, renamed.stderr

        deleted = _run(
            ["memory", "delete", "/memories/prefs", *flags], cwd=tmp_path, env=env
        )
        assert deleted.returncode == 0, deleted.stderr

        gone = _run(["memory", "view", "/", *flags], cwd=tmp_path, env=env)
        assert gone.returncode == 0
        assert "notes" not in gone.stdout
        assert "prefs" not in gone.stdout

        # Store truth across the process boundary: the version rows carry
        # the actor the argv named.
        keep = _run(
            ["memory", "create", "/memories/keep", "--content", "kept", *flags],
            cwd=tmp_path,
            env=env,
        )
        assert keep.returncode == 0
        (row,) = await FileStore(root).versions("user:walkthrough", "keep")
        assert row.actor == "cli:walkthrough"

        # NL: the ledger from the shell — the same actor, newest first.
        ledger = _run(
            ["audit", "--scope", "user:walkthrough", "--root", str(root), "--json"],
            cwd=tmp_path,
            env=env,
        )
        assert ledger.returncode == 0, ledger.stderr
        entries = json.loads(ledger.stdout)["entries"]
        assert entries[0]["path"] == "keep" and entries[0]["actor"] == "cli:walkthrough"
        assert {e["event"] for e in entries} >= {"created", "deleted"}

    def test_redaction_runs_end_to_end(self, tmp_path: Path) -> None:
        """The NP done-when's second leg: find (versions) → erase
        (redact) → the store shows the skeleton and refuses the undo —
        through the literal binary."""
        env = _env(tmp_path)
        flags = _store_flags(tmp_path / "mem")

        created = _run(
            ["memory", "create", "/memories/leak", "--content", "sensitive", *flags],
            cwd=tmp_path,
            env=env,
        )
        assert created.returncode == 0, created.stderr

        rows = _run(
            ["memory", "versions", "/memories/leak", *flags], cwd=tmp_path, env=env
        )
        assert rows.returncode == 0
        assert rows.stdout.startswith("v1  created  cli:walkthrough")
        assert "sensitive" not in rows.stdout  # text output carries no content

        redacted = _run(
            ["memory", "redact", "/memories/leak", *flags], cwd=tmp_path, env=env
        )
        assert redacted.returncode == 0, redacted.stderr
        assert "redacted 1 document" in redacted.stdout

        viewed = _run(
            ["memory", "view", "/memories/leak", *flags], cwd=tmp_path, env=env
        )
        assert viewed.returncode == 0
        assert "redacted" in viewed.stdout and "sensitive" not in viewed.stdout

        history = _run(
            ["memory", "versions", "/memories/leak", "--json", *flags],
            cwd=tmp_path,
            env=env,
        )
        assert history.returncode == 0
        payload = json.loads(history.stdout)
        assert payload["versions"][0]["redacted"] is True
        assert payload["versions"][0]["content"] == ""  # skeleton, no bytes

        undo = _run(
            ["memory", "revert", "/memories/leak", "--version", "1", *flags],
            cwd=tmp_path,
            env=env,
        )
        assert undo.returncode == 1  # redaction is not restorable (C3)
        assert "redacted" in undo.stderr

    def test_the_json_envelope_parses(self, tmp_path: Path) -> None:
        env = _env(tmp_path)
        root = tmp_path / "mem"
        result = _run(
            [
                "memory",
                "create",
                "/memories/j",
                "--content",
                "x",
                "--json",
                *_store_flags(root),
            ],
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.count("\n") == 1
        payload = json.loads(result.stdout)
        assert payload["success"] is True

    def test_a_grammar_error_exits_2(self, tmp_path: Path) -> None:
        result = _run(
            ["memory", "create", "/memories/n", *_store_flags(tmp_path / "mem")],
            cwd=tmp_path,
            env=_env(tmp_path),
        )
        assert result.returncode == 2
        assert result.stdout == ""
        assert "--content" in result.stderr

    def test_the_dsn_conflict_exits_2(self, tmp_path: Path) -> None:
        # The one test that SETS the DSN: proves the entry point reads
        # the real environment across the process boundary.
        env = _env(tmp_path)
        env["NEOSIAN_POSTGRES_DSN"] = "postgresql://bogus/never-connected"
        result = _run(
            [
                "memory",
                "view",
                "/",
                "--root",
                str(tmp_path / "mem"),
                "--scope",
                "user:walkthrough",
            ],
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 2
        assert "NEOSIAN_POSTGRES_DSN" in result.stderr


class TestUpgrade:
    def test_install_prints_the_registration(self, tmp_path: Path) -> None:
        env = _env(tmp_path)
        (tmp_path / ".claude").mkdir()  # HOME is tmp_path — install evidence
        result = _run(
            [
                "mcp",
                "install",
                "--client",
                "claude-code",
                "--root",
                str(tmp_path / "mem"),
                "--scope",
                "user:walkthrough",
            ],
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        fragment = json.loads(result.stdout)  # stdout is valid JSON alone
        entry = fragment["mcpServers"]["neosian-memory"]
        assert entry["args"][:2] == ["-m", "neosian.mcp"]
        assert not (tmp_path / ".mcp.json").exists()  # print mode writes nothing

    def test_no_flags_render_the_home_and_the_project_layout(
        self, tmp_path: Path
    ) -> None:
        """DESIGN §22: a fresh machine, no store flags — the registration
        names the home and this directory's two-mount layout, visibly."""
        env = _env(tmp_path)
        (tmp_path / ".claude").mkdir()
        project = tmp_path / "demo proj"
        project.mkdir()
        result = _run(
            ["mcp", "install", "--client", "claude-code"], cwd=project, env=env
        )
        assert result.returncode == 0, result.stderr
        args = json.loads(result.stdout)["mcpServers"]["neosian-memory"]["args"]
        assert args[args.index("--root") + 1] == str(tmp_path / "home")
        tokens = [args[i + 1] for i, a in enumerate(args) if a == "--mount"]
        assert [t.split(",")[1] for t in tokens] == ["path=user", "path=project"]
        assert tokens[1].split(",")[0].endswith("/proj:demo-proj")
        hooks = _run(
            ["record", "install", "--client", "claude-code"], cwd=project, env=env
        )
        assert hooks.returncode == 0, hooks.stderr
        assert "/proj:demo-proj,path=project" in hooks.stdout
        assert str(tmp_path / "home" / "spool") in hooks.stdout
        # The shell itself keeps the scope the caller's: the refusal shows
        # this directory's spelling instead of deciding it.
        bare = _run(["memory", "view", "/"], cwd=project, env=env)
        assert bare.returncode == 2
        assert "this directory's layout" in bare.stderr
        assert "/proj:demo-proj,path=project" in bare.stderr
        assert not (tmp_path / "home").exists()  # nothing built on the tier

    def test_install_refuses_a_missing_client_dir(self, tmp_path: Path) -> None:
        env = _env(tmp_path)  # empty fake HOME — Cursor is "not installed"
        result = _run(
            [
                "mcp",
                "install",
                "--client",
                "cursor",
                "--root",
                str(tmp_path / "mem"),
                "--scope",
                "user:walkthrough",
            ],
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 1
        assert "error:" in result.stderr
        assert not (tmp_path / ".cursor").exists()  # never created


class TestRecord:
    """NL slice B: a foreign agent's hooks, replayed through the literal
    binary — the span lands as one turn with the foreign actor, the
    sessions document appears in the index, the ledger names both."""

    def test_replayed_hooks_land_a_turn_and_a_sessions_document(
        self, tmp_path: Path
    ) -> None:
        env = _env(tmp_path)
        root = tmp_path / "mem"
        flags = ["--root", str(root), "--scope", "user:walkthrough"]
        argv = ["record", *flags, "--spool", str(tmp_path / "spool")]
        for payload in (prompt("hello"), tool(), stop("done")):
            result = _run(argv, cwd=tmp_path, env=env, stdin=json.dumps(payload))
            assert result.returncode == 0, result.stderr
            assert result.stdout == ""  # silent: a hook's stdout is injected
        # NB slice B: the next session's SessionStart reads it back — the
        # index and "where we left off" are the hook's stdout, the context.
        started = _run(
            argv, cwd=tmp_path, env=env, stdin=json.dumps(session_start("startup"))
        )
        assert started.returncode == 0, started.stderr
        assert f"/memories/sessions/{SESSION}" in started.stdout
        assert "[1] USER: hello" in started.stdout

        ledger = _run(
            ["audit", "--scope", "user:walkthrough", "--root", str(root), "--json"],
            cwd=tmp_path,
            env=env,
        )
        assert ledger.returncode == 0, ledger.stderr
        (entry,) = json.loads(ledger.stdout)["entries"]
        assert entry["path"] == f"sessions/{SESSION}"
        assert entry["actor"] == f"claude-code:{SESSION}#1"
        turns = _run(
            [
                "audit",
                "--scope",
                "user:walkthrough",
                "--root",
                str(root),
                "--conversation",
                SESSION,
                "--json",
            ],
            cwd=tmp_path,
            env=env,
        )
        events = [e["event"] for e in json.loads(turns.stdout)["entries"]]
        assert events == ["created", "turn"]

        index = _run(["memory", "view", "/", *flags], cwd=tmp_path, env=env)
        assert index.returncode == 0
        assert f"sessions/{SESSION}" in index.stdout

    @pytest.mark.parametrize(
        ("client", "evidence"),
        [
            ("claude-code", ".claude"),
            ("codex", ".codex"),
            ("opencode", ".config/opencode"),
        ],
    )
    def test_install_prints_the_hooks(
        self, tmp_path: Path, client: str, evidence: str
    ) -> None:
        env = _env(tmp_path)
        (tmp_path / evidence).mkdir(parents=True)  # HOME is tmp_path — evidence
        result = _run(
            [
                "record",
                "install",
                "--client",
                client,
                "--root",
                str(tmp_path / "mem"),
                "--scope",
                "user:walkthrough",
            ],
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        if client == "opencode":
            assert "export const NeosianRecord" in result.stdout
            assert not (tmp_path / ".opencode").exists()  # print mode
            return
        hooks = json.loads(result.stdout)["hooks"]
        assert set(hooks) == {"UserPromptSubmit", "PostToolUse", "Stop", "SessionStart"}
        assert "-m neosian.record" in hooks["Stop"][0]["hooks"][0]["command"]
        assert not (tmp_path / ".claude" / "settings.json").exists()  # print mode
        assert not (tmp_path / ".codex" / "hooks.json").exists()

    def test_codex_mcp_install_prints_toml(self, tmp_path: Path) -> None:
        import tomllib

        env = _env(tmp_path)
        (tmp_path / ".codex").mkdir()
        result = _run(
            [
                "mcp",
                "install",
                "--client",
                "codex",
                "--root",
                str(tmp_path / "mem"),
                "--scope",
                "user:walkthrough",
            ],
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert tomllib.loads(result.stdout)["mcp_servers"]["neosian-memory"]["args"][
            :2
        ] == ["-m", "neosian.mcp"]
        assert "codex mcp add neosian-memory --" in result.stderr
