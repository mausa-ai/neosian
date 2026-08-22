"""The `revert` operator verb (DESIGN §14.2, ledger #101). Zero keys.

The shell face of `revert_memory`: `--version` names the row to undo
and must be the newest; reverts append, redacted history refuses. The
`--json` envelope is the receipt's fields — its own shape, never the
six-command `ToolResult` (§14.2).
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
    async def test_version_is_required(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        result = await _run(["revert", "/memories/prefs"], root)
        assert result.code == 2
        assert not root.exists()

    async def test_a_zero_version_constructs_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "r"
        result = await _run(["revert", "/memories/prefs", "--version", "0"], root)
        assert result.code == 2
        assert "--version must be at least 1" in result.err
        assert not root.exists()


class TestKeyless:
    async def test_undo_an_edit_restores_prior_content(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(["revert", "/memories/prefs", "--version", "2"], tmp_path)
        assert result.code == 0
        assert "v1" in result.out  # the restored row is named
        viewed = await _run(["view", "/memories/prefs"], tmp_path)
        assert "likes tea" in viewed.out

    async def test_the_actor_flag_lands_on_the_audit_row(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(
            ["revert", "/memories/prefs", "--version", "2", "--actor", "cli:undo"],
            tmp_path,
        )
        assert result.code == 0
        rows = await _run(["versions", "/memories/prefs", "--json"], tmp_path)
        newest = json.loads(rows.out)["versions"][0]
        assert newest["version"] == 3  # reverts append, never rewrite
        assert newest["actor"] == "cli:undo"


class TestJsonEnvelope:
    async def test_the_receipt_envelope(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(
            ["revert", "/memories/prefs", "--version", "2", "--json"], tmp_path
        )
        assert result.code == 0
        envelope = json.loads(result.out)
        assert envelope["command"] == "revert"
        assert envelope["path"] == "/memories/prefs"
        assert envelope["version"] == 3
        assert envelope["previous_path"] is None
        # The operator envelope, never the six-command ToolResult shape.
        assert "success" not in envelope


class TestFailures:
    async def test_a_stale_version_refuses(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(["revert", "/memories/prefs", "--version", "1"], tmp_path)
        assert result.code == 1
        assert "[memory_conflict]" in result.err
        assert "revert_stale" in result.err

    async def test_redacted_history_refuses(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        redacted = await _run(["redact", "/memories/prefs"], tmp_path)
        assert redacted.code == 0
        result = await _run(["revert", "/memories/prefs", "--version", "2"], tmp_path)
        assert result.code == 1
        assert "redacted" in result.err

    async def test_failure_prints_one_error_object(self, tmp_path: Path) -> None:
        await _seed_history(tmp_path)
        result = await _run(
            ["revert", "/memories/prefs", "--version", "1", "--json"], tmp_path
        )
        assert result.code == 1
        envelope = json.loads(result.out)
        assert "revert_stale" in envelope["error"]
