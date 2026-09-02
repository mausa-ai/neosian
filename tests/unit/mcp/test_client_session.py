"""An in-process MCP client against the memory server: the wire behaves
like the function tool (DESIGN §8, ledger #50/#52)."""

from mcp.client import Client

from neosian._foundation.mcp.server import create_memory_server
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.tools.base import get_tool_definition


class TestListTools:
    async def test_serves_the_definition_verbatim(self, config: MemoryConfig) -> None:
        # The zero-drift pin (ledger #50): name, description and schema are
        # the function tool's own bytes, never re-derived.
        definition = get_tool_definition(create_memory_tool(config))
        assert definition is not None
        server = await create_memory_server(config)
        async with Client(server) as client:
            result = await client.list_tools()
        (tool,) = result.tools
        assert tool.name == definition.name
        assert tool.description == definition.description
        assert tool.input_schema == definition.parameters
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is True

    async def test_instructions_reach_the_client(self, config: MemoryConfig) -> None:
        await config.store.write("user:demo", "prefs", "dark mode")
        server = await create_memory_server(config)
        async with Client(server) as client:
            assert client.instructions is not None
            assert "prefs" in client.instructions


class TestCallTool:
    async def test_all_six_commands_round_trip(self, config: MemoryConfig) -> None:
        server = await create_memory_server(config)
        async with Client(server) as client:
            created = await client.call_tool(
                "memory",
                {"command": "create", "path": "/memories/prefs", "content": "a\nb"},
            )
            assert created.is_error is False

            viewed = await client.call_tool(
                "memory", {"command": "view", "path": "/memories/prefs"}
            )
            assert viewed.is_error is False
            assert "a" in viewed.content[0].text  # type: ignore[union-attr]

            replaced = await client.call_tool(
                "memory",
                {
                    "command": "str_replace",
                    "path": "/memories/prefs",
                    "old_str": "b",
                    "new_str": "c",
                },
            )
            assert replaced.is_error is False

            inserted = await client.call_tool(
                "memory",
                {
                    "command": "insert",
                    "path": "/memories/prefs",
                    "insert_line": 0,
                    "insert_text": "top",
                },
            )
            assert inserted.is_error is False

            renamed = await client.call_tool(
                "memory",
                {
                    "command": "rename",
                    "old_path": "/memories/prefs",
                    "new_path": "/memories/style",
                },
            )
            assert renamed.is_error is False

            deleted = await client.call_tool(
                "memory", {"command": "delete", "path": "/memories/style"}
            )
            assert deleted.is_error is False

        document = await config.store.read("user:demo", "style")
        assert document is None  # deleted; the whole chain really executed

    async def test_corrective_failure_carries_the_hint(
        self, config: MemoryConfig
    ) -> None:
        server = await create_memory_server(config)
        async with Client(server) as client:
            result = await client.call_tool(
                "memory", {"command": "create", "path": "/kb/doc", "content": "x"}
            )
        assert result.is_error is True
        texts = [c.text for c in result.content]  # type: ignore[union-attr]
        assert "[memory_read_only_mount]" in texts[0]
        assert "writable mount" in texts[1]

    async def test_unknown_command_is_corrective(self, config: MemoryConfig) -> None:
        server = await create_memory_server(config)
        async with Client(server) as client:
            result = await client.call_tool("memory", {"command": "update"})
        assert result.is_error is True
        assert "Unknown command 'update'" in result.content[0].text  # type: ignore[union-attr]

    async def test_unknown_tool_is_in_band(self, config: MemoryConfig) -> None:
        server = await create_memory_server(config)
        async with Client(server) as client:
            result = await client.call_tool("recall", {"command": "view"})
        assert result.is_error is True
        assert "Tool 'recall' not found" in result.content[0].text  # type: ignore[union-attr]

    async def test_actor_defaults_to_mcp_stdio_on_version_rows(
        self, config: MemoryConfig
    ) -> None:
        server = await create_memory_server(config)
        async with Client(server) as client:
            await client.call_tool(
                "memory", {"command": "create", "path": "/memories/a", "content": "x"}
            )
        versions = await config.store.versions("user:demo", "a")
        assert versions[0].actor == "mcp:stdio"
