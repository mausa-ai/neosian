"""Tests for Agent guardrails integration.

Since ledger #84 the guardrail policy model rides a neosian client via
`_create_client`, so these tests run on scripted FakeClients through
`client_factory` — the agent on FAKE, the guardrail model on a distinct
provider, fully keyless and mock-free.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.guards import extract_user_content
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    Usage,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import (
    GuardrailStreamingError,
    MissingAPIKeyError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    GuardrailErrorPolicy,
    GuardrailMode,
    GuardrailsConfig,
    Model,
    Provider,
    SystemPrompt,
)

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


def _guarded_agent(
    *,
    guard_turns: tuple[FakeTurn, ...],
    agent_turns: tuple[FakeTurn, ...] = (FakeTurn(content="Response"),),
    guardrails: GuardrailsConfig,
) -> tuple[Agent, FakeClient, FakeClient]:
    """An Agent on FAKE with its guardrail model on a distinct provider,
    each served by its own scripted FakeClient — deterministic under the
    concurrent guard task."""
    agent_fake = FakeClient(FakeScript(turns=agent_turns))
    guard_fake = FakeClient(FakeScript(turns=guard_turns))

    def factory(provider: Provider) -> BaseLLMClient:
        return guard_fake if provider is Provider.ANTHROPIC else agent_fake

    config = AgentConfig(
        system_prompt=SystemPrompt("You are helpful."),
        tools=[],
        model=Model.FAKE,
        enable_todo=False,
        client_factory=factory,
        guardrails=guardrails,
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

    def test_agent_creates_guardrail_client_when_configured(self) -> None:
        """The policy client comes from _create_client for the guardrail
        model's provider; an explicit GuardrailsConfig.model wins."""
        agent, agent_fake, guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            guardrails=_guardrails(),
        )
        assert agent._guardrail_client is guard_fake
        assert agent._guardrail_model is Model.CLAUDE_HAIKU_4_5

    def test_guardrail_model_defaults_to_the_agents_own(self) -> None:
        """model=None rides the agent's configured model — no hidden
        second provider (ledger #84)."""
        agent, agent_fake, _guard_fake = _guarded_agent(
            guard_turns=(FakeTurn(content=_SAFE_JSON),),
            guardrails=_guardrails(model=None),
        )
        assert agent._guardrail_model is Model.FAKE
        assert agent._guardrail_client is agent_fake

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
            assert agent._guardrail_client is not None

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

            assert agent._guardrail_client is None
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

    def test_skips_empty_user_content(self) -> None:
        """Should skip user messages with empty content."""
        messages = [
            Message(role=Role.USER, content="First"),
            Message(role=Role.USER, content=""),  # Empty
        ]
        assert extract_user_content(messages) == "First"


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
