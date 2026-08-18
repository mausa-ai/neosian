"""Tests for Agent guardrails integration."""

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
from neosian._foundation.shared.exceptions import GuardrailStreamingError
from neosian._foundation.shared.types import (
    AgentConfig,
    GuardrailErrorPolicy,
    GuardrailMode,
    GuardrailsConfig,
    SystemPrompt,
)

_POLICY = "test policy string"
_UNSAFE_JSON = '{"violation": 1, "category": "P1", "rationale": "Flagged content"}'
_SAFE_JSON = '{"violation": 0, "category": null, "rationale": "Content is safe"}'


def _create_mock_router(mock_client: BaseLLMClient | None = None) -> MagicMock:
    """Create a mock ProviderRouter that returns the given client.

    Args:
        mock_client: The mock client to return. If None, creates a new AsyncMock.

    Returns:
        MagicMock configured as a ProviderRouter.
    """
    if mock_client is None:
        mock_client = AsyncMock(spec=BaseLLMClient)

    mock_router = MagicMock()
    mock_router.has_provider.return_value = True
    mock_router.create_client.return_value = mock_client
    return mock_router


def _mock_guardrail_response(content: str) -> AsyncMock:
    """Create a mock guardrail API response."""
    return AsyncMock(choices=[AsyncMock(message=AsyncMock(content=content))])


