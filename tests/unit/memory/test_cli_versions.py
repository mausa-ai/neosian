"""The `versions` operator verb (DESIGN §14.2, NP). Zero keys.

Point-in-time reads exposed: text output is the audit trail without
content; `--json` carries every row's full content. Not a dispatch
command — its envelope is its own, never the `ToolResult` shape.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from neosian._foundation.memory.cli import run

_ENV: dict[str, str] = {}


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err


async def _run(argv: list[str], root: Path) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(
        [*argv, "--root", str(root), "--scope", "user:me"],
        _ENV,
        stdin=io.StringIO(""),
        out=out,
        err=err,
    )
    return _Result(code, out.getvalue(), err.getvalue())


async def _seed_history(root: Path) -> None:
    created = await _run(["create", "/memories/prefs", "--content", "likes tea"], root)
    assert created.code == 0
    edited = await _run(
        ["create", "/memories/prefs", "--content", "likes coffee"], root
    )
    assert edited.code == 0


class TestGrammarTier:
    async def test_a_zero_limit_constructs_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        result = await _run(["versions", "/memories/prefs", "--limit", "0"], root)
        assert result.code == 2
        assert "--limit must be at least 1" in result.err
        assert not root.exists()

    async def test_a_negative_limit_constructs_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        result = await _run(["versions", "/memories/prefs", "--limit", "-1"], root)
        assert result.code == 2
        assert not root.exists()

    async def test_a_missing_path_is_a_grammar_error(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        result = await _run(["versions"], root)
        assert result.code == 2
        assert not root.exists()


class TestTextOutput:
    async def test_rows_newest_first_without_content(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(["versions", "/memories/prefs"], tmp_path)
        assert result.code == 0
        lines = result.out.splitlines()
        assert lines[0].startswith("v2  modified  cli")
        assert lines[1].startswith("v1  created  cli")
        assert "likes tea" not in result.out and "likes coffee" not in result.out

    async def test_limit_keeps_the_most_recent(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(["versions", "/memories/prefs", "--limit", "1"], tmp_path)
        assert result.code == 0
        assert result.out.startswith("v2 ") and "v1 " not in result.out

    async def test_empty_history_is_an_answer_not_an_error(
        self, tmp_path: Path
    ) -> None:
        result = await _run(["versions", "/memories/ghost"], tmp_path)
        assert result.code == 0
        assert "no history for /memories/ghost" in result.out

    async def test_a_mount_root_is_not_a_document(self, tmp_path: Path) -> None:
        result = await _run(["versions", "/memories"], tmp_path)
        assert result.code == 1
        assert "[memory_path_invalid]" in result.err

    async def test_an_unknown_mount_fails_correctively(self, tmp_path: Path) -> None:
        result = await _run(["versions", "/nope/doc"], tmp_path)
        assert result.code == 1
        assert "[memory_path_invalid]" in result.err
        assert "hint:" in result.err


class TestJsonEnvelope:
    async def test_full_rows_including_content(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(["versions", "/memories/prefs", "--json"], tmp_path)
        assert result.code == 0
        envelope = json.loads(result.out)
        assert envelope["path"] == "/memories/prefs"
        rows = envelope["versions"]
        assert [row["version"] for row in rows] == [2, 1]
        assert rows[0]["content"] == "likes coffee"  # point-in-time reads
        assert rows[1]["content"] == "likes tea"
        assert rows[0]["action"] == "modified" and rows[1]["action"] == "created"
        assert all(not row["redacted"] for row in rows)
        # The operator envelope, never the six-command ToolResult shape.
        assert "success" not in envelope

    async def test_failure_prints_one_error_object(self, tmp_path: Path) -> None:
        result = await _run(["versions", "/nope/doc", "--json"], tmp_path)
        assert result.code == 1
        envelope = json.loads(result.out)
        assert "[memory_path_invalid]" in envelope["error"]
