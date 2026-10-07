"""The per-server bound on bridged MCP tools (N7, ledger #331)."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role, ToolCall, ToolDefinition
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.mcp.bridge import bridge_tool
from neosian._foundation.mcp.client import McpServer
from neosian._foundation.shared.types import AgentConfig, Model, ToolCallId, ToolName
from neosian._foundation.tools.base import get_tool_metadata

_DEFINITION = ToolDefinition(
    name=ToolName("remote"),
    description="A tool on a server that stopped answering.",
    parameters={"type": "object", "properties": {}},
)


async def _never_answers(name: str, arguments: dict[str, Any]) -> Any:  # noqa: ARG001
    await asyncio.sleep(1)
    raise AssertionError("the bound should have fired first")


@pytest.mark.unit
class TestServerBound:
    def test_the_bound_rides_every_bridged_tool(self) -> None:
        tool = bridge_tool(
            _never_answers,
            "remote",
            _DEFINITION,
            origin="MCP server 'x'",
            timeout_seconds=5,
        )
        metadata = get_tool_metadata(tool)
        assert metadata is not None and metadata.timeout_seconds == 5

    def test_unbounded_by_default(self) -> None:
        tool = bridge_tool(
            _never_answers, "remote", _DEFINITION, origin="MCP server 'x'"
        )
        metadata = get_tool_metadata(tool)
        assert metadata is not None and metadata.timeout_seconds is None

    def test_the_server_refuses_a_non_positive_bound(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            McpServer.in_process(SimpleNamespace(name="stub"), timeout_seconds=0)

    async def test_a_server_that_stops_answering_fails_in_band(self) -> None:
        tool = bridge_tool(
            _never_answers,
            "remote",
            _DEFINITION,
            origin="MCP server 'x'",
            timeout_seconds=0.05,
        )
        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"), name=ToolName("remote"), arguments={}
                        ),
                    )
                ),
                FakeTurn(content="finished"),
            )
        )
        fake = FakeClient(script)
        agent = Agent(
            AgentConfig(
                system_prompt="You call the server.",
                model=Model.FAKE,
                enable_todo=False,
                tools=[tool],
                client_factory=lambda _: fake,
            )
        )
        response = await agent.run(
            [Message(role=Role.USER, content="call it")], stream=False
        )
        result = response.tool_results[0]
        assert result.code == "tool_timeout"
        assert result.error == "Tool 'remote' timed out after 0.05s"
        assert response.message.content == "finished"
