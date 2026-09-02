"""The `neosian memory` engine end to end over a FileStore root.

Exit tiering per DESIGN §14.1: 0 success (data on stdout, hints on
stderr) · 1 corrective dispatch failure (`error:`/`hint:` on stderr;
with --json the envelope carries it) · 2 argv tier (test_cli_grammar).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from neosian._foundation.memory.cli import run
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount

_ENV: dict[str, str] = {}


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err


async def _run(
    argv: list[str], root: Path, *, stdin: str = "", scope: str = "user:me"
) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(
        [*argv, "--root", str(root), "--scope", scope],
        _ENV,
        stdin=io.StringIO(stdin),
        out=out,
        err=err,
    )
    return _Result(code, out.getvalue(), err.getvalue())


class TestHappyPaths:
    async def test_create_then_view_round_trips(self, tmp_path: Path) -> None:
        created = await _run(
            ["create", "/memories/notes", "--content", "Drinks espresso."], tmp_path
        )
        assert created.code == 0
        assert "Created /memories/notes (v1)" in created.out
        viewed = await _run(["view", "/memories/notes"], tmp_path)
        assert viewed.code == 0
        assert "Drinks espresso." in viewed.out

    async def test_view_bare_prints_the_index(self, tmp_path: Path) -> None:
        await _run(["create", "/memories/notes", "--content", "x"], tmp_path)
        index = await _run(["view"], tmp_path)
        assert index.code == 0
        assert "/memories/notes" in index.out

    async def test_str_replace_and_insert(self, tmp_path: Path) -> None:
        await _run(["create", "/memories/n", "--content", "a\nb"], tmp_path)
        replaced = await _run(
            ["str_replace", "/memories/n", "--old-str", "b", "--new-str", "c"], tmp_path
        )
        assert replaced.code == 0
        inserted = await _run(
            ["insert", "/memories/n", "--insert-line", "0", "--insert-text", "top"],
            tmp_path,
        )
        assert inserted.code == 0
        viewed = await _run(["view", "/memories/n"], tmp_path)
        assert "top" in viewed.out and "c" in viewed.out

    async def test_rename_and_delete(self, tmp_path: Path) -> None:
        await _run(["create", "/memories/a", "--content", "x"], tmp_path)
        renamed = await _run(["rename", "/memories/a", "/memories/b"], tmp_path)
        assert renamed.code == 0
        deleted = await _run(["delete", "/memories/b"], tmp_path)
        assert deleted.code == 0
        listing = await _run(["view"], tmp_path)
        assert "/memories/b" not in listing.out

    async def test_heredoc_content_round_trips_byte_exact(self, tmp_path: Path) -> None:
        body = "line one\n\nline three, trailing spaces  \n"
        created = await _run(
            ["create", "/memories/hd", "--content", "-"], tmp_path, stdin=body
        )
        assert created.code == 0
        store = FileStore(tmp_path)
        doc = await store.read("user:me", "hd")
        assert doc is not None and doc.content == body


class TestJsonEnvelope:
    async def test_success_envelope_is_the_tool_result_verbatim(
        self, tmp_path: Path
    ) -> None:
        created = await _run(
            ["create", "/memories/n", "--content", "hi", "--json"], tmp_path / "cli"
        )
        assert created.code == 0
        # The same command through the function transport, on its own root.
        config = MemoryConfig(
            store=FileStore(tmp_path / "fn"),
            mounts=(Mount(scope="user:me", mount_path="memories"),),
        )
        expected = await dispatch(
            config,
            "create",
            {"path": "/memories/n", "content": "hi"},
            actor="cli:local",
        )
        assert created.out == expected.to_json() + "\n"

    async def test_failure_envelope_carries_code_and_hint(self, tmp_path: Path) -> None:
        failed = await _run(["delete", "/memories/nope", "--json"], tmp_path)
        assert failed.code == 1
        payload = json.loads(failed.out)
        assert payload["success"] is False
        assert "error" in payload and "data" not in payload
        assert failed.err == ""

    async def test_json_output_is_exactly_one_line(self, tmp_path: Path) -> None:
        created = await _run(
            ["create", "/memories/n", "--content", "a\nb\nc", "--json"], tmp_path
        )
        assert created.out.endswith("\n")
        assert created.out.count("\n") == 1


class TestFailures:
    async def test_missing_document_exits_1_with_error_and_hint(
        self, tmp_path: Path
    ) -> None:
        failed = await _run(["delete", "/memories/nope"], tmp_path)
        assert failed.code == 1
        assert failed.out == ""
        assert failed.err.startswith("error: ")
        assert "hint: " in failed.err

    async def test_read_only_mount_write_exits_1_with_the_code(
        self, tmp_path: Path
    ) -> None:
        out, err = io.StringIO(), io.StringIO()
        code = await run(
            [
                "create",
                "/kb/x",
                "--content",
                "hi",
                "--root",
                str(tmp_path),
                "--mount",
                "scope=user:me,path=kb,ro",
            ],
            _ENV,
            stdin=io.StringIO(),
            out=out,
            err=err,
        )
        assert code == 1
        assert "[memory_read_only_mount]" in err.getvalue()

    async def test_overwrite_hint_lands_on_stderr(self, tmp_path: Path) -> None:
        await _run(["create", "/memories/n", "--content", "v1"], tmp_path)
        second = await _run(["create", "/memories/n", "--content", "v2"], tmp_path)
        assert second.code == 0
        assert "hint: " in second.err  # the ledger #20 overwrite reminder
        assert "hint" not in second.out


class TestActor:
    async def test_default_actor_cli_local_lands_on_version_rows(
        self, tmp_path: Path
    ) -> None:
        await _run(["create", "/memories/n", "--content", "x"], tmp_path)
        (row,) = await FileStore(tmp_path).versions("user:me", "n")
        assert row.actor == "cli:local"

    async def test_actor_flag_passes_verbatim(self, tmp_path: Path) -> None:
        out, err = io.StringIO(), io.StringIO()
        code = await run(
            [
                "create",
                "/memories/n",
                "--content",
                "x",
                "--root",
                str(tmp_path),
                "--scope",
                "user:me",
                "--actor",
                "cli:walkthrough",
            ],
            _ENV,
            stdin=io.StringIO(),
            out=out,
            err=err,
        )
        assert code == 0
        (row,) = await FileStore(tmp_path).versions("user:me", "n")
        assert row.actor == "cli:walkthrough"
