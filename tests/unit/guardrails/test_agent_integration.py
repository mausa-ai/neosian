"""Tests for Agent guardrails integration."""

from unittest.mock import AsyncMock, patch

import pytest

from neosian._foundation.agent.base import Agent, AgentResponse
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
    GuardrailMode,
    GuardrailsConfig,
    ModelId,
    SystemPrompt,
)


def _mock_create_client(
    provider_id: str | None,  # noqa: ARG001
) -> tuple[BaseLLMClient, ModelId]:
    """Mock client factory that returns a mock client."""
    client = AsyncMock(spec=BaseLLMClient)
    return client, ModelId("test-model")


@pytest.mark.unit
class TestAgentGuardrailsInit:
    """Test Agent initialization with guardrails."""

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    @patch("neosian._foundation.agent.base._create_guardrail_client")
    def test_agent_creates_guardrail_client_when_configured(
        self, mock_guardrail_client: AsyncMock
    ) -> None:
        """Agent should create guardrail client when guardrails configured."""
        mock_guardrail_client.return_value = AsyncMock()

        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            tools=[],
            enable_todo=False,
            guardrails=GuardrailsConfig(
                input_mode=GuardrailMode.CLASSIFIER_ONLY,
            ),
        )
        agent = Agent(config=config)

        mock_guardrail_client.assert_called_once()
        assert agent._guardrail_client is not None

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    def test_agent_no_guardrail_client_when_not_configured(self) -> None:
        """Agent should not create guardrail client when no guardrails."""
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

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    @patch("neosian._foundation.agent.base._create_guardrail_client")
    def test_streaming_with_output_guardrails_raises_error(
        self, mock_guardrail_client: AsyncMock
    ) -> None:
        """stream=True with output guardrails should raise GuardrailStreamingError."""
        mock_guardrail_client.return_value = AsyncMock()

        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            tools=[],
            enable_todo=False,
            guardrails=GuardrailsConfig(
                output_mode=GuardrailMode.CLASSIFIER_ONLY,
            ),
        )
        agent = Agent(config=config)

        with pytest.raises(GuardrailStreamingError):
            # Note: run() with stream=True is synchronous until iteration
            import asyncio

            asyncio.get_event_loop().run_until_complete(
                agent.run([Message(role=Role.USER, content="Hi")], stream=True)
            )

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    @patch("neosian._foundation.agent.base._create_guardrail_client")
    def test_streaming_with_input_guardrails_allowed(
        self, mock_guardrail_client: AsyncMock
    ) -> None:
        """stream=True with only input guardrails should be allowed."""
        mock_guardrail_client.return_value = AsyncMock()

        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            tools=[],
            enable_todo=False,
            guardrails=GuardrailsConfig(
                input_mode=GuardrailMode.CLASSIFIER_ONLY,
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
    """Test _extract_user_content method."""

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    def test_extracts_last_user_message(self) -> None:
        """Should extract content from last user message."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            tools=[],
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [
            Message(role=Role.USER, content="First message"),
            Message(role=Role.ASSISTANT, content="Response"),
            Message(role=Role.USER, content="Second message"),
        ]
        content = agent._extract_user_content(messages)
        assert content == "Second message"

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    def test_returns_empty_when_no_user_messages(self) -> None:
        """Should return empty string when no user messages."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            tools=[],
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [
            Message(role=Role.ASSISTANT, content="Hello"),
        ]
        content = agent._extract_user_content(messages)
        assert content == ""

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    def test_returns_empty_for_empty_messages(self) -> None:
        """Should return empty string for empty message list."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            tools=[],
            enable_todo=False,
        )
        agent = Agent(config=config)

        content = agent._extract_user_content([])
        assert content == ""

    @patch("neosian._foundation.agent.base._create_client", _mock_create_client)
    def test_skips_empty_user_content(self) -> None:
        """Should skip user messages with empty content."""
        config = AgentConfig(
            system_prompt=SystemPrompt("You are helpful."),
            tools=[],
            enable_todo=False,
        )
        agent = Agent(config=config)

        messages = [
            Message(role=Role.USER, content="First"),
            Message(role=Role.USER, content=""),  # Empty
        ]
        content = agent._extract_user_content(messages)
        assert content == "First"


@pytest.mark.unit
class TestAgentInputGuardrails:
    """Test Agent input guardrails behavior."""

    @pytest.mark.asyncio
    async def test_input_blocked_returns_blocked_response(self) -> None:
        """Flagged input with block_on_input=True should return blocked response."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_guardrail_client = AsyncMock()

        # Mock classifier to return unsafe
        mock_guardrail_client.chat.completions.create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content="unsafe\nS1"))]
        )

        with (
            patch(
                "neosian._foundation.agent.base._create_client",
                return_value=(mock_client, ModelId("test-model")),
            ),
            patch(
                "neosian._foundation.agent.base._create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.CLASSIFIER_ONLY,
                    block_on_input=True,
                ),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Bad content")]
            response = await agent.run(messages, stream=False)

            assert response.blocked is True
            assert response.guardrail_result is not None
            assert response.guardrail_result.blocked_at == "input"
            assert response.guardrail_result.safe is False
            assert response.message.content == ""

            # LLM should NOT have been called
            mock_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_input_flagged_not_blocked_continues(self) -> None:
        """Flagged input with block_on_input=False should continue execution."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Response"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model=ModelId("test-model"),
        )

        mock_guardrail_client = AsyncMock()
        # Mock classifier to return unsafe
        mock_guardrail_client.chat.completions.create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content="unsafe\nS1"))]
        )

        with (
            patch(
                "neosian._foundation.agent.base._create_client",
                return_value=(mock_client, ModelId("test-model")),
            ),
            patch(
                "neosian._foundation.agent.base._create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.CLASSIFIER_ONLY,
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
            assert response.guardrail_result.input_classifier is not None
            assert response.guardrail_result.input_classifier.safe is False

    @pytest.mark.asyncio
    async def test_input_safe_continues_execution(self) -> None:
        """Safe input should continue to LLM execution."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model=ModelId("test-model"),
        )

        mock_guardrail_client = AsyncMock()
        # Mock classifier to return safe
        mock_guardrail_client.chat.completions.create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content="safe"))]
        )

        with (
            patch(
                "neosian._foundation.agent.base._create_client",
                return_value=(mock_client, ModelId("test-model")),
            ),
            patch(
                "neosian._foundation.agent.base._create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.CLASSIFIER_ONLY,
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
            model=ModelId("test-model"),
        )

        mock_guardrail_client = AsyncMock()
        # Mock classifier to return unsafe for output
        mock_guardrail_client.chat.completions.create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content="unsafe\nS2"))]
        )

        with (
            patch(
                "neosian._foundation.agent.base._create_client",
                return_value=(mock_client, ModelId("test-model")),
            ),
            patch(
                "neosian._foundation.agent.base._create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    output_mode=GuardrailMode.CLASSIFIER_ONLY,
                ),
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hello")]
            response = await agent.run(messages, stream=False)

            assert response.blocked is True
            assert response.message.content == "Bad output"  # Preserved for logging
            assert response.guardrail_result is not None
            assert response.guardrail_result.blocked_at == "output"
            assert response.guardrail_result.output_classifier is not None
            assert response.guardrail_result.output_classifier.categories == ["S2"]

    @pytest.mark.asyncio
    async def test_output_safe_not_blocked(self) -> None:
        """Safe output should not be blocked."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Good output"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model=ModelId("test-model"),
        )

        mock_guardrail_client = AsyncMock()
        # Mock classifier to return safe
        mock_guardrail_client.chat.completions.create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content="safe"))]
        )

        with (
            patch(
                "neosian._foundation.agent.base._create_client",
                return_value=(mock_client, ModelId("test-model")),
            ),
            patch(
                "neosian._foundation.agent.base._create_guardrail_client",
                return_value=mock_guardrail_client,
            ),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    output_mode=GuardrailMode.CLASSIFIER_ONLY,
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
            model=ModelId("test-model"),
        )

        with patch(
            "neosian._foundation.agent.base._create_client",
            return_value=(mock_client, ModelId("test-model")),
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
