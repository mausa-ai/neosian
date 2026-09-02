"""`create_memory_server`: the definition served verbatim, the mapping,
the instructions (DESIGN §8, ledger #50–#52)."""

from mcp.types import CallToolResult, TextContent

from neosian._foundation.mcp.sdk import load_sdk
from neosian._foundation.mcp.server import (
    DEFAULT_ACTOR,
    _to_call_tool_result,
    create_memory_server,
)
from neosian._foundation.memory.index import memory_system_section
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.tools.base import ToolResult


class TestConstruction:
    async def test_name_and_default_actor(self, config: MemoryConfig) -> None:
        server = await create_memory_server(config)
        assert server.name == "neosian-memory"
        assert DEFAULT_ACTOR == "mcp:stdio"

    async def test_instructions_are_the_system_section(
        self, config: MemoryConfig
    ) -> None:
        server = await create_memory_server(config)
        assert server.instructions == await memory_system_section(config)
        assert "memories" in str(server.instructions)

    async def test_lifespan_refreshes_the_instructions(
        self, config: MemoryConfig
    ) -> None:
        # The per-connection render (ledger #51): a document written after
        # construction appears once the SDK enters the lifespan.
        server = await create_memory_server(config)
        before = server.instructions
        await config.store.write("user:demo", "arrived-later", "x")
        async with server.lifespan(server):
            assert server.instructions != before
            assert "arrived-later" in str(server.instructions)


def _texts(result: CallToolResult) -> list[str]:
    return [c.text for c in result.content if isinstance(c, TextContent)]


class TestMapping:
    def test_success_is_one_text_block(self) -> None:
        sdk = load_sdk()
        result = _to_call_tool_result(sdk, ToolResult.ok("the content"))
        assert result.is_error is False
        assert _texts(result) == ["the content"]

    def test_failure_rides_in_band(self) -> None:
        sdk = load_sdk()
        result = _to_call_tool_result(sdk, ToolResult.fail("[code] boom"))
        assert result.is_error is True
        assert _texts(result) == ["[code] boom"]

    def test_system_reminder_is_a_second_block(self) -> None:
        sdk = load_sdk()
        result = _to_call_tool_result(
            sdk, ToolResult.fail("[code] boom", system_reminder="try view /")
        )
        assert _texts(result) == ["[code] boom", "try view /"]

    def test_none_data_is_empty_text(self) -> None:
        sdk = load_sdk()
        result = _to_call_tool_result(sdk, ToolResult.ok(None))
        assert result.is_error is False
        assert _texts(result) == [""]
