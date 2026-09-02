"""Tests for Agent guardrails integration.

Since ledger #84 the guardrail policy model rides a neosian client via
`_create_client`, so these tests run on scripted FakeClients through
`client_factory` — the agent on FAKE, the guardrail model on a distinct
provider, fully keyless and mock-free.
"""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.events import BlockedEvent, DoneEvent
from neosian._foundation.agent.guards import extract_user_content
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    ModelUsage,
    Role,
    ToolCall,
    Usage,
)
from neosian._foundation.llm.blocks import ImageBlock
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import (
    GuardrailStreamingError,
    MissingAPIKeyError,
    ModelFailedError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    FallbackConfig,
    GuardrailErrorPolicy,
    GuardrailMode,
    GuardrailsConfig,
    Model,
    Provider,
    SystemPrompt,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult

_POLICY = "test policy string"
_UNSAFE_JSON = '{"violation": 1, "category": "P1", "rationale": "Flagged content"}'
_SAFE_JSON = '{"violation": 0, "category": null, "rationale": "Content is safe"}'


def _create_mock_router(mock_client: BaseLLMClient | None = None) -> MagicMock:
    """Create a mock ProviderRouter that returns the given client."""
    if mock_client is None:
        mock_client = AsyncMock(spec=BaseLLMClient)

    mock_router = MagicMock()
    mock_router.has_provider.return_value = True
    mock_router.create_client_for.return_value = mock_client
    return mock_router


class _SlowFake(FakeClient):
    """A fake whose completion takes a while — a task still pending when
    the other side of the guard race finishes."""

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResponse:
        await asyncio.sleep(0.2)
        return await super().complete(*args, **kwargs)


def _guarded_agent(
    *,
    guard_turns: tuple[FakeTurn, ...],
    agent_turns: tuple[FakeTurn, ...] = (FakeTurn(content="Response"),),
    guardrails: GuardrailsConfig,
    guard_client: type[FakeClient] = FakeClient,
    agent_client: type[FakeClient] = FakeClient,
    tools: list[Any] | None = None,
    fallback: FallbackConfig | None = None,
) -> tuple[Agent, FakeClient, FakeClient]:
    """An Agent on FAKE with its guardrail model on a distinct provider,
    each served by its own scripted FakeClient — deterministic under the
    concurrent guard task."""
    agent_fake = agent_client(FakeScript(turns=agent_turns))
    guard_fake = guard_client(FakeScript(turns=guard_turns))

    def factory(provider: Provider) -> BaseLLMClient:
        return guard_fake if provider is Provider.ANTHROPIC else agent_fake

    config = AgentConfig(
        system_prompt=SystemPrompt("You are helpful."),
        tools=tools or [],
        model=Model.FAKE,
        enable_todo=False,
        client_factory=factory,
        guardrails=guardrails,
        fallback=fallback,
    )
    return Agent(config=config), agent_fake, guard_fake


def _guardrails(**overrides: object) -> GuardrailsConfig:
    defaults: dict[str, object] = {
        "input_mode": GuardrailMode.POLICY_ONLY,
        "input_policy": _POLICY,
        "block_on_input": True,
        "model": Model.CLAUDE_HAIKU_4_5,
    }
    defaults.update(overrides)
    return GuardrailsConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestAgentGuardrailsInit:
    """Test Agent initialization with guardrails."""

    async def test_guardrail_model_routes_to_its_own_provider(self) -> None:
        """An explicit GuardrailsConfig.model wins, and its client is
        acquired for that model's provider at check time."""
        agent, agent_fake, guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            guardrails=_guardrails(),
        )
        assert agent._guardrail_model is Model.CLAUDE_HAIKU_4_5
        await agent.run([Message(role=Role.USER, content="Hello")], stream=False)
        assert len(guard_fake.calls) == 1
        assert _POLICY in str(guard_fake.calls[0].messages[-1].content)

    def test_guardrail_model_defaults_to_the_agents_own(self) -> None:
        """model=None rides the agent's configured model — no hidden
        second provider (ledger #84)."""
        agent, agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            guardrails=_guardrails(model=None),
        )
        assert agent._guardrail_model is Model.FAKE

    def test_missing_key_for_guardrail_provider_is_loud(self) -> None:
        """Without a client_factory, an absent key for the guardrail
        model's provider raises at construction — never a silent
        fail-open at check time."""
        with patch.dict(os.environ, {}, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                model=Model.FAKE,
                enable_todo=False,
                guardrails=_guardrails(),
            )
            with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY"):
                Agent(config=config)

    def test_fake_guardrail_model_needs_no_key(self) -> None:
        """The FAKE default is keyless by construction."""
        with patch.dict(os.environ, {}, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                model=Model.FAKE,
                enable_todo=False,
                guardrails=_guardrails(model=None),
            )
            agent = Agent(config=config)
            assert agent._guardrail_model is Model.FAKE

    def test_agent_no_guardrail_client_when_not_configured(self) -> None:
        """Agent should not create guardrail client when no guardrails."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert agent._guardrail_model is None
            assert agent._guardrails is None


@pytest.mark.unit
class TestAgentStreamingWithOutputGuardrails:
    """Test streaming restriction with output guardrails."""

    def test_streaming_with_output_guardrails_raises_error(self) -> None:
        """stream=True with output guardrails should raise GuardrailStreamingError."""
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            guardrails=_guardrails(
                input_mode=GuardrailMode.NONE,
                input_policy=None,
                output_mode=GuardrailMode.POLICY_ONLY,
                output_policy=_POLICY,
            ),
        )

        with pytest.raises(GuardrailStreamingError):
            # Note: run() with stream=True is synchronous until iteration
            import asyncio

            asyncio.run(agent.run([Message(role=Role.USER, content="Hi")], stream=True))

    def test_streaming_with_input_guardrails_allowed(self) -> None:
        """stream=True with only input guardrails should be allowed."""
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            guardrails=_guardrails(output_mode=GuardrailMode.NONE),
        )

        # Should not raise - streaming is allowed with input-only guardrails
        # We just verify no exception is raised synchronously
        assert agent._guardrails is not None
        assert not agent._guardrails.has_output_guardrails


@pytest.mark.unit
class TestExtractUserContent:
    """extract_user_content is a pure function since the N0 split."""

    def test_extracts_last_user_message(self) -> None:
        """Should extract content from last user message."""
        messages = [
            Message(role=Role.USER, content="First message"),
            Message(role=Role.ASSISTANT, content="Response"),
            Message(role=Role.USER, content="Second message"),
        ]
        assert extract_user_content(messages) == "Second message"

    def test_returns_empty_when_no_user_messages(self) -> None:
        """Should return empty string when no user messages."""
        messages = [
            Message(role=Role.ASSISTANT, content="Hello"),
        ]
        assert extract_user_content(messages) == ""

    def test_returns_empty_for_empty_messages(self) -> None:
        """Should return empty string for empty message list."""
        assert extract_user_content([]) == ""

    def test_empty_last_message_is_not_walked_back(self) -> None:
        """The last user message decides; older text is never classified in
        its place (TG-15)."""
        messages = [
            Message(role=Role.USER, content="First message"),
            Message(role=Role.ASSISTANT, content="Response"),
            Message(role=Role.USER, content=""),
        ]
        assert extract_user_content(messages) == ""

    def test_media_only_last_message_is_not_walked_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        messages = [
            Message(role=Role.USER, content="First message"),
            Message(role=Role.ASSISTANT, content="Response"),
            Message(role=Role.USER, content=[ImageBlock(url="https://x/y.png")]),
        ]
        with caplog.at_level(
            logging.WARNING, logger="neosian._foundation.agent.guards"
        ):
            assert extract_user_content(messages) == ""
        assert any("media-only" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
class TestAgentInputGuardrails:
    """Test Agent input guardrails behavior."""

    async def test_input_blocked_returns_blocked_response(self) -> None:
        """Flagged input with block_on_input=True should return blocked response."""
        agent, _agent_fake, guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_UNSAFE_JSON),),
            guardrails=_guardrails(block_on_input=True),
        )

        messages = [Message(role=Role.USER, content="Bad content")]
        response = await agent.run(messages, stream=False)

        # Response should be blocked with guardrail info
        assert response.blocked is True
        assert response.guardrail_result is not None
        assert response.guardrail_result.flagged_at == "input"
        assert response.guardrail_result.safe is False
        assert response.message.content == ""

        # The guardrail client really ran the classifier prompt
        assert len(guard_fake.calls) == 1
        assert _POLICY in str(guard_fake.calls[0].messages[-1].content)

    async def test_input_flagged_not_blocked_continues(self) -> None:
        """Flagged input with block_on_input=False should continue execution."""
        agent, agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_UNSAFE_JSON),),
            guardrails=_guardrails(block_on_input=False),
        )

        messages = [Message(role=Role.USER, content="Bad content")]
        response = await agent.run(messages, stream=False)

        # Should have continued to LLM
        assert len(agent_fake.calls) == 1
        assert response.message.content == "Response"

        # Guardrail result should still be populated
        assert response.guardrail_result is not None
        assert response.guardrail_result.input_policy is not None
        assert response.guardrail_result.input_policy.safe is False

    async def test_input_safe_continues_execution(self) -> None:
        """Safe input should continue to LLM execution."""
        agent, agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            agent_turns=(FakeTurn(content="Hello!"),),
            guardrails=_guardrails(block_on_input=True),
        )

        messages = [Message(role=Role.USER, content="Hello")]
        response = await agent.run(messages, stream=False)

        assert response.blocked is False
        assert response.message.content == "Hello!"
        assert len(agent_fake.calls) == 1


@pytest.mark.unit
class TestAgentOutputGuardrails:
    """Test Agent output guardrails behavior."""

    async def test_output_flagged_sets_blocked(self) -> None:
        """Flagged output should set blocked=True and preserve content."""
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_UNSAFE_JSON),),
            agent_turns=(FakeTurn(content="Bad output"),),
            guardrails=_guardrails(
                input_mode=GuardrailMode.NONE,
                input_policy=None,
                output_mode=GuardrailMode.POLICY_ONLY,
                output_policy=_POLICY,
            ),
        )

        messages = [Message(role=Role.USER, content="Hello")]
        response = await agent.run(messages, stream=False)

        assert response.blocked is True
        assert response.message.content == "Bad output"  # Preserved for logging
        assert response.guardrail_result is not None
        assert response.guardrail_result.flagged_at == "output"
        assert response.guardrail_result.output_policy is not None
        assert response.guardrail_result.output_policy.category == "P1"

    async def test_output_safe_not_blocked(self) -> None:
        """Safe output should not be blocked."""
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            agent_turns=(FakeTurn(content="Good output"),),
            guardrails=_guardrails(
                input_mode=GuardrailMode.NONE,
                input_policy=None,
                output_mode=GuardrailMode.POLICY_ONLY,
                output_policy=_POLICY,
            ),
        )

        messages = [Message(role=Role.USER, content="Hello")]
        response = await agent.run(messages, stream=False)

        assert response.blocked is False
        assert response.message.content == "Good output"


@pytest.mark.unit
class TestAgentNoGuardrails:
    """Test Agent behavior with no guardrails configured."""

    @pytest.mark.asyncio
    async def test_no_guardrails_no_result(self) -> None:
        """No guardrails should result in no guardrail_result."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hello")]
            response = await agent.run(messages, stream=False)

            assert response.blocked is False
            assert response.guardrail_result is None