@pytest.mark.unit
class TestAgentGuardrailsInit:
    """Test Agent initialization with guardrails."""

    @patch("neosian._foundation.agent.base.create_guardrail_client")
    def test_agent_creates_guardrail_client_when_configured(
        self, mock_guardrail_client: AsyncMock
    ) -> None:
        """Agent should create guardrail client when guardrails configured."""
        mock_guardrail_client.return_value = AsyncMock()

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy=_POLICY,
                ),
            )
            agent = Agent(config=config)

            mock_guardrail_client.assert_called_once()
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

    @patch("neosian._foundation.agent.base.create_guardrail_client")
    def test_streaming_with_output_guardrails_raises_error(
        self, mock_guardrail_client: AsyncMock
    ) -> None:
        """stream=True with output guardrails should raise GuardrailStreamingError."""
        mock_guardrail_client.return_value = AsyncMock()

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    output_mode=GuardrailMode.POLICY_ONLY,
                    output_policy=_POLICY,
                ),
            )
            agent = Agent(config=config)

            with pytest.raises(GuardrailStreamingError):
                # Note: run() with stream=True is synchronous until iteration
                import asyncio

                asyncio.run(
                    agent.run([Message(role=Role.USER, content="Hi")], stream=True)
                )

    @patch("neosian._foundation.agent.base.create_guardrail_client")
    def test_streaming_with_input_guardrails_allowed(
        self, mock_guardrail_client: AsyncMock
    ) -> None:
        """stream=True with only input guardrails should be allowed."""
        mock_guardrail_client.return_value = AsyncMock()

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy=_POLICY,
                    output_mode=GuardrailMode.NONE,  # No output guardrails
                ),
            )
            agent = Agent(config=config)

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

    @pytest.mark.asyncio
    async def test_input_blocked_returns_blocked_response(self) -> None:
        """Flagged input with block_on_input=True should return blocked response."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_guardrail_client = AsyncMock()

        # Mock policy to return unsafe
        mock_guardrail_client.chat.completions.create.return_value = (
            _mock_guardrail_response(_UNSAFE_JSON)
        )

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy=_POLICY,
                    block_on_input=True,
                ),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Bad content")]
            response = await agent.run(messages, stream=False)

            # Response should be blocked with guardrail info
            assert response.blocked is True
            assert response.guardrail_result is not None
            assert response.guardrail_result.flagged_at == "input"
            assert response.guardrail_result.safe is False
            assert response.message.content == ""

            # Guardrail was called
            mock_guardrail_client.chat.completions.create.assert_called()

    @pytest.mark.asyncio
    async def test_input_flagged_not_blocked_continues(self) -> None:
        """Flagged input with block_on_input=False should continue execution."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Response"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        mock_guardrail_client = AsyncMock()
        # Mock policy to return unsafe
        mock_guardrail_client.chat.completions.create.return_value = (
            _mock_guardrail_response(_UNSAFE_JSON)
        )

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy=_POLICY,
                    block_on_input=False,  # Don't block
                ),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Bad content")]
            response = await agent.run(messages, stream=False)

            # Should have continued to LLM
            mock_client.complete.assert_called_once()
            assert response.message.content == "Response"

            # Guardrail result should still be populated
            assert response.guardrail_result is not None
            assert response.guardrail_result.input_policy is not None
            assert response.guardrail_result.input_policy.safe is False

    @pytest.mark.asyncio
    async def test_input_safe_continues_execution(self) -> None:
        """Safe input should continue to LLM execution."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        mock_guardrail_client = AsyncMock()
        # Mock policy to return safe
        mock_guardrail_client.chat.completions.create.return_value = (
            _mock_guardrail_response(_SAFE_JSON)
        )

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy=_POLICY,
                    block_on_input=True,
                ),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hello")]
            response = await agent.run(messages, stream=False)

            assert response.blocked is False
            assert response.message.content == "Hello!"
            mock_client.complete.assert_called_once()


@pytest.mark.unit
class TestAgentOutputGuardrails:
    """Test Agent output guardrails behavior."""

    @pytest.mark.asyncio
    async def test_output_flagged_sets_blocked(self) -> None:
        """Flagged output should set blocked=True and preserve content."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Bad output"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        mock_guardrail_client = AsyncMock()
        # Mock policy to return unsafe for output
        mock_guardrail_client.chat.completions.create.return_value = (
            _mock_guardrail_response(_UNSAFE_JSON)
        )

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    output_mode=GuardrailMode.POLICY_ONLY,
                    output_policy=_POLICY,
                ),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hello")]
            response = await agent.run(messages, stream=False)

            assert response.blocked is True
            assert response.message.content == "Bad output"  # Preserved for logging
            assert response.guardrail_result is not None
            assert response.guardrail_result.flagged_at == "output"
            assert response.guardrail_result.output_policy is not None
            assert response.guardrail_result.output_policy.category == "P1"

    @pytest.mark.asyncio
    async def test_output_safe_not_blocked(self) -> None:
        """Safe output should not be blocked."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Good output"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        mock_guardrail_client = AsyncMock()
        # Mock policy to return safe
        mock_guardrail_client.chat.completions.create.return_value = (
            _mock_guardrail_response(_SAFE_JSON)
        )

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    output_mode=GuardrailMode.POLICY_ONLY,
                    output_policy=_POLICY,
                ),
            )
            agent = Agent(config=config)

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

    @pytest.mark.asyncio
    async def test_fail_open_on_guardrail_error(self) -> None:
        """FAIL_OPEN policy should treat guardrail errors as safe."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Response"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        mock_guardrail_client = AsyncMock()
        # Simulate API error
        mock_guardrail_client.chat.completions.create.side_effect = Exception(
            "API timeout"
        )

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy=_POLICY,
                    block_on_input=True,
                    error_policy=GuardrailErrorPolicy.FAIL_OPEN,  # Default
                ),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hello")]
            response = await agent.run(messages, stream=False)

            # Should NOT be blocked - fail-open treats error as safe
            assert response.blocked is False
            assert response.message.content == "Response"

    @pytest.mark.asyncio
    async def test_fail_closed_on_guardrail_error(self) -> None:
        """FAIL_CLOSED policy should treat guardrail errors as blocked."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Response"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        mock_guardrail_client = AsyncMock()
        # Simulate API error
        mock_guardrail_client.chat.completions.create.side_effect = Exception(
            "API timeout"
        )

        with (
            patch(
                "neosian._foundation.agent.base.ProviderRouter",
                return_value=_create_mock_router(mock_client),
            ),
            patch(
                "neosian._foundation.agent.base.create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy=_POLICY,
                    block_on_input=True,
                    error_policy=GuardrailErrorPolicy.FAIL_CLOSED,
                ),
            )
            agent = Agent(config=config)

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
