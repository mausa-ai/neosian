"""Run identity, timing and the whole bill on the hook seam (N7, #332, #333)."""

import asyncio
from datetime import UTC, datetime

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.hooks import (
    AgentHooks,
    LlmCallEvent,
    ToolEvent,
    TurnEvent,
)
from neosian._foundation.llm.base import BaseLLMClient, Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import (
    AgentConfig,
    AnyModel,
    GuardrailMode,
    GuardrailsConfig,
    Model,
    Provider,
    ToolCallId,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult

_USER = [Message(role=Role.USER, content="go")]
_SAFE_JSON = '{"violation": 0, "category": null, "rationale": "Content is safe"}'


class _Seen:
    """Every hook event of a run, in order."""

    def __init__(self) -> None:
        self.calls: list[LlmCallEvent] = []
        self.tools: list[ToolEvent] = []
        self.turns: list[TurnEvent] = []

    def hooks(self) -> AgentHooks:
        return AgentHooks(
            on_llm_call=self.calls.append,
            on_tool=self.tools.append,
            on_turn=self.turns.append,
        )


def _calling(*names: str) -> FakeScript:
    """A script that calls every named tool once, then answers."""
    return FakeScript(
        turns=(
            FakeTurn(
                tool_calls=tuple(
                    ToolCall(id=ToolCallId(f"c{i}"), name=ToolName(n), arguments={})
                    for i, n in enumerate(names)
                )
            ),
            FakeTurn(content="finished"),
        )
    )


def _agent(script: FakeScript, seen: _Seen, *tools: ToolFunction) -> Agent:
    fake = FakeClient(script)
    return Agent(
        AgentConfig(
            system_prompt="You run tools.",
            model=Model.FAKE,
            enable_todo=False,
            tools=list(tools),
            hooks=seen.hooks(),
            client_factory=lambda _: fake,
        )
    )


def _sleeper(name: str, seconds: float) -> ToolFunction:
    @Tool(name=name, description="sleeps")
    async def sleeper() -> ToolResult[str]:
        await asyncio.sleep(seconds)
        return ToolResult.ok(name)

    return sleeper


@pytest.mark.unit
class TestRunIdentity:
    @pytest.mark.parametrize("stream", [False, True])
    async def test_one_run_shares_one_id_and_no_parent(self, stream: bool) -> None:
        seen = _Seen()
        agent = _agent(_calling("a"), seen, _sleeper("a", 0))
        if stream:
            async for _ in await agent.run(_USER, stream=True):
                pass
        else:
            await agent.run(_USER, stream=False)
        events: list[LlmCallEvent | ToolEvent | TurnEvent] = [
            *seen.calls,
            *seen.tools,
            *seen.turns,
        ]
        ids = {e.run_id for e in events}
        assert len(ids) == 1 and None not in ids
        assert len(seen.calls) == 2 and len(seen.tools) == 1 and len(seen.turns) == 1
        assert all(e.parent_run_id is None for e in seen.calls)
        assert all(e.conversation_id is None for e in seen.calls)
        assert all(e.purpose == "agent" for e in seen.calls)

    async def test_two_runs_get_two_ids(self) -> None:
        seen = _Seen()
        agent = _agent(
            FakeScript(turns=(FakeTurn(content="x"),), repeat_last=True), seen
        )
        await agent.run(_USER, stream=False)
        await agent.run(_USER, stream=False)
        assert len({e.run_id for e in seen.turns}) == 2

    async def test_an_agent_inside_a_tool_names_its_parent(self) -> None:
        inner_seen = _Seen()
        inner = _agent(FakeScript(turns=(FakeTurn(content="inner"),)), inner_seen)

        @Tool(name="delegate", description="runs an inner agent")
        async def delegate() -> ToolResult[str]:
            response = await inner.run(_USER, stream=False)
            return ToolResult.ok(str(response.message.content))

        outer_seen = _Seen()
        await _agent(_calling("delegate"), outer_seen, delegate).run(
            _USER, stream=False
        )
        outer_id = outer_seen.turns[0].run_id
        inner_turn = inner_seen.turns[0]
        assert inner_turn.run_id != outer_id
        assert inner_turn.parent_run_id == outer_id
        assert inner_seen.calls[0].parent_run_id == outer_id
        # The outer run itself has no parent: the var never leaked upward.
        assert outer_seen.turns[0].parent_run_id is None

    @pytest.mark.parametrize("stream", [False, True])
    async def test_parallel_tools_start_where_they_started(self, stream: bool) -> None:
        seen = _Seen()
        agent = _agent(
            _calling("quick", "slow"),
            seen,
            _sleeper("quick", 0.01),
            _sleeper("slow", 0.15),
        )
        before = datetime.now(UTC)
        if stream:
            async for _ in await agent.run(_USER, stream=True):
                pass
        else:
            await agent.run(_USER, stream=False)
        quick, slow = seen.tools
        assert quick.started_at is not None and slow.started_at is not None
        assert quick.started_at.tzinfo is UTC and before <= quick.started_at
        assert abs((slow.started_at - quick.started_at).total_seconds()) < 0.05
        assert slow.duration_ms >= 100 > quick.duration_ms
        call = seen.calls[0]
        assert call.started_at is not None and before <= call.started_at

    async def test_the_classifier_is_on_the_bill(self) -> None:
        seen = _Seen()
        agent_fake = FakeClient(FakeScript(turns=(FakeTurn(content="Hello!"),)))
        guard_fake = FakeClient(FakeScript(turns=(FakeTurn(content=_SAFE_JSON),)))

        def factory(model: AnyModel) -> BaseLLMClient:
            return guard_fake if model.provider is Provider.ANTHROPIC else agent_fake

        agent = Agent(
            AgentConfig(
                system_prompt="You are helpful.",
                model=Model.FAKE,
                enable_todo=False,
                client_factory=factory,
                hooks=seen.hooks(),
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy="test policy",
                    block_on_input=True,
                    model=Model.CLAUDE_SONNET_5,
                ),
            )
        )
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "Hello!"
        purposes = [(e.purpose, e.requested_model) for e in seen.calls]
        assert ("guardrail", Model.CLAUDE_SONNET_5.value) in purposes
        assert ("agent", Model.FAKE.value) in purposes
        assert len({e.run_id for e in seen.calls}) == 1
        classifier = next(e for e in seen.calls if e.purpose == "guardrail")
        assert classifier.provider is Provider.ANTHROPIC
        assert classifier.iteration == 0 and classifier.started_at is not None