@pytest.mark.unit
class TestAgentResponseDefaults:
    """Test AgentResponse guardrail-related defaults."""

    def test_agent_response_blocked_default(self) -> None:
        """AgentResponse blocked should default to False."""
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="Hello"),
        )
        assert response.blocked is False

    def test_agent_response_guardrail_result_default(self) -> None:
        """AgentResponse guardrail_result should default to None."""
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="Hello"),
        )
        assert response.guardrail_result is None


@pytest.mark.unit
class TestGuardrailErrorPolicy:
    """Test guardrail error policy behavior."""

    async def test_fail_open_on_guardrail_error(self) -> None:
        """FAIL_OPEN policy should treat guardrail errors as safe."""
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(error=TimeoutError("API timeout")),),
            guardrails=_guardrails(
                error_policy=GuardrailErrorPolicy.FAIL_OPEN,  # Default
            ),
        )

        messages = [Message(role=Role.USER, content="Hello")]
        response = await agent.run(messages, stream=False)

        # Should NOT be blocked - fail-open treats error as safe
        assert response.blocked is False
        assert response.message.content == "Response"

    async def test_fail_closed_on_guardrail_error(self) -> None:
        """FAIL_CLOSED policy should treat guardrail errors as blocked."""
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(error=TimeoutError("API timeout")),),
            guardrails=_guardrails(
                error_policy=GuardrailErrorPolicy.FAIL_CLOSED,
            ),
        )

        messages = [Message(role=Role.USER, content="Hello")]
        response = await agent.run(messages, stream=False)

        # Should be blocked - fail-closed treats error as blocked
        assert response.blocked is True
        assert response.message.content == ""

    def test_guardrails_config_default_error_policy(self) -> None:
        """GuardrailsConfig should default to FAIL_OPEN."""
        config = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY,
            input_policy=_POLICY,
        )
        assert config.error_policy == GuardrailErrorPolicy.FAIL_OPEN


