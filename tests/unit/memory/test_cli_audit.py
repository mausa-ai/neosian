"""`neosian audit` — the engine end to end over a FileStore root (§20)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from neosian._foundation.memory.cli_audit import run
from neosian._foundation.memory.file import FileStore


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


async def _run(argv: list[str], env: dict[str, str] | None = None) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, env or {}, out=out, err=err)
    return _Result(code, out.getvalue(), err.getvalue())


async def _seed(root: Path) -> None:
    store = FileStore(root)
    await store.write("user:me", "notes", "x", actor="claude-code:s1")
    await store.redact("user:me", path="notes", actor="cli:local")


class TestHappyPaths:
    async def test_text_lists_newest_first(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["--root", str(tmp_path), "--scope", "user:me"])
        assert result.code == 0, result.err
        lines = result.out.splitlines()
        assert "redacted  1 at /notes" in lines[0] and "cli:local" in lines[0]
        assert (
            "created  /notes v1 (redacted)" in lines[1] and "claude-code:s1" in lines[1]
        )

    async def test_json_is_one_object(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["--root", str(tmp_path), "--scope", "user:me", "--json"])
        assert result.code == 0
        envelope = json.loads(result.out)
        assert envelope["scope"] == "user:me" and envelope["client"] is None
        assert [e["event"] for e in envelope["entries"]] == ["redacted", "created"]
        assert envelope["entries"][1]["created_at"].endswith("Z")

    async def test_filters_ride_through(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(
            [
                "--root",
                str(tmp_path),
                "--scope",
                "user:me",
                "--actor",
                "cli:local",
                "--limit",
                "5",
            ]
        )
        assert result.code == 0
        assert result.out.count("\n") == 1 and "redacted" in result.out

    async def test_an_empty_ledger_is_an_answer(self, tmp_path: Path) -> None:
        result = await _run(["--root", str(tmp_path), "--scope", "user:nobody"])
        assert result.code == 0
        assert "no ledger entries" in result.out


class TestGrammarTier:
    @pytest.mark.parametrize(
        "extra",
        [
            ["--since", "2026-09-02T12:00:00"],  # naive
            ["--since", "yesterday"],
            ["--scope", "nobody"],  # an invalid scope
            ["--limit", "-1"],
            ["--url", "ftp://x"],
        ],
    )
    async def test_bad_invocations_exit_two_with_nothing_built(
        self, tmp_path: Path, extra: list[str]
    ) -> None:
        root = tmp_path / "never"
        argv = ["--root", str(root), "--scope", "user:me", *extra]
        if extra[0] == "--url":
            argv = ["--scope", "user:me", *extra]
        result = await _run(argv, {"NEOSIAN_CLIENT_TOKEN": "t"})
        assert result.code == 2, result.err
        assert not root.exists()

    async def test_url_needs_the_client_token_from_the_environment(self) -> None:
        result = await _run(["--url", "http://127.0.0.1:1", "--scope", "user:me"])
        assert result.code == 2
        assert "NEOSIAN_CLIENT_TOKEN" in result.err

    async def test_since_with_an_offset_parses(self, tmp_path: Path) -> None:
        result = await _run(
            [
                "--root",
                str(tmp_path),
                "--scope",
                "user:me",
                "--since",
                "2026-09-02T12:00:00Z",
            ]
        )
        assert result.code == 0


class TestTheDaemonBranch:
    async def test_url_connects_a_remote_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--url` reaches `RemoteStore.connect` with the URL and the token
        from the environment; the store's client rides the envelope."""
        seen: dict[str, str] = {}
        backing = FileStore(tmp_path)
        await backing.write("user:me", "a", "x", actor="conv:x#1")

        class _Fake:
            client = "claude-code:laptop"
            history = backing.history
            redactions = backing.redactions

            async def aclose(self) -> None:
                seen["closed"] = "yes"

        async def connect(url: str, *, token: str) -> _Fake:
            seen.update(url=url, token=token)
            return _Fake()

        import neosian._foundation.server.remote as remote_module

        monkeypatch.setattr(remote_module.RemoteStore, "connect", connect)
        result = await _run(
            ["--url", "http://127.0.0.1:6367", "--scope", "user:me", "--json"],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 0, result.err
        assert seen == {"url": "http://127.0.0.1:6367", "token": "abc", "closed": "yes"}
        assert json.loads(result.out)["client"] == "claude-code:laptop"

    async def test_a_refused_connection_is_tier_one(self) -> None:
        result = await _run(
            ["--url", "http://127.0.0.1:1", "--scope", "user:me", "--json"],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 1
        assert result.err.startswith("error:")
        assert "error" in json.loads(result.out)
