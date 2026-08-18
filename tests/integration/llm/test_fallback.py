"""Integration tests for provider fallback logic.

These tests use real API calls to verify fallback behavior when providers fail.
Invalid API keys are used to trigger authentication errors (401), exercising
the complete fallback path in agent execution.

API keys are read from environment variables (matching tests/integration/conftest.py):
- GROQ_API_KEY
- OPENAI_API_KEY
- ANTHROPIC_API_KEY

Run with: uv run pytest tests/integration/llm/test_fallback.py -v
"""

import os
from unittest.mock import patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.exceptions import (
    FallbackExhaustedError,
    ModelFailedError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    FallbackConfig,
    Model,
    SystemPrompt,
)

# Invalid API keys that will trigger authentication errors
INVALID_GROQ_KEY = "gsk_invalid_test_key_12345"
INVALID_OPENAI_KEY = "sk-invalid_test_key_12345"
INVALID_ANTHROPIC_KEY = "sk-ant-invalid_test_key_12345"


@pytest.fixture
def valid_groq_key() -> str:
    """Fixture that provides valid Groq API key or skips test."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        pytest.skip("GROQ_API_KEY environment variable not set")
    return key


@pytest.fixture
def valid_openai_key() -> str:
    """Fixture that provides valid OpenAI API key or skips test."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY environment variable not set")
    return key


