"""`McpServer` — an MCP server consumed as tools (NC1, DESIGN §25).

The done-when, keyless: a neosian agent calls a real MCP server's tool —
the in-process memory server over the official client, on both paths,
and the stdio entry point as a real subprocess. Around it: the mapping
(ledger #52 read from the client side), the verbatim schema, the
prefix, the collision message, the connection error, the lifetime, and
the gate and hooks applying unchanged — a bridged tool is an ordinary
tool.
"""

import inspect
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
)

from neosian import (
    ERROR_CODES,
    Agent,
    AgentConfig,
    AgentHooks,
    ConfigurationError,
    McpConnectionError,
    Model,
    ToolDecision,
    ToolGateConfig,
)
from neosian._foundation.agent.events import DoneEvent, ToolResultEvent
from neosian._foundation.agent.hooks import ToolEvent
from neosian._foundation.agent.tool_exec import execute_tool
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.types import (
    ToolCallId,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult, get_tool_definition
from neosian.mcp import McpServer, create_memory_server

_SYSTEM = "You are a test agent."
_USER = [Message(role=Role.USER, content="go")]
_CREATE = {"command": "create", "path": "/memories/prefs", "content": "Espresso only."}
_BLOCKS: list[Any] = [
    TextContent(type="text", text="caption"),
    ImageContent(type="image", data="aGk=", mime_type="image/png"),
    AudioContent(type="audio", data="aGk=", mime_type="audio/wav"),
    ResourceLink(
        type="resource_link", name="r", uri="file:///x", mime_type="text/plain", size=12
    ),
    EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri="file:///b", mime_type="application/octet-stream", blob="aGk="
        ),
    ),
]


def _probe() -> tuple[MCPServer, list[dict[str, Any]]]:
    """A small SDK server; `calls` records every `add` the server ran."""
    server = MCPServer("probe")
    calls: list[dict[str, Any]] = []

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        calls.append({"a": a, "b": b})
        return a + b

    @server.tool(structured_output=False)
    def media() -> list[Any]:
        return _BLOCKS

    return server, calls


def _by_name(tools: Sequence[ToolFunction]) -> dict[str, ToolFunction]:
    named = {}
    for tool in tools:
        definition = get_tool_definition(tool)
        assert definition is not None
        named[str(definition.name)] = tool
    return named


def _script(name: str, arguments: dict[str, Any]) -> FakeScript:
    return FakeScript(
        turns=(
            FakeTurn(
                tool_calls=(
                    ToolCall(
                        id=ToolCallId("c1"), name=ToolName(name), arguments=arguments
                    ),
                )
            ),
            FakeTurn(content="finished"),
        )
    )


def _agent(
    tools: Sequence[ToolFunction],
    script: FakeScript,
    *,
    gate: ToolGateConfig | None = None,
    hooks: AgentHooks | None = None,
) -> Agent:
    fake = FakeClient(script)
    return Agent(
        AgentConfig(
            system_prompt=_SYSTEM,
            model=Model.FAKE,
            enable_todo=False,
            tools=list(tools),
            tool_gate=gate,
            hooks=hooks,
            client_factory=lambda _: fake,
        )
    )


@pytest.mark.unit
class TestDoneWhen:
    """A neosian agent calls the real memory server's tool, keylessly."""

    async def test_blocking(self, config: MemoryConfig, store: FileStore) -> None:
        server = await create_memory_server(config)
        async with McpServer.in_process(server) as s:
            response = await _agent(s.tools, _script("memory", _CREATE)).run(
                _USER, stream=False
            )
        assert response.message.content == "finished"
        assert [c.name for c in response.tool_calls_made] == ["memory"]
        doc = await store.read("user:demo", "prefs")
        assert doc is not None and doc.content == "Espresso only."

    async def test_streaming(self, config: MemoryConfig, store: FileStore) -> None:
        server = await create_memory_server(config)
        async with McpServer.in_process(server) as s:
            agent = _agent(s.tools, _script("memory", _CREATE))
            events = [e async for e in await agent.run(_USER, stream=True)]
        results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(results) == 1 and results[0].success
        assert isinstance(events[-1], DoneEvent)
        assert await store.read("user:demo", "prefs") is not None

    async def test_real_stdio_server(self, tmp_path: Path) -> None:
        """The stdio entry point as a subprocess — the same tools, the
        same store, through pipes."""
        root = tmp_path / "store"
        args = ["-m", "neosian.mcp", "--root", str(root), "--scope", "user:demo"]
        async with McpServer.stdio(sys.executable, args) as s:
            assert s.name == Path(sys.executable).name
            assert list(_by_name(s.tools)) == [
                "memory",
                "list_skills",
                "load_skill",
                "recall_turn",
            ]
            create = {"command": "create", "path": "/memories/a", "content": "b"}
            response = await _agent(s.tools, _script("memory", create)).run(
                _USER, stream=False
            )
        assert response.message.content == "finished"
        doc = await FileStore(root).read("user:demo", "a")
        assert doc is not None and doc.content == "b"


