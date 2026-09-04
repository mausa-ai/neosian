"""`neosian export` / `neosian import` — the engine end to end (§26.4)."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from neosian._foundation.memory.cli_transfer import run
from neosian._foundation.memory.file import FileStore

from .mobility import CONVERSATIONS, SCOPES, assert_indistinguishable, seed

if TYPE_CHECKING:
    from pathlib import Path


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


async def _run(argv: list[str], env: dict[str, str] | None = None) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, env or {}, out=out, err=err)
    return _Result(code, out.getvalue(), err.getvalue())


@pytest.fixture
async def source(tmp_path: Path) -> Path:
    store = FileStore(tmp_path / "source")
    await seed(store, plant_root=store._root)  # noqa: SLF001 — the substrate hook
    return tmp_path / "source"


class TestRoundTrip:
    async def test_export_then_import(self, source: Path, tmp_path: Path) -> None:
        archive, target = tmp_path / "archive", tmp_path / "target"
        exported = await _run(["export", str(archive), "--root", str(source)])
        assert exported.code == 0, exported.err
        lines = exported.out.splitlines()
        assert lines[0].startswith(
            "scope        user:kit  5 documents  13 versions  1 redaction"
        )
        assert lines[-1] == f"exported 2 scopes, 2 conversations to {archive}"
        imported = await _run(["import", str(archive), "--root", str(target)])
        assert imported.code == 0, imported.err
        assert imported.out.splitlines()[-1].startswith(
            "imported 2 scopes, 2 conversations from"
        )
        await assert_indistinguishable(FileStore(source), FileStore(target))

    async def test_json_is_one_object(self, source: Path, tmp_path: Path) -> None:
        result = await _run(
            [
                "export",
                str(tmp_path / "a"),
                "--root",
                str(source),
                "--json",
                "--scope",
                SCOPES[1],
            ]
        )
        assert result.code == 0, result.err
        envelope = json.loads(result.out)
        assert envelope["verb"] == "export" and envelope["client"] is None
        assert envelope["archive"] == str((tmp_path / "a").resolve())
        assert envelope["units"] == [
            {
                "kind": "scope",
                "name": SCOPES[1],
                "documents": 4,
                "versions": 12,
                "redactions": 2,
                "turns": 0,
                "projections": 0,
            }
        ]

    async def test_the_home_is_the_default_store(
        self, source: Path, tmp_path: Path
    ) -> None:
        result = await _run(
            ["export", str(tmp_path / "a"), "--conversation", CONVERSATIONS[0]],
            {"NEOSIAN_HOME": str(source)},
        )
        assert result.code == 0, result.err
        assert result.out.splitlines()[0].startswith(
            "conversation conv-a  3 turns  4 projections"
        )

    async def test_nothing_to_move_is_exit_zero(self, tmp_path: Path) -> None:
        result = await _run(
            ["export", str(tmp_path / "a"), "--root", str(tmp_path / "empty")]
        )
        assert result.code == 0 and result.out == "nothing to export\n"


class TestGrammarTier:
    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["move", "x"],
            ["export"],
            ["export", "x", "--scope", "not a scope"],
            ["export", "x", "--conversation", "a/b"],
            ["export", "x", "--url", "http://h", "--root", "r"],
            ["export", "x", "--schema", "s"],
        ],
    )
    async def test_bad_invocations_exit_two(
        self, argv: list[str], tmp_path: Path
    ) -> None:
        result = await _run(argv)
        assert result.code == 2, result.out
        assert not (tmp_path / "x").exists()

    async def test_import_needs_an_existing_directory(self, tmp_path: Path) -> None:
        result = await _run(
            ["import", str(tmp_path / "missing"), "--root", str(tmp_path / "t")]
        )
        assert result.code == 2
        assert "not a directory" in result.err
        assert not (tmp_path / "t").exists()

    async def test_url_needs_the_client_token(self) -> None:
        result = await _run(["export", "x", "--url", "http://127.0.0.1:6367"])
        assert result.code == 2 and "NEOSIAN_CLIENT_TOKEN" in result.err


class TestTierOne:
    async def test_a_second_import_refuses_with_the_conflict(
        self, source: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "target"
        assert (await _run(["import", str(source), "--root", str(target)])).code == 0
        again = await _run(["import", str(source), "--root", str(target), "--json"])
        assert again.code == 1
        assert "[memory_conflict]" in again.err and "target_occupied" in again.err
        assert json.loads(again.out) == {
            "error": again.err[len("error: ") : -1],
            "hint": None,
        }

    async def test_url_reaches_the_daemon(
        self, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--url` connects a RemoteStore; the store's client rides the envelope."""
        seen: dict[str, str] = {}
        backing = FileStore(tmp_path / "backing")

        class _Fake(FileStore):
            client = "claude-code:laptop"

            async def aclose(self) -> None:
                seen["closed"] = "yes"

        async def connect(url: str, *, token: str) -> _Fake:
            seen.update(url=url, token=token)
            return _Fake(backing._root)  # noqa: SLF001

        import neosian._foundation.server.remote as remote_module

        monkeypatch.setattr(remote_module.RemoteStore, "connect", connect)
        result = await _run(
            ["import", str(source), "--url", "http://127.0.0.1:6367", "--json"],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 0, result.err
        assert seen == {"url": "http://127.0.0.1:6367", "token": "abc", "closed": "yes"}
        assert json.loads(result.out)["client"] == "claude-code:laptop"
        await assert_indistinguishable(FileStore(source), backing)
