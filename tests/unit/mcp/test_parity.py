"""Transport parity: the function tool and the MCP path produce the same
texts over identical stores (ledger #50 — one ladder, no drift)."""

from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import CallToolResult
from pydantic import ValidationError

from neosian._foundation.conversation.recall import create_recall_any_tool
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.mcp.server import create_memory_server
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import ToolResult, get_tool_metadata
from neosian._foundation.tools.schema import rejection, validate_arguments

_MOUNTS = (
    Mount(scope="user:demo", mount_path="memories", description="user facts"),
    Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True),
)

# One entry per behavior shape: happy paths for all six commands, plus
# every failure class the dispatcher can produce.
_CASES: list[dict[str, object]] = [
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


async def _seed(root: Path) -> MemoryConfig:
    config = MemoryConfig(store=FileStore(root), mounts=_MOUNTS)
    await config.store.write("user:demo", "seeded", "a\nb")
    await config.store.write("user:demo", "other", "occupied")
    return config


def _blocks(result: CallToolResult) -> list[str]:
    return [c.text for c in result.content]  # type: ignore[union-attr]


async def _call(tool: ToolFunction, arguments: dict[str, object]) -> ToolResult[Any]:
    """The agent path in miniature: validate against the tool's own model
    (`agent/tool_exec.py`), then call — the MCP handler does the same."""
    metadata = get_tool_metadata(tool)
    assert metadata is not None and metadata.arguments is not None
    try:
        validated = validate_arguments(metadata.arguments, arguments)
    except ValidationError as exc:
        return rejection(metadata.name, exc)
    return await tool(**validated)


@pytest.mark.parametrize("arguments", _CASES, ids=lambda a: str(a))
async def test_both_transports_agree(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    fn_config = await _seed(tmp_path / "fn")
    mcp_config = await _seed(tmp_path / "mcp")

    fn_result = await _call(create_memory_tool(fn_config, actor="mcp"), arguments)

    server = await create_memory_server(mcp_config)
    async with Client(server) as client:
        mcp_result = await client.call_tool("memory", arguments)

    assert mcp_result.is_error == (not fn_result.success)
    expected_text = fn_result.data if fn_result.success else fn_result.error
    expected = ["" if expected_text is None else str(expected_text)]
    if fn_result.system_reminder is not None:
        expected.append(fn_result.system_reminder)
    assert _blocks(mcp_result) == expected


_RECALL_CASES: list[dict[str, object]] = [
    {"turn": 1, "conversation": "cc-1"},
    {"turn": 0, "conversation": "cc-1"},
    {"turn": 9, "conversation": "cc-1"},
    {"turn": 1, "conversation": "nobody"},
    {"turn": 1, "conversation": "bad/id"},
]


async def _seed_turn(root: Path) -> FileStore:
    store = FileStore(root)
    await store.append_turn(
        "cc-1",
        [
            Message(role=Role.USER, content="hello"),
            Message(role=Role.ASSISTANT, content="hi"),
        ],
        actor="claude-code:cc-1",
    )
    return store


@pytest.mark.parametrize("arguments", _RECALL_CASES, ids=lambda a: str(a))
async def test_recall_turn_agrees_on_both_transports(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    """The MCP twin (§21.7) is the function tool's own bytes: every
    success and every corrective failure, block for block."""
    store = await _seed_turn(tmp_path / "s")
    fn_result = await _call(create_recall_any_tool(store), arguments)

    server = await create_memory_server(
        MemoryConfig(store=store, mounts=_MOUNTS), conversations=store
    )
    async with Client(server) as client:
        mcp_result = await client.call_tool("recall_turn", arguments)

    assert mcp_result.is_error == (not fn_result.success)
    expected_text = fn_result.data if fn_result.success else fn_result.error
    expected = ["" if expected_text is None else str(expected_text)]
    if fn_result.system_reminder is not None:
        expected.append(fn_result.system_reminder)
    assert _blocks(mcp_result) == expected


async def test_a_missing_conversation_is_the_invalid_arguments_text(
    tmp_path: Path,
) -> None:
    """`conversation` is required on the server (ledger #138): omitting it
    is the same corrective text tool_exec produces for the function tool."""
    store = await _seed_turn(tmp_path / "s")
    fn_result = await _call(create_recall_any_tool(store), {"turn": 1})
    assert fn_result.success is False
    assert fn_result.code == "tool_invalid_arguments"
    expected = fn_result.error
    server = await create_memory_server(
        MemoryConfig(store=store, mounts=_MOUNTS), conversations=store
    )
    async with Client(server) as client:
        mcp_result = await client.call_tool("recall_turn", {"turn": 1})
    assert mcp_result.is_error is True
    assert _blocks(mcp_result) == [expected]


async def test_mistyped_value_parity(tmp_path: Path) -> None:
    """A mistyped `insert_line` is the same corrective failure on both
    transports — validation against the one schema (§27.9), not a
    TypeError from inside the command."""
    config = await _seed(tmp_path / "m")
    arguments: dict[str, object] = {
        "command": "insert",
        "path": "/memories/seeded",
        "insert_line": "three",
        "insert_text": "t",
    }

    fn_result = await _call(create_memory_tool(config), arguments)
    assert fn_result.success is False
    assert fn_result.code == "tool_invalid_arguments"
    assert "insert_line" in (fn_result.error or "")
    expected = fn_result.error

    server = await create_memory_server(config)
    async with Client(server) as client:
        mcp_result = await client.call_tool("memory", arguments)
    assert mcp_result.is_error is True
    assert _blocks(mcp_result) == [expected]