@pytest.mark.unit
class TestGuardrailClientLifecycle:
    """The classifier's client rides the run's `acquire` seam (AG-6): an
    Agent mints nothing at construction, and a session's pool owns it."""

    def test_agent_init_mints_no_guard_client(self) -> None:
        created: list[Provider] = []

        def factory(provider: Provider) -> BaseLLMClient:
            created.append(provider)
            return FakeClient()

        Agent(
            config=AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                model=Model.FAKE,
                enable_todo=False,
                client_factory=factory,
                guardrails=_guardrails(),
            )
        )
        assert created == []

    async def test_session_closes_the_guard_client(self) -> None:
        agent, _agent_fake, guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            guardrails=_guardrails(),
        )
        async with agent.session() as session:
            await session.run([Message(role=Role.USER, content="Hello")], stream=False)
        assert len(guard_fake.calls) == 1
        assert guard_fake.closed

    async def test_session_shares_the_pool_when_guard_model_is_the_agents(
        self,
    ) -> None:
        created: list[FakeClient] = []

        def factory(_: Provider) -> BaseLLMClient:
            created.append(
                FakeClient(
                    FakeScript(turns=(FakeTurn(content=_SAFE_JSON),), repeat_last=True)
                )
            )
            return created[-1]

        agent = Agent(
            config=AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                model=Model.FAKE,
                enable_todo=False,
                client_factory=factory,
                guardrails=_guardrails(model=None),
            )
        )
        async with agent.session() as session:
            await session.run([Message(role=Role.USER, content="Hello")], stream=False)
        assert len(created) == 1
        assert len(created[0].calls) == 2


