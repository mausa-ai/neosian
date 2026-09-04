"""Max-iterations exhaustion is observable on both paths (NF #170, AG-7):
`AgentResponse.iterations_exhausted` and the `done` frame's additive key."""

import pytest

from neosian import (
    Agent,
    AgentConfig,
    DoneEvent,
    Message,
    Model,
    Role,
    Tool,
    ToolCall,
    ToolCallId,
    ToolResult,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import ToolName


@Tool(name="noop", description="noop")
async def noop() -> ToolResult[str]:
    return ToolResult.ok("ok")


_CALL = ToolCall(id=ToolCallId("c1"), name=ToolName("noop"), arguments={})
_USER = [Message(role=Role.USER, content="go")]


def _agent(fake: FakeClient, *, max_tool_iterations: int) -> Agent:
    return Agent(
        AgentConfig(
            system_prompt="S",
            model=Model.FAKE,
            enable_todo=False,
            tools=[noop],
            client_factory=lambda _: fake,
            max_tool_iterations=max_tool_iterations,
        )
    )


def _exhausting() -> FakeClient:
    """One tool round against a bound of one, then the toolless final."""
    return FakeClient(
        FakeScript(turns=(FakeTurn(tool_calls=(_CALL,)), FakeTurn(content="final")))
    )


@pytest.mark.unit
class TestIterationsExhausted:
    async def test_blocking_flags_the_final_call(self) -> None:
        response = await _agent(_exhausting(), max_tool_iterations=1).run(
            _USER, stream=False
        )
        assert response.iterations_exhausted is True
        assert response.message.content == "final"
        assert len(response.tool_calls_made) == 1

    async def test_streaming_flags_the_done_frame(self) -> None:
        events = [
            e
            async for e in await _agent(_exhausting(), max_tool_iterations=1).run(
                _USER, stream=True
            )
        ]
        done = events[-1]
        assert isinstance(done, DoneEvent)
        assert done.iterations_exhausted is True
        assert done.to_dict()["iterations_exhausted"] is True

    async def test_a_run_that_finishes_in_bound_says_so(self) -> None:
        agent = _agent(_exhausting(), max_tool_iterations=2)
        response = await agent.run(_USER, stream=False)
        assert response.iterations_exhausted is False
        events = [
            e
            async for e in await _agent(_exhausting(), max_tool_iterations=2).run(
                _USER, stream=True
            )
        ]
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].iterations_exhausted is False
