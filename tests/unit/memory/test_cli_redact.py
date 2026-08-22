"""The `redact` operator verb (DESIGN §14.2, NP). Zero keys.

The one irreversible verb — C3's audit skeleton is deliberately not
restorable — so a whole mount takes an explicit `--all` at the grammar
tier (nothing constructed without it). Document redaction is the
find-and-redact remedy (#102); no `memory_write` event is ever emitted
for it (#99).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from neosian._foundation.memory.cli import run

_ENV: dict[str, str] = {}
_MOUNTS = [
    "--mount",
    "scope=user:me,path=memories",
    "--mount",
    "scope=tenant:acme/kb:main,path=kb,ro",
    "--mount",
    "scope=user:me/layout:erp,path=fixed,eo",
]


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err


async def _run(argv: list[str], root: Path) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(
        [*argv, "--root", str(root), *_MOUNTS],
        _ENV,
        stdin=io.StringIO(""),
        out=out,
        err=err,
    )
    return _Result(code, out.getvalue(), err.getvalue())


class TestGrammarTier:
    async def test_a_mount_root_without_all_constructs_nothing(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "r"
        result = await _run(["redact", "/memories"], root)
        assert result.code == 2
        assert "--all" in result.err
        assert not root.exists()

    async def test_all_with_a_document_path_constructs_nothing(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "r"
        result = await _run(["redact", "/memories/prefs", "--all"], root)
        assert result.code == 2
        assert "mount root" in result.err
        assert not root.exists()


class TestKeyless:
    async def test_a_document_redacts_and_view_shows_it(self, tmp_path: Path) -> None:
        created = await _run(
            ["create", "/memories/secret", "--content", "sensitive"], tmp_path
        )
        assert created.code == 0
        result = await _run(["redact", "/memories/secret"], tmp_path)
        assert result.code == 0
        assert "redacted 1 document at /memories/secret" in result.out
        viewed = await _run(["view", "/memories/secret"], tmp_path)
        assert "redacted" in viewed.out and "sensitive" not in viewed.out
        rows = await _run(["versions", "/memories/secret", "--json"], tmp_path)
        assert json.loads(rows.out)["versions"][0]["content"] == ""

    async def test_scope_wide_names_the_scope_and_counts(self, tmp_path: Path) -> None:
        for doc in ("a", "b"):
            created = await _run(
                ["create", f"/memories/{doc}", "--content", "x"], tmp_path
            )
            assert created.code == 0
        result = await _run(["redact", "/memories", "--all"], tmp_path)
        assert result.code == 0
        assert "redacted 2 documents in scope 'user:me' (mount /memories)" in result.out

    async def test_redact_is_idempotent(self, tmp_path: Path) -> None:
        created = await _run(["create", "/memories/a", "--content", "x"], tmp_path)
        assert created.code == 0
        first = await _run(["redact", "/memories/a"], tmp_path)
        second = await _run(["redact", "/memories/a"], tmp_path)
        assert first.code == 0 and second.code == 0
        assert "redacted 1 document" in second.out

    async def test_a_read_only_mount_refuses(self, tmp_path: Path) -> None:
        result = await _run(["redact", "/kb/doc"], tmp_path)
        assert result.code == 1
        assert "[memory_read_only_mount]" in result.err

    async def test_an_edit_only_mount_allows_redaction(self, tmp_path: Path) -> None:
        """Clearing content is a content act: the document survives in
        listings, so the fixed document set is untouched."""
        # Plant on the eo mount through its own scope: create-new is
        # refused there, so seed it as the operator would — pre-created.
        from neosian._foundation.memory.file import FileStore

        store = FileStore(tmp_path)
        await store.write("user:me/layout:erp", "notes", "template", actor="operator")
        result = await _run(["redact", "/fixed/notes"], tmp_path)
        assert result.code == 0
        assert "redacted 1 document at /fixed/notes" in result.out
        listed = await _run(["view", "/fixed"], tmp_path)
        assert "/fixed/notes" in listed.out  # still in the set


class TestJsonEnvelope:
    async def test_the_document_envelope(self, tmp_path: Path) -> None:
        created = await _run(["create", "/memories/a", "--content", "x"], tmp_path)
        assert created.code == 0
        result = await _run(["redact", "/memories/a", "--json"], tmp_path)
        assert result.code == 0
        assert json.loads(result.out) == {
            "path": "/memories/a",
            "scope_wide": False,
            "matched": 1,
        }

    async def test_failure_prints_one_error_object(self, tmp_path: Path) -> None:
        result = await _run(["redact", "/kb/doc", "--json"], tmp_path)
        assert result.code == 1
        envelope = json.loads(result.out)
        assert "[memory_read_only_mount]" in envelope["error"]
        assert envelope["hint"]
