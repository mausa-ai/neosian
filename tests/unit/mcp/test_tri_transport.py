"""The N4 done-when, MCP's share: one store serves the same memory
through the function tool, the native flag, and an MCP client."""

from mcp.client import Client

from neosian._foundation.mcp.server import create_memory_server
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.tools import (
    NATIVE_MEMORY_TOOL_TYPE,
    create_memory_tool,
)
from neosian._foundation.tools.base import get_tool_definition


async def test_one_store_three_transports(config: MemoryConfig) -> None:
    # 1. The function tool writes.
    plain = create_memory_tool(config)
    created = await plain(command="create", path="/memories/prefs", content="dark mode")
    assert created.success

    # 2. The native-marked tool reads the same bytes (slice A pins that
    #    marked and unmarked execution are identical; here we pin the
    #    marker and the read against this store).
    native = create_memory_tool(config, native=True)
    definition = get_tool_definition(native)
    assert definition is not None
    assert definition.native_type == NATIVE_MEMORY_TOOL_TYPE
    native_view = await native(command="view", path="/memories/prefs")
    assert native_view.success
    assert "dark mode" in str(native_view.data)

    # 3. An MCP client reads the same document and writes a second one.
    server = await create_memory_server(config)
    async with Client(server) as client:
        mcp_view = await client.call_tool(
            "memory", {"command": "view", "path": "/memories/prefs"}
        )
        assert mcp_view.is_error is False
        assert mcp_view.content[0].text == native_view.data  # type: ignore[union-attr]

        mcp_create = await client.call_tool(
            "memory",
            {"command": "create", "path": "/memories/from-mcp", "content": "hi"},
        )
        assert mcp_create.is_error is False

    # 4. The function tool sees the MCP client's write in the index.
    index = await plain(command="view", path="/")
    assert index.success
    assert "prefs" in str(index.data)
    assert "from-mcp" in str(index.data)