@pytest.mark.unit
class TestGuardTaskLifetime:
    """The guard task never outlives its run (AG-2, AG-3): every exit —
    an agent error, a cancellation, a consumer disconnect — reaps it."""

    _MESSAGES = [Message(role=Role.USER, content="Hello")]

    def _slow_guarded(self, agent_turns: tuple[FakeTurn, ...], **kwargs: Any) -> Agent:
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            agent_turns=agent_turns,
            guardrails=_guardrails(),
            guard_client=_SlowFake,
            **kwargs,
        )
        return agent

    async def test_agent_error_reaps_pending_guard_task(self) -> None:
        agent = self._slow_guarded((FakeTurn(error=RuntimeError("boom")),))
        before = asyncio.all_tasks()
        with pytest.raises(ModelFailedError):
            await agent.run(self._MESSAGES, stream=False)
        assert not (asyncio.all_tasks() - before)

    async def test_cancelled_blocking_run_reaps_both_tasks(self) -> None:
        agent = self._slow_guarded(
            (FakeTurn(content="Response"),), agent_client=_SlowFake
        )
        before = asyncio.all_tasks()
        task = asyncio.create_task(agent.run(self._MESSAGES, stream=False))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not (asyncio.all_tasks() - before)

    async def test_stream_error_reaps_pending_guard_task(self) -> None:
        agent = self._slow_guarded((FakeTurn(error=RuntimeError("boom")),))
        before = asyncio.all_tasks()
        with pytest.raises(ModelFailedError):
            async for _ in await agent.run(self._MESSAGES, stream=True):
                pass
        assert not (asyncio.all_tasks() - before)

    async def test_consumer_aclose_reaps_pending_guard_task(self) -> None:
        agent = self._slow_guarded((FakeTurn(content="Response"),))
        before = asyncio.all_tasks()
        stream = await agent.run(self._MESSAGES, stream=True)
        assert isinstance(stream, AsyncGenerator)
        await anext(stream)  # ReadyEvent — the guard task is now running
        await stream.aclose()
        assert not (asyncio.all_tasks() - before)


