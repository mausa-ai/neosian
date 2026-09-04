"""The provider channel rides a stream's terminal chunk onto the message
the agent assembles, on the tool-call turn and the final one alike
(NF #172) — so a streamed Anthropic reasoning turn replays whole."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from neosian import (
    Agent,
    AgentConfig,
    AgentHooks,
    AgentResponse,
    Message,
    Model,
    Role,
    StreamChunk,
    Tool,
    ToolCall,
    ToolCallId,
    ToolResult,
    TurnEvent,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import ToolName


class _ExtraFake(FakeClient):
    """Stamps `extra` on every stream's terminal chunk, like a provider."""

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        chunks = [chunk async for chunk in super().stream(*args, **kwargs)]
        chunks[-1].extra = {"anthropic": {"thinking_blocks": [{"type": "t"}]}}
        for chunk in chunks:
            yield chunk


@Tool(name="noop", description="noop")
async def noop() -> ToolResult[str]:
    return ToolResult.ok("ok")


_CALL = ToolCall(id=ToolCallId("c1"), name=ToolName("noop"), arguments={})


@pytest.mark.unit
async def test_streamed_messages_carry_the_terminal_chunks_extra() -> None:
    turns: list[AgentResponse] = []

    async def on_turn(event: TurnEvent) -> None:
        turns.append(event.response)

    fake = _ExtraFake(
        FakeScript(turns=(FakeTurn(tool_calls=(_CALL,)), FakeTurn(content="done")))
    )
    agent = Agent(
        AgentConfig(
            system_prompt="S",
            model=Model.FAKE,
            enable_todo=False,
            tools=[noop],
            client_factory=lambda _: fake,
            hooks=AgentHooks(on_turn=on_turn),
        )
    )
    async for _ in await agent.run(
        [Message(role=Role.USER, content="go")], stream=True
    ):
        pass

    (response,) = turns
    assistant = [m for m in response.turn_messages if m.role is Role.ASSISTANT]
    assert len(assistant) == 2  # the tool-call turn and the final answer
    assert all(
        m.extra == {"anthropic": {"thinking_blocks": [{"type": "t"}]}}
        for m in assistant
    )
