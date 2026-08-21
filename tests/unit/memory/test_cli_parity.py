"""Transport parity: the function tool and the CLI produce the same
envelope over identical stores (ledger #50/#77 — one ladder, one
definition, no drift).

The 14 dispatcher-reachable rows of the MCP parity table are byte-equal
through `--json`; the two grammar-tier rows (missing required flag,
unknown command) fail on both transports, but the CLI answers them at
exit 2 with argparse text — a shell answers grammar before the
dispatcher does. That divergence is §14.1's tiering, not drift.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from neosian._foundation.memory.cli import run
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.settings import format_mount
from neosian._foundation.memory.tools import create_memory_tool

# Description-less: mount descriptions are not expressible in the argv
# token grammar (settings.format_mount documents the drop), so parity is
# measured over the mounts both transports can actually be given.
_MOUNTS = (
    Mount(scope="user:demo", mount_path="memories"),
    Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True),
)

# The MCP parity table's cases (tests/unit/mcp/test_parity.py): happy
# paths for all six commands plus every dispatcher failure class.
_CASES: list[dict[str, Any]] = [
    {"command": "view"},
    {"command": "view", "path": "/memories/seeded"},
    {"command": "view", "path": "/memories/absent"},
    {"command": "view", "path": "/nope/doc"},
    {"command": "create", "path": "/memories/new", "content": "n"},
    {"command": "create", "path": "/kb/doc", "content": "x"},
    {"command": "create", "path": "/memories/new"},  # missing content
    {
        "command": "str_replace",
        "path": "/memories/seeded",
        "old_str": "a",
        "new_str": "z",
    },
    {
        "command": "str_replace",
        "path": "/memories/seeded",
        "old_str": "no",
        "new_str": "z",
    },
    {
        "command": "insert",
        "path": "/memories/seeded",
        "insert_line": 0,
        "insert_text": "t",
    },
    {
        "command": "insert",
        "path": "/memories/seeded",
        "insert_line": 99,
        "insert_text": "t",
    },
    {"command": "delete", "path": "/memories/seeded"},
    {"command": "delete", "path": "/memories/absent"},
    {
        "command": "rename",
        "old_path": "/memories/seeded",
        "new_path": "/memories/moved",
    },
    {
        "command": "rename",
        "old_path": "/memories/seeded",
        "new_path": "/memories/other",
    },
    {"command": "update", "path": "/memories/seeded"},  # unknown command
]

_FLAGS = {
    "content": "--content",
    "old_str": "--old-str",
    "new_str": "--new-str",
    "insert_line": "--insert-line",
    "insert_text": "--insert-text",
}


def _grammar_tier(case: dict[str, Any]) -> bool:
    """Rows the CLI answers at the argv tier (exit 2), not the dispatcher."""
    if case["command"] == "update":
        return True
    return case["command"] == "create" and "content" not in case


def _argv(case: dict[str, Any], root: Path) -> list[str]:
    argv: list[str] = [case["command"]]
    if case["command"] == "rename":
        argv += [case["old_path"], case["new_path"]]
    elif "path" in case:
        argv.append(case["path"])
    for key, flag in _FLAGS.items():
        if key in case:
            argv += [flag, str(case[key])]
    argv += ["--root", str(root), "--actor", "cli"]
    for mount in _MOUNTS:
        argv += ["--mount", format_mount(mount)]
    return [*argv, "--json"]


async def _seed(root: Path) -> MemoryConfig:
    config = MemoryConfig(store=FileStore(root), mounts=_MOUNTS)
    await config.store.write("user:demo", "seeded", "a\nb")
    await config.store.write("user:demo", "other", "occupied")
    return config


@pytest.mark.parametrize("case", _CASES, ids=lambda c: str(c))
async def test_both_transports_agree(tmp_path: Path, case: dict[str, Any]) -> None:
    fn_config = await _seed(tmp_path / "fn")
    await _seed(tmp_path / "cli")

    tool = create_memory_tool(fn_config, actor="cli")
    fn_result = await tool(**case)

    out, err = io.StringIO(), io.StringIO()
    code = await run(
        _argv(case, tmp_path / "cli"),
        {},
        stdin=io.StringIO(),
        out=out,
        err=err,
    )

    if _grammar_tier(case):
        # Both fail; the CLI's answer is the grammar's, at exit 2.
        assert fn_result.success is False
        assert code == 2
        assert out.getvalue() == ""
        text = err.getvalue()
        assert "--content" in text or "invalid choice" in text
        return

    assert code == (0 if fn_result.success else 1)
    assert out.getvalue() == fn_result.to_json() + "\n"
    assert err.getvalue() == ""
