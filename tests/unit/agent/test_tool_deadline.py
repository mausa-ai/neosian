"""The bound on a tool's execution (N7, ledger #331)."""

import asyncio
from unittest.mock import patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.events import ToolProgressEvent, ToolResultEvent
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    ToolCallId,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult

_USER = [Message(role=Role.USER, content="run it")]
_HEARTBEAT = "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS"


def _agent(tool: ToolFunction, name: str, *, default: float | None = None) -> Agent:
    """A fake-scripted agent that calls `name` once, then answers."""
    script = FakeScript(
        turns=(
            FakeTurn(
                tool_calls=(
                    ToolCall(id=ToolCallId("c1"), name=ToolName(name), arguments={}),
                )
            ),
            FakeTurn(content="finished"),
        )
    )
    fake = FakeClient(script)
    return Agent(
        AgentConfig(
            system_prompt="You run tools.",
            model=Model.FAKE,
            enable_todo=False,
            tools=[tool],
            tool_timeout_seconds=default,
            client_factory=lambda _: fake,
        )
    )


def _sleeper(name: str, seconds: float, *, bound: float | None = None) -> ToolFunction:
    @Tool(name=name, description="sleeps", timeout_seconds=bound)
    async def sleeper() -> ToolResult[str]:
        await asyncio.sleep(seconds)
        return ToolResult.ok("slept")

    return sleeper


@pytest.mark.unit
class TestToolDeadline:
    async def test_past_the_bound_fails_in_band_and_the_run_goes_on(self) -> None:
        agent = _agent(_sleeper("slow", 1, bound=0.05), "slow")
        response = await agent.run(_USER, stream=False)
        result = response.tool_results[0]
        assert result.success is False
        assert result.code == "tool_timeout"
        assert result.error == "Tool 'slow' timed out after 0.05s"
        tool_message = next(m for m in response.turn_messages if m.role == Role.TOOL)
        assert "tool_timeout" in str(tool_message.content)
        assert response.message.content == "finished"

    async def test_streaming_heartbeats_until_the_bound(self) -> None:
        agent = _agent(_sleeper("slow", 1, bound=0.1), "slow")
        with patch(_HEARTBEAT, 0.02):
            events = [e async for e in await agent.run(_USER, stream=True)]
        assert any(isinstance(e, ToolProgressEvent) for e in events)
        result = next(e for e in events if isinstance(e, ToolResultEvent))
        assert result.success is False
        assert result.error == "Tool 'slow' timed out after 0.1s"

    async def test_the_agent_default_bounds_an_unbounded_tool(self) -> None:
        agent = _agent(_sleeper("slow", 1), "slow", default=0.05)
        response = await agent.run(_USER, stream=False)
        assert response.tool_results[0].code == "tool_timeout"

    async def test_the_tool_bound_wins_over_the_agent_default(self) -> None:
        quick = _agent(_sleeper("t", 0.05, bound=10), "t", default=0.01)
        assert (await quick.run(_USER, stream=False)).tool_results[0].success
        slow = _agent(_sleeper("t", 1, bound=0.05), "t", default=10)
        response = await slow.run(_USER, stream=False)
        assert response.tool_results[0].code == "tool_timeout"

    async def test_none_waits(self) -> None:
        response = await _agent(_sleeper("t", 0.05), "t").run(_USER, stream=False)
        assert response.tool_results[0].success is True

    async def test_a_body_raising_timeout_error_is_its_own_failure(self) -> None:
        @Tool(name="inner", description="raises", timeout_seconds=10)
        async def inner() -> ToolResult[str]:
            raise TimeoutError("upstream")

        response = await _agent(inner, "inner").run(_USER, stream=False)
        assert response.tool_results[0].code == "tool_execution_failed"

    def test_the_config_bound_must_be_positive(self) -> None:
        with pytest.raises(UnsupportedParameterError):
            AgentConfig(system_prompt="x", model=Model.FAKE, tool_timeout_seconds=0)
