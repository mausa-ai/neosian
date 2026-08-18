"""Agent end-to-end on FakeProvider — fully keyless (ECOSYSTEM §7).

These are the tests the fake exists for: real Agent code paths (blocking,
streaming, tool loop, fallback, sessions) with zero API keys and zero
ad-hoc mocks.
"""

import os
from unittest.mock import patch

import pytest

from neosian import Agent, AgentConfig, FallbackConfig, Model, ReasoningEffort
from neosian._foundation.llm.base import (
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolCall,
    Usage,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import ModelFailedError
from neosian._foundation.shared.types import (
    Provider,
    SystemPrompt,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult

_SYSTEM = SystemPrompt("You are a test agent.")
_USER = [Message(role=Role.USER, content="Hi")]


def _config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "system_prompt": _SYSTEM,
        "model": Model.FAKE,
        "enable_todo": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestKeylessBoot:
    async def test_blocking_run_with_zero_keys(self) -> None:
        """The three-line keyless boot: config, agent, run."""
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(_config())
            response = await agent.run(_USER, stream=False)
        assert response.message.content == "fake response"
        assert response.usage == Usage(input_tokens=10, output_tokens=5)
        assert response.model == Model.FAKE.value

    async def test_streaming_run_with_zero_keys(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(_config())
            events = [event async for event in await agent.run(_USER, stream=True)]
        assert events
        assert any("fake" in event for event in events)


@pytest.mark.unit
class TestClientFactory:
    async def test_factory_bypasses_the_router(self) -> None:
        fake = FakeClient()
        seen: list[Provider] = []

        def factory(provider: Provider) -> FakeClient:
            seen.append(provider)
            return fake

        agent = Agent(_config(client_factory=factory))
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "fake response"
        assert seen == [Provider.FAKE]
        assert len(fake.calls) == 1

    async def test_tool_loop_end_to_end(self) -> None:
        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(
                        tool_calls=(
                            ToolCall(
                                id=ToolCallId("c1"),
                                name=ToolName("greet"),
                                arguments={"name": "ada"},
                            ),
                        ),
                        usage=Usage(input_tokens=10, output_tokens=5),
                    ),
                    FakeTurn(
                        content="Greeted ada",
                        usage=Usage(input_tokens=30, output_tokens=4),
                    ),
                )
            )
        )
        agent = Agent(_config(tools=[greet], client_factory=lambda _: fake))
        response = await agent.run(_USER, stream=False)

        assert response.message.content == "Greeted ada"
        assert [tc.name for tc in response.tool_calls_made] == ["greet"]
        assert response.tool_results[0].data == "Hello ada"
        # The second recorded call must carry the TOOL message of the loop.
        second_call_roles = [m.role for m in fake.calls[1].messages]
        assert Role.TOOL in second_call_roles
        # Usage sums across iterations.
        assert response.usage == Usage(input_tokens=40, output_tokens=9)

    async def test_fallback_run_with_hand_computed_cost(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main model down")),
                    FakeTurn(
                        content="recovered",
                        usage=Usage(input_tokens=100, output_tokens=50),
                    ),
                )
            )
        )
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "recovered"
        assert response.model == Model.FAKE_SMALL.value
        # 100×100_000 + 50×1_000_000 = 60_000_000 → 60 µ$ at FAKE_SMALL rates.
        assert response.usage.cost_micro_usd(Model.FAKE_SMALL) == 60

    async def test_reasoning_effort_dropped_on_non_reasoning_fallback(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main model down")),
                    FakeTurn(content="fallback answer"),
                )
            )
        )
        agent = Agent(
            _config(
                model=Model.FAKE_REASONING,
                reasoning_effort=ReasoningEffort.LOW,
                fallback=FallbackConfig(model=Model.FAKE),
                client_factory=lambda _: fake,
            )
        )
        await agent.run(_USER, stream=False)
        assert fake.calls[0].reasoning_effort is ReasoningEffort.LOW
        assert fake.calls[1].reasoning_effort is None  # silently dropped


@pytest.mark.unit
class TestCapabilityAwareFallback:
    async def test_media_conversation_skips_incapable_fallback(self) -> None:
        """FAKE carries media, FAKE_SMALL does not — no downgrade allowed."""
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(error=TimeoutError("main down")),))
        )
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        image_conversation = [
            Message(
                role=Role.USER,
                content=[
                    TextBlock(text="What is this?"),
                    ImageBlock(media_type="image/png", data="aGk="),
                ],
            )
        ]
        with pytest.raises(ModelFailedError) as exc_info:
            await agent.run(image_conversation, stream=False)
        assert exc_info.value.has_fallback is False

    async def test_text_conversation_still_falls_back(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main down")),
                    FakeTurn(content="fallback ok"),
                )
            )
        )
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "fallback ok"


@pytest.mark.unit
class TestSessionWithFake:
    async def test_session_honors_factory_and_caches_per_provider(self) -> None:
        created: list[Provider] = []

        def factory(provider: Provider) -> FakeClient:
            created.append(provider)
            return FakeClient()

        agent = Agent(_config(client_factory=factory))
        async with agent.session() as session:
            first = await session.run(_USER, stream=False)
            second = await session.run(_USER, stream=False)
        assert first.message.content == second.message.content == "fake response"
        # One client per provider for the whole session.
        assert created == [Provider.FAKE]

    async def test_session_close_closes_the_fake(self) -> None:
        fake = FakeClient()
        agent = Agent(_config(client_factory=lambda _: fake))
        async with agent.session() as session:
            await session.run(_USER, stream=False)
        assert fake.closed is True