@pytest.fixture
def valid_anthropic_key() -> str:
    """Fixture that provides valid Anthropic API key or skips test."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY environment variable not set")
    return key


@pytest.mark.integration
class TestFallbackFirstProviderFails:
    """Test fallback when first provider fails with invalid API key."""

    @pytest.mark.asyncio
    async def test_groq_fails_openai_succeeds(self, valid_openai_key: str) -> None:
        """Test fallback from Groq (invalid key) to OpenAI (valid key).

        Scenario: Primary provider Groq fails with 401, should fall back to OpenAI.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,  # Will fail with 401
            "OPENAI_API_KEY": valid_openai_key,  # Will succeed
            "ANTHROPIC_API_KEY": "",  # Not available
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt(
                    "You are a helpful assistant. Reply concisely."
                ),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,  # Groq model
                fallback=FallbackConfig(model=Model.GPT_5_NANO),  # OpenAI fallback
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [
                Message(role=Role.USER, content="Say 'hello' and nothing else.")
            ]
            response = await agent.run(messages, stream=False)

            # Should have succeeded with OpenAI fallback
            assert response.message.content is not None
            assert len(response.message.content) > 0
            assert response.message.role == Role.ASSISTANT

    @pytest.mark.asyncio
    async def test_openai_fails_groq_succeeds(self, valid_groq_key: str) -> None:
        """Test fallback from OpenAI (invalid key) to Groq (valid key).

        Scenario: Primary provider OpenAI fails with 401, should fall back to Groq.
        """
        env = {
            "GROQ_API_KEY": valid_groq_key,  # Will succeed
            "OPENAI_API_KEY": INVALID_OPENAI_KEY,  # Will fail with 401
            "ANTHROPIC_API_KEY": "",  # Not available
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt(
                    "You are a helpful assistant. Reply concisely."
                ),
                tools=[],
                model=Model.GPT_5_MINI,  # OpenAI model
                fallback=FallbackConfig(model=Model.GROQ_QWEN3_6_27B),  # Groq fallback
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [
                Message(role=Role.USER, content="Say 'hello' and nothing else.")
            ]
            response = await agent.run(messages, stream=False)

            # Should have succeeded with Groq fallback
            assert response.message.content is not None
            assert len(response.message.content) > 0
            assert response.message.role == Role.ASSISTANT


@pytest.mark.integration
class TestNoFallbackConfigured:
    """Test behavior when no fallback is configured."""

    @pytest.mark.asyncio
    async def test_model_failed_error_when_no_fallback(self) -> None:
        """Test that ModelFailedError is raised when model fails with no fallback.

        Scenario: Model fails and no fallback is configured.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,  # Will fail
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a helpful assistant."),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,
                # No fallback configured
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Say hello")]

            with pytest.raises(ModelFailedError) as exc_info:
                await agent.run(messages, stream=False)

            # Verify the error indicates no fallback
            error = exc_info.value
            assert error.has_fallback is False
            assert "qwen/qwen3.6-27b" in error.model


@pytest.mark.integration
class TestFallbackExhausted:
    """Test that FallbackExhaustedError is raised when both models fail."""

    @pytest.mark.asyncio
    async def test_both_models_fail(self) -> None:
        """Test that FallbackExhaustedError is raised when both main and fallback fail.

        Scenario: Both main and fallback models have invalid API keys.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,
            "OPENAI_API_KEY": INVALID_OPENAI_KEY,
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a helpful assistant."),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,  # Will fail
                fallback=FallbackConfig(model=Model.GPT_5_NANO),  # Will also fail
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Say hello")]

            with pytest.raises(FallbackExhaustedError) as exc_info:
                await agent.run(messages, stream=False)

            # Verify the error contains both model information
            error = exc_info.value
            assert "qwen/qwen3.6-27b" in error.main_model
            assert "gpt-5-nano" in error.fallback_model


@pytest.mark.integration
class TestSingleProviderWorks:
    """Test behavior when only one provider is available and works."""

    @pytest.mark.asyncio
    async def test_single_provider_succeeds(self, valid_groq_key: str) -> None:
        """Test that single available provider works correctly.

        Scenario: Only Groq has API key, it should succeed directly.
        """
        env = {
            "GROQ_API_KEY": valid_groq_key,
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt(
                    "You are a helpful assistant. Reply concisely."
                ),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [
                Message(role=Role.USER, content="Say 'hello' and nothing else.")
            ]
            response = await agent.run(messages, stream=False)

            assert response.message.content is not None
            assert len(response.message.content) > 0


@pytest.mark.integration
class TestFallbackStreaming:
    """Test fallback in streaming mode."""

    @pytest.mark.asyncio
    async def test_streaming_fallback(self, valid_openai_key: str) -> None:
        """Test that fallback works in streaming mode.

        Scenario: Groq fails (invalid key), falls back to OpenAI, streams response.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,  # Will fail
            "OPENAI_API_KEY": valid_openai_key,  # Will succeed
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt(
                    "You are a helpful assistant. Reply concisely."
                ),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,
                fallback=FallbackConfig(model=Model.GPT_5_NANO),
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [
                Message(role=Role.USER, content="Say 'hello' and nothing else.")
            ]
            result = await agent.run(messages, stream=True)

            # Collect all SSE events
            events = []
            async for sse in result:
                events.append(sse)

            # Should have content and done events
            assert len(events) > 0
            # Last event should be done
            assert "done" in events[-1]

    @pytest.mark.asyncio
    async def test_streaming_fallback_exhausted(self) -> None:
        """Test that FallbackExhaustedError is raised in streaming mode.

        Scenario: Both providers fail, should raise when consuming stream.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,
            "OPENAI_API_KEY": INVALID_OPENAI_KEY,
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a helpful assistant."),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,
                fallback=FallbackConfig(model=Model.GPT_5_NANO),
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Say hello")]
            result = await agent.run(messages, stream=True)

            # The error should be raised when we try to consume the stream
            with pytest.raises(FallbackExhaustedError):
                async for _ in result:
                    pass

    @pytest.mark.asyncio
    async def test_streaming_no_fallback_raises_model_failed(self) -> None:
        """Test that ModelFailedError is raised in streaming with no fallback.

        Scenario: Model fails and no fallback configured, error during stream.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a helpful assistant."),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,
                # No fallback
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Say hello")]
            result = await agent.run(messages, stream=True)

            # The error should be raised when we try to consume the stream
            with pytest.raises(ModelFailedError) as exc_info:
                async for _ in result:
                    pass

            assert exc_info.value.has_fallback is False


@pytest.mark.integration
class TestStickyFallbackWithSession:
    """Test sticky fallback behavior with AgentSession."""

    @pytest.mark.asyncio
    async def test_sticky_fallback_stays_on_fallback(
        self, valid_openai_key: str
    ) -> None:
        """Test that session stays on fallback after main fails.

        Scenario: Main fails, fallback succeeds, subsequent calls use fallback.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,  # Will always fail
            "OPENAI_API_KEY": valid_openai_key,  # Will succeed
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt(
                    "You are a helpful assistant. Reply concisely."
                ),
                tools=[],
                model=Model.GROQ_QWEN3_6_27B,
                fallback=FallbackConfig(
                    model=Model.GPT_5_NANO,
                    retry_main_after=0,  # Never retry main
                ),
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [
                Message(role=Role.USER, content="Say 'hello' and nothing else.")
            ]

            async with agent.session() as session:
                # First call - main fails, fallback succeeds
                response1 = await session.run(messages, stream=False)
                assert response1.message.content is not None

                # Check fallback state
                assert session._fallback_state.using_fallback is True

                # Second call - should stay on fallback
                response2 = await session.run(messages, stream=False)
                assert response2.message.content is not None

                # Still on fallback
                assert session._fallback_state.using_fallback is True