@pytest.mark.unit
class TestBridge:
    async def test_definitions_are_the_servers_verbatim(
        self, config: MemoryConfig
    ) -> None:
        server = await create_memory_server(config)
        async with McpServer.in_process(server) as s:
            bridged = get_tool_definition(_by_name(s.tools)["memory"])
        assert bridged is not None
        own = get_tool_definition(create_memory_tool(config))
        assert own is not None
        assert bridged.name == own.name
        assert bridged.description == own.description
        assert bridged.parameters == own.parameters

    async def test_is_error_becomes_a_failure(self, config: MemoryConfig) -> None:
        """The server's two text blocks — the error and its reminder —
        join into one in-band failure, as any client sees them."""
        server = await create_memory_server(config)
        async with McpServer.in_process(server) as s:
            memory = _by_name(s.tools)["memory"]
            result = await memory(command="create", path="/kb/new", content="x")
        assert not result.success and result.error is not None
        assert "[memory_read_only_mount]" in result.error
        assert "\n" in result.error
        assert result.system_reminder is None

    async def test_structured_content_is_the_data(self) -> None:
        server, _ = _probe()
        async with McpServer.in_process(server) as s:
            result = await _by_name(s.tools)["add"](a=1, b=2)
        assert result == ToolResult.ok({"result": 3})

    async def test_media_blocks_leave_markers(self) -> None:
        server, _ = _probe()
        async with McpServer.in_process(server) as s:
            result = await _by_name(s.tools)["media"]()
        assert result.success
        assert result.data == "\n".join(
            [
                "caption",
                "[image image/png 4 chars base64]",
                "[audio audio/wav 4 chars base64]",
                "[resource_link file:///x text/plain 12 bytes]",
                "[resource file:///b application/octet-stream 4 chars base64]",
            ]
        )

    async def test_arguments_cross_verbatim(self) -> None:
        """No local binding check — the server validates, in-band."""
        server, calls = _probe()
        async with McpServer.in_process(server) as s:
            add = _by_name(s.tools)["add"]
            inspect.signature(add).bind(anything=1)
            result = await add(a="x", b=2)
        assert not result.success and result.error is not None
        assert "valid integer" in result.error
        assert calls == []

    async def test_prefix(self) -> None:
        server, calls = _probe()
        async with McpServer.in_process(server, prefix="p") as s:
            assert s.prefix == "p"
            named = _by_name(s.tools)
            assert list(named) == ["p__add", "p__media"]
            assert (await named["p__add"](a=2, b=3)).data == {"result": 5}
        assert calls == [{"a": 2, "b": 3}]

    async def test_names(self, config: MemoryConfig) -> None:
        server = await create_memory_server(config)
        assert McpServer.in_process(server).name == "neosian-memory"
        assert McpServer.in_process(server, name="mem").name == "mem"
        assert McpServer.stdio("/usr/bin/python3", ["-m", "x"]).name == "python3"
        assert McpServer.http("http://localhost:8765/mcp").name == (
            "http://localhost:8765/mcp"
        )


@pytest.mark.unit
class TestCollisions:
    async def test_the_message_names_the_server(self) -> None:
        @Tool(name=ToolName("add"), description="local add")
        async def add(a: int, b: int) -> ToolResult[int]:
            return ToolResult.ok(a + b)

        server, _ = _probe()
        async with McpServer.in_process(server) as s:
            with pytest.raises(ConfigurationError, match="MCP server 'probe'"):
                _agent([*s.tools, add], _script("add", {}))
            with pytest.raises(ConfigurationError, match="test_client"):
                _agent([add, *s.tools], _script("add", {}))
        async with McpServer.in_process(server, prefix="p") as s:
            agent = _agent([add, *s.tools], _script("p__add", {"a": 1, "b": 1}))
            response = await agent.run(_USER, stream=False)
        assert response.message.content == "finished"