@pytest.mark.unit
class TestBlockedUsageSurvives:
    """An input block never hides spend (AG-13): the cancelled attempt's
    tokens were billed and ride the blocked response."""

    async def test_input_blocked_mid_run_keeps_billed_usage(self) -> None:
        @Tool(name="slow", description="Takes a while")
        async def slow() -> ToolResult[str]:
            await asyncio.sleep(0.2)
            return ToolResult.ok("done")

        agent, agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_UNSAFE_JSON),),
            agent_turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"), name=ToolName("slow"), arguments={}
                        ),
                    ),
                    usage=Usage(input_tokens=10, output_tokens=5),
                ),
                FakeTurn(content="never"),
            ),
            guardrails=_guardrails(),
            tools=[slow],
        )
        response = await agent.run(
            [Message(role=Role.USER, content="Hello")], stream=False
        )
        assert response.blocked
        assert len(agent_fake.calls) == 1  # parked in the tool when the guard landed
        assert response.usage == Usage(input_tokens=10, output_tokens=5)
        assert _split(response.usage_by_model)[Model.FAKE.value] == Usage(
            input_tokens=10, output_tokens=5
        )


_GUARD_USAGE = Usage(input_tokens=7, output_tokens=3)
_AGENT_USAGE = Usage(input_tokens=20, output_tokens=5)
_BOTH = Usage(input_tokens=27, output_tokens=8)


class _GatedFake(FakeClient):
    """A fake whose completion waits for the test to release it — makes
    guard-first and agent-first deterministic."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.release = asyncio.Event()

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResponse:
        await self.release.wait()
        return await super().complete(*args, **kwargs)


def _split(by_model: tuple[ModelUsage, ...]) -> dict[str, Usage]:
    return {entry.model: entry.usage for entry in by_model}


@pytest.mark.unit
class TestGuardSpendAccounting:
    """The classifier's call is billed, so it is accounted (TG-4): the
    run's usage and its per-model split carry it on both paths."""

    _MESSAGES = [Message(role=Role.USER, content="Hello")]
    _GUARD = Model.CLAUDE_HAIKU_4_5.value

    def _agent(self, verdict: str, **kwargs: Any) -> tuple[Agent, FakeClient]:
        agent, _agent_fake, guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=verdict, usage=_GUARD_USAGE),),
            agent_turns=(FakeTurn(content="Response", usage=_AGENT_USAGE),),
            guardrails=kwargs.pop("guardrails", _guardrails()),
            **kwargs,
        )
        return agent, guard_fake

    async def test_blocking_safe_turn_counts_classifier_spend(self) -> None:
        agent, _ = self._agent(_SAFE_JSON)
        response = await agent.run(self._MESSAGES, stream=False)
        assert response.usage == _BOTH
        assert _split(response.usage_by_model) == {
            Model.FAKE.value: _AGENT_USAGE,
            self._GUARD: _GUARD_USAGE,
        }

    async def test_blocking_output_guard_counts_classifier_spend(self) -> None:
        agent, _ = self._agent(
            _SAFE_JSON,
            guardrails=_guardrails(
                input_mode=GuardrailMode.NONE,
                input_policy=None,
                output_mode=GuardrailMode.POLICY_ONLY,
                output_policy=_POLICY,
            ),
        )
        response = await agent.run(self._MESSAGES, stream=False)
        assert response.usage == _BOTH
        assert _split(response.usage_by_model)[self._GUARD] == _GUARD_USAGE

    async def test_blocked_input_case1_carries_classifier_spend(self) -> None:
        agent, _ = self._agent(_UNSAFE_JSON, agent_client=_GatedFake)
        response = await agent.run(self._MESSAGES, stream=False)
        assert response.blocked
        assert _split(response.usage_by_model) == {self._GUARD: _GUARD_USAGE}
        assert response.usage == _GUARD_USAGE

    async def test_blocked_input_case2_carries_both_spends(self) -> None:
        agent, guard_fake = self._agent(_UNSAFE_JSON, guard_client=_GatedFake)
        assert isinstance(guard_fake, _GatedFake)
        task = asyncio.create_task(agent.run(self._MESSAGES, stream=False))
        await asyncio.sleep(0.01)  # the agent finishes; the guard is gated
        guard_fake.release.set()
        response = await task
        assert response.blocked
        assert response.usage == _BOTH
        assert _split(response.usage_by_model) == {
            Model.FAKE.value: _AGENT_USAGE,
            self._GUARD: _GUARD_USAGE,
        }

    async def test_streaming_done_counts_classifier_spend(self) -> None:
        agent, _ = self._agent(_SAFE_JSON)
        events = [e async for e in await agent.run(self._MESSAGES, stream=True)]
        done = events[-1]
        assert isinstance(done, DoneEvent)
        assert done.usage == _BOTH
        assert _split(done.usage_by_model) == {
            Model.FAKE.value: _AGENT_USAGE,
            self._GUARD: _GUARD_USAGE,
        }

    async def test_streaming_blocked_carries_classifier_spend(self) -> None:
        agent, _ = self._agent(_UNSAFE_JSON)
        events = [e async for e in await agent.run(self._MESSAGES, stream=True)]
        blocked = events[-1]
        assert isinstance(blocked, BlockedEvent)
        assert blocked.usage == _BOTH
        assert _split(blocked.usage_by_model)[self._GUARD] == _GUARD_USAGE

    async def test_streaming_fallback_counts_classifier_spend_once(self) -> None:
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON, usage=_GUARD_USAGE),),
            agent_turns=(
                FakeTurn(error=RuntimeError("boom")),
                FakeTurn(content="recovered", usage=_AGENT_USAGE),
            ),
            guardrails=_guardrails(),
            fallback=FallbackConfig(model=Model.FAKE_SMALL),
        )
        stream = await agent.run(self._MESSAGES, stream=True)
        await anext(stream)  # ReadyEvent
        await asyncio.sleep(0)  # the guard lands before the main attempt
        events = [e async for e in stream]
        done = events[-1]
        assert isinstance(done, DoneEvent)
        assert done.usage == _BOTH
        assert _split(done.usage_by_model)[self._GUARD] == _GUARD_USAGE


