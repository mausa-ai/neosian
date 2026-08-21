"""The scripted keyless walkthrough — NA's done-when (DESIGN §14.5).

A coding agent with shell access alone can discover (llms.txt), learn
(`neosian docs`), operate memory (all six commands, `--json`), and
offer the MCP upgrade — driven through the LITERAL `neosian` binary,
closing §14.3's honest limit (the process boundary the in-process cli
transport deliberately skips, ledger #78). ~10 interpreter starts,
once per gate.

No skip: if the console script ever stops being installed, this must
go red, not green-by-skip.
"""

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from neosian._foundation.memory.file import FileStore

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