@pytest.mark.unit
class TestConnection:
    async def test_a_missing_command(self) -> None:
        with pytest.raises(McpConnectionError) as info:
            async with McpServer.stdio("definitely-not-a-binary-xyz"):
                pass
        error = info.value
        assert error.code == "tool_mcp_connection_failed"
        assert error.server == "definitely-not-a-binary-xyz"
        assert error.details is not None
        assert error.details["server"] == "definitely-not-a-binary-xyz"
        assert "definitely-not-a-binary-xyz" in str(error)
        assert ERROR_CODES["tool_mcp_connection_failed"] is McpConnectionError

    async def test_a_failing_handshake_names_its_cause(self) -> None:
        """A lifespan that raises arrives as a task group; the error
        names the one leaf, not the group."""

        @asynccontextmanager
        async def lifespan(server: MCPServer) -> AsyncIterator[None]:
            if server.name == "bad":
                raise OSError("no db")
            yield

        bad = MCPServer("bad", lifespan=lifespan)
        with pytest.raises(
            McpConnectionError, match="'bad' could not be connected: no db"
        ):
            async with McpServer.in_process(bad):
                pass

    async def test_the_extra_is_named_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("mcp", "mcp.client", "mcp.client.stdio"):
            monkeypatch.setitem(sys.modules, name, None)
        with pytest.raises(ImportError, match=r"neosian\[mcp\]"):
            async with McpServer.stdio("anything"):
                pass


@pytest.mark.unit
class TestLifetime:
    async def test_tools_need_a_connection(self) -> None:
        server, _ = _probe()
        s = McpServer.in_process(server)
        with pytest.raises(RuntimeError, match="not connected"):
            _ = s.tools

    async def test_a_captured_tool_fails_in_band_after_exit(self) -> None:
        server, _ = _probe()
        async with McpServer.in_process(server) as s:
            add = _by_name(s.tools)["add"]
        agent = _agent([add], _script("add", {"a": 1, "b": 1}))
        call = ToolCall(
            id=ToolCallId("c1"), name=ToolName("add"), arguments={"a": 1, "b": 1}
        )
        result = await execute_tool(agent, call)
        assert not result.success and result.error is not None
        assert "MCP server 'probe' is disconnected" in result.error

    async def test_reentry_and_body_exceptions(self) -> None:
        """A body exception propagates as itself — never wrapped in the
        SDK's task group — and the server can be entered again."""
        server, calls = _probe()
        with pytest.raises(KeyError):
            async with McpServer.in_process(server):
                raise KeyError("body")
        s = McpServer.in_process(server)
        async with s:
            await _by_name(s.tools)["add"](a=1, b=1)
        async with s:
            await _by_name(s.tools)["add"](a=2, b=2)
        assert calls == [{"a": 1, "b": 1}, {"a": 2, "b": 2}]


@pytest.mark.unit
class TestSeamsUnchanged:
    async def test_the_gate_denies_before_the_wire(self) -> None:
        server, calls = _probe()

        def deny(_request: object) -> ToolDecision:
            return ToolDecision(approved=False, reason="not today")

        async with McpServer.in_process(server) as s:
            agent = _agent(
                s.tools,
                _script("add", {"a": 1, "b": 1}),
                gate=ToolGateConfig(approver=deny),
            )
            events = [e async for e in await agent.run(_USER, stream=True)]
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert not result.success and result.error is not None
        assert "not today" in result.error
        assert calls == []

    async def test_hooks_see_the_bridged_call(self) -> None:
        server, _ = _probe()
        seen: list[ToolEvent] = []

        async def on_tool(event: ToolEvent) -> None:
            seen.append(event)

        async with McpServer.in_process(server) as s:
            agent = _agent(
                s.tools,
                _script("add", {"a": 3, "b": 4}),
                hooks=AgentHooks(on_tool=on_tool),
            )
            await agent.run(_USER, stream=False)
        (event,) = seen
        assert event.name == "add"
        assert event.arguments == {"a": 3, "b": 4}
        assert event.result == ToolResult.ok({"result": 7})
