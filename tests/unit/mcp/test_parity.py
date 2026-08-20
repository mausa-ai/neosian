"""Transport parity: the function tool and the MCP path produce the same
texts over identical stores (ledger #50 — one ladder, no drift)."""

from pathlib import Path

import pytest
from mcp.client import Client
from mcp.types import CallToolResult

from neosian._foundation.mcp.server import create_memory_server
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.constants import ErrorMessages

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


@pytest.mark.parametrize("arguments", _CASES, ids=lambda a: str(a))
async def test_both_transports_agree(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    fn_config = await _seed(tmp_path / "fn")
    mcp_config = await _seed(tmp_path / "mcp")

    tool = create_memory_tool(fn_config, actor="mcp")
    fn_result = await tool(**arguments)

    server = await create_memory_server(mcp_config)
    async with Client(server) as client:
        mcp_result = await client.call_tool("memory", arguments)

    assert mcp_result.is_error == (not fn_result.success)
    expected_text = fn_result.data if fn_result.success else fn_result.error
    expected = ["" if expected_text is None else str(expected_text)]
    if fn_result.system_reminder is not None:
        expected.append(fn_result.system_reminder)
    assert _blocks(mcp_result) == expected


async def test_mistyped_value_parity(tmp_path: Path) -> None:
    """A mistyped `insert_line` is a corrective failure on both transports
    (the agent path via tool_exec's TypeError catch, MCP via the handler's),
    formatted by the same constant."""
    config = await _seed(tmp_path / "m")
    arguments = {
        "command": "insert",
        "path": "/memories/seeded",
        "insert_line": "3",
        "insert_text": "t",
    }

    tool = create_memory_tool(config)
    with pytest.raises(TypeError) as excinfo:
        await tool(**arguments)  # tool_exec catches this in the agent loop
    expected = ErrorMessages.TOOL_INVALID_ARGUMENTS.format(
        tool_name="memory", error=excinfo.value
    )

    server = await create_memory_server(config)
    async with Client(server) as client:
        mcp_result = await client.call_tool("memory", arguments)
    assert mcp_result.is_error is True
    assert _blocks(mcp_result) == [expected]
