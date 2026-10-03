"""`neosian continue` — the continue call from the shell, a read (§33)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from neosian._foundation.conversation.cli_continue import run
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import project_scope
from neosian._foundation.memory.sessions import (
    HANDOFF_PATH,
    handoff_document,
    parse_handoff,
    sessions_document,
    sessions_path,
)

SCOPE = "user:me/proj:d"


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


async def _run(argv: list[str], env: dict[str, str] | None = None) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, env or {}, out=out, err=err)
    return _Result(code, out.getvalue(), err.getvalue())


async def _seed(root: Path, scope: str = SCOPE, *, note: bool = True) -> FileStore:
    """One recorded session, listed, and its pending note."""
    store = FileStore(root)
    for text in ("first", "second"):
        await store.append_turn(
            "s1",
            (
                Message(role=Role.USER, content=text),
                Message(role=Role.ASSISTANT, content=f"re: {text}"),
            ),
            actor="claude-code:s1",
        )
    turns = await store.read_turns("s1")
    await store.write(
        scope,
        sessions_path("s1"),
        sessions_document(
            agent="claude-code",
            session_id="s1",
            started=turns[0].created_at,
            last_prompt="second",
            turns=2,
        ),
        actor="claude-code:s1#2",
    )
    if note:
        await store.write(
            scope,
            HANDOFF_PATH,
            handoff_document(
                actor="claude-code:s1",
                written=datetime.now(UTC),
                note="Next: the gates.",
                conversation="s1",
            ),
            actor="claude-code:s1#2",
        )
    return store


class TestHappyPaths:
    async def test_text_is_the_continuation_and_the_note_stays_pending(
        self, tmp_path: Path
    ) -> None:
        store = await _seed(tmp_path)
        result = await _run(["--root", str(tmp_path), "--scope", SCOPE])
        assert result.code == 0, result.err
        assert result.out.startswith(
            "[continuing conversation s1 — written by claude-code:s1, 2 turns"
        )
        assert "[its handoff note, written" in result.out
        assert "[1] USER: first" in result.out and "AGENT: re: second" in result.out
        baton = await store.read(SCOPE, HANDOFF_PATH)
        assert baton is not None and baton.version == 1  # a read: still pending
        parsed = parse_handoff(baton.content)
        assert parsed is not None and parsed.pending

    async def test_a_named_conversation_and_json(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["s1", "--root", str(tmp_path), "--scope", SCOPE, "--json"])
        assert result.code == 0, result.err
        envelope = json.loads(result.out)
        assert envelope["conversation"] == "s1" and envelope["scope"] == SCOPE
        assert envelope["note"] is True and envelope["client"] is None
        assert envelope["text"].startswith("[continuing conversation s1")

    async def test_the_scope_defaults_to_the_directorys_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.chdir(project)
        await _seed(tmp_path / "store", str(project_scope(project)), note=False)
        result = await _run(["--root", str(tmp_path / "store")])
        assert result.code == 0, result.err
        assert result.out.startswith("[continuing conversation s1")
        assert "handoff note" not in result.out
        named = await _run(
            ["--root", str(tmp_path / "store")], {"NEOSIAN_SCOPE": SCOPE}
        )
        assert named.code == 1 and "Nothing to continue" in named.err

    async def test_nothing_to_continue_is_tier_one(self, tmp_path: Path) -> None:
        FileStore(tmp_path)
        result = await _run(["--root", str(tmp_path), "--scope", SCOPE, "--json"])
        assert result.code == 1
        assert result.err.startswith("error: Nothing to continue")
        assert "hint: Name a conversation id" in result.err
        assert json.loads(result.out)["error"].startswith("Nothing to continue")


class TestGrammarTier:
    @pytest.mark.parametrize(
        "argv",
        [
            ["a b"],
            ["--scope", "not a scope"],
            ["--url", "ftp://x"],
        ],
    )
    async def test_exit_two_constructs_nothing(
        self, tmp_path: Path, argv: list[str]
    ) -> None:
        root = tmp_path / "never"
        result = await _run([*argv, "--root", str(root)])
        assert result.code == 2, result.out
        assert not root.exists()


class TestTheDaemonBranch:
    async def test_a_refused_connection_is_tier_one(self) -> None:
        result = await _run(
            ["--url", "http://127.0.0.1:1", "--scope", SCOPE, "--json"],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 1
        assert result.err.startswith("error:")
        assert "error" in json.loads(result.out)
