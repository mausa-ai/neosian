"""The `neosian memory` grammar: pure parsing, structural validation.

Everything here exercises the argv tier — exit 2, nothing constructed —
through the async engine with injected streams (the same seam the eval
harness uses; the real binary is pinned by the walkthrough).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from neosian._foundation.memory.cli import run
from neosian._foundation.memory.cli_grammar import parse_request as _parse
from neosian._foundation.memory.settings import POSTGRES_DSN_ENV
from neosian._foundation.shared.exceptions import (
    MemoryPathInvalidError,
    MemoryScopeInvalidError,
)

_ENV: dict[str, str] = {}
_DSN_ENV = {POSTGRES_DSN_ENV: "postgresql://localhost/x"}


def _parsed(argv: list[str], env: dict[str, str] | None = None) -> Any:
    return _parse(
        argv,
        env if env is not None else _ENV,
        stdin=io.StringIO(),
        out=io.StringIO(),
        err=io.StringIO(),
        prog="neosian memory",
    )


async def _exit_code(argv: list[str], env: dict[str, str] | None = None) -> int:
    return await run(
        argv,
        env if env is not None else _ENV,
        stdin=io.StringIO(),
        out=io.StringIO(),
        err=io.StringIO(),
    )


_STORE = ["--root", "m", "--scope", "user:me"]


class TestArgumentMapping:
    def test_view_defaults_to_the_index(self) -> None:
        request = _parsed(["view", *_STORE])
        assert request.command == "view"
        assert request.arguments == {"path": "/", "view_range": None}

    def test_view_range_accepts_the_reference_sentinel(self) -> None:
        # END -1 rides argparse's negative-number heuristic — pinned.
        request = _parsed(["view", "/memories/x", "--view-range", "1", "-1", *_STORE])
        assert request.arguments == {"path": "/memories/x", "view_range": [1, -1]}

    def test_create_maps_the_dispatcher_names(self) -> None:
        request = _parsed(["create", "/memories/x", "--content", "hi", *_STORE])
        assert request.arguments == {"path": "/memories/x", "content": "hi"}

    def test_str_replace_maps_the_dispatcher_names(self) -> None:
        request = _parsed(
            ["str_replace", "/memories/x", "--old-str", "a", "--new-str", "b", *_STORE]
        )
        assert request.arguments == {
            "path": "/memories/x",
            "old_str": "a",
            "new_str": "b",
        }

    def test_insert_maps_the_dispatcher_names(self) -> None:
        request = _parsed(
            [
                "insert",
                "/memories/x",
                "--insert-line",
                "3",
                "--insert-text",
                "t",
                *_STORE,
            ]
        )
        assert request.arguments == {
            "path": "/memories/x",
            "insert_line": 3,
            "insert_text": "t",
        }

    def test_rename_takes_two_positionals(self) -> None:
        request = _parsed(["rename", "/memories/a", "/memories/b", *_STORE])
        assert request.arguments == {
            "old_path": "/memories/a",
            "new_path": "/memories/b",
        }

    def test_stdin_dash_reads_the_payload(self) -> None:
        request = _parse(
            ["create", "/memories/x", "--content", "-", *_STORE],
            _ENV,
            stdin=io.StringIO("line1\nline2\n"),
            out=io.StringIO(),
            err=io.StringIO(),
            prog="neosian memory",
        )
        assert request.arguments["content"] == "line1\nline2\n"


class TestStoreFlags:
    def test_default_actor_is_cli(self) -> None:
        request = _parsed(["view", *_STORE])
        assert request.settings.actor == "cli"

    def test_actor_passes_verbatim(self) -> None:
        request = _parsed(["view", *_STORE, "--actor", "cli:claude-code"])
        assert request.settings.actor == "cli:claude-code"

    def test_scope_is_the_memories_sugar(self) -> None:
        (mount,) = _parsed(["view", *_STORE]).settings.mounts
        assert mount.scope == "user:me"
        assert mount.mount_path == "memories"

    def test_mounts_are_repeatable_with_ro(self) -> None:
        request = _parsed(
            [
                "view",
                "--root",
                "m",
                "--mount",
                "scope=user:me,path=memories",
                "--mount",
                "scope=tenant:acme/kb:main,path=kb,ro",
            ]
        )
        assert [m.read_only for m in request.settings.mounts] == [False, True]

    def test_eo_token_parses_edit_only(self) -> None:
        request = _parsed(
            ["view", "--root", "m", "--mount", "scope=user:me/layout:erp,path=fixed,eo"]
        )
        assert request.settings.mounts[0].edit_only is True
        assert request.settings.mounts[0].read_only is False

    def test_ro_and_eo_are_exclusive(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            _parsed(["view", "--root", "m", "--mount", "scope=user:me,path=m,ro,eo"])
        assert excinfo.value.code == 2

    def test_env_dsn_builds_postgres_settings(self) -> None:
        request = _parsed(["view", "--scope", "user:me"], _DSN_ENV)
        assert request.settings.dsn == "postgresql://localhost/x"
        assert request.settings.root is None


class TestArgvTier:
    async def test_missing_command_exits_2(self) -> None:
        assert await _exit_code([]) == 2

    async def test_unknown_command_exits_2(self) -> None:
        assert await _exit_code(["update", "/memories/x", *_STORE]) == 2

    async def test_missing_content_exits_2(self, tmp_path: Path) -> None:
        root = tmp_path / "mem"
        code = await _exit_code(
            ["create", "/memories/x", "--root", str(root), "--scope", "user:me"]
        )
        assert code == 2
        # The argv tier constructs nothing.
        assert not root.exists()

    async def test_help_exits_0(self) -> None:
        out = io.StringIO()
        code = await run(
            ["--help"], _ENV, stdin=io.StringIO(), out=out, err=io.StringIO()
        )
        assert code == 0
        assert "view" in out.getvalue() and "rename" in out.getvalue()

    async def test_root_and_dsn_conflict_exits_2(self) -> None:
        assert await _exit_code(["view", *_STORE], _DSN_ENV) == 2

    async def test_no_store_exits_2(self) -> None:
        assert await _exit_code(["view", "--scope", "user:me"]) == 2

    async def test_schema_with_root_exits_2(self) -> None:
        assert await _exit_code(["view", *_STORE, "--schema", "acme"]) == 2

    async def test_bad_scope_exits_2_with_the_code(self, tmp_path: Path) -> None:
        err = io.StringIO()
        code = await run(
            ["view", "--root", str(tmp_path / "m"), "--scope", "not a scope"],
            _ENV,
            stdin=io.StringIO(),
            out=io.StringIO(),
            err=err,
        )
        assert code == 2
        assert "[memory_scope_invalid]" in err.getvalue()
        assert not (tmp_path / "m").exists()

    def test_bad_scope_raises_structurally_at_the_parse_tier(self) -> None:
        with pytest.raises(MemoryScopeInvalidError):
            _parsed(["view", "--root", "m", "--scope", "not a scope"])

    def test_bad_mount_path_raises_structurally(self) -> None:
        with pytest.raises(MemoryPathInvalidError):
            _parsed(["view", "--root", "m", "--mount", "scope=user:me,path=a/b"])