@pytest.mark.unit
class TestUnparseableVerdict:
    """A classifier that answered but could not be parsed is not "never
    answered" (TG-6): named at ERROR, without echoing the reply."""

    _FENCED = '```json\n{"violation": 1, "category": "P1"}\n```'

    async def test_unparseable_verdict_fails_open_but_logs_at_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=self._FENCED),),
            guardrails=_guardrails(),
        )
        with caplog.at_level(
            logging.WARNING, logger="neosian._foundation.agent.guards"
        ):
            response = await agent.run(
                [Message(role=Role.USER, content="Hello")], stream=False
            )
        assert response.blocked is False
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "unparseable" in errors[0].getMessage()
        assert "violation" not in errors[0].getMessage()

    async def test_unparseable_verdict_blocks_under_fail_closed(self) -> None:
        agent, _agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=self._FENCED),),
            guardrails=_guardrails(error_policy=GuardrailErrorPolicy.FAIL_CLOSED),
        )
        response = await agent.run(
            [Message(role=Role.USER, content="Hello")], stream=False
        )
        assert response.blocked is True
        assert response.guardrail_result is not None
        assert response.guardrail_result.input_policy is None


@pytest.mark.unit
class TestMediaOnlyInput:
    """A media-only user message skips the input guard loudly, never
    silently and never against stale text (TG-15)."""

    async def test_media_only_input_skips_guard_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent, agent_fake, guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_UNSAFE_JSON),),
            guardrails=_guardrails(),
        )
        with caplog.at_level(
            logging.WARNING, logger="neosian._foundation.agent.guards"
        ):
            response = await agent.run(
                [Message(role=Role.USER, content=[ImageBlock(url="https://x/y.png")])],
                stream=False,
            )
        assert response.blocked is False
        assert guard_fake.calls == []
        assert len(agent_fake.calls) == 1
        assert any("media-only" in r.getMessage() for r in caplog.records)
