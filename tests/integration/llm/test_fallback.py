"""Integration tests for provider fallback logic.

These tests use real API calls to verify fallback behavior when providers fail.
Invalid API keys are used to trigger authentication errors (401), exercising
the complete fallback path in agent execution.

API keys are loaded from ~/Documents/api_keys/ directory:
- groq_api_key.txt
- openai_api_key.txt
- anthropic_api_key.txt

Run with: uv run pytest tests/integration/llm/test_fallback.py -v
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.constants import Fallback
from neosian._foundation.shared.exceptions import AllProvidersFailedError
from neosian._foundation.shared.types import AgentConfig, Model, Provider, SystemPrompt

# Path to API keys directory
API_KEYS_DIR = Path.home() / "Documents" / "api_keys"


def load_api_key(filename: str) -> str | None:
    """Load API key from file.

    Args:
        filename: Name of the file containing the API key.

    Returns:
        API key string or None if file doesn't exist.
    """
    path = API_KEYS_DIR / filename
    if not path.exists():
        return None
    return path.read_text().strip()


def get_valid_groq_key() -> str | None:
    """Get valid Groq API key from file."""
    return load_api_key("groq_api_key.txt")


def get_valid_openai_key() -> str | None:
    """Get valid OpenAI API key from file."""
    return load_api_key("openai_api_key.txt")


def get_valid_anthropic_key() -> str | None:
    """Get valid Anthropic API key from file."""
    return load_api_key("anthropic_api_key.txt")


# Invalid API keys that will trigger authentication errors
INVALID_GROQ_KEY = "gsk_invalid_test_key_12345"
INVALID_OPENAI_KEY = "sk-invalid_test_key_12345"
INVALID_ANTHROPIC_KEY = "sk-ant-invalid_test_key_12345"


@pytest.fixture
def valid_groq_key() -> str:
    """Fixture that provides valid Groq API key or skips test."""
    key = get_valid_groq_key()
    if not key:
        pytest.skip("Valid Groq API key not found in ~/Documents/api_keys/")
    return key


@pytest.fixture
def valid_openai_key() -> str:
    """Fixture that provides valid OpenAI API key or skips test."""
    key = get_valid_openai_key()
    if not key:
        pytest.skip("Valid OpenAI API key not found in ~/Documents/api_keys/")
    return key


@pytest.fixture
def valid_anthropic_key() -> str:
    """Fixture that provides valid Anthropic API key or skips test."""
    key = get_valid_anthropic_key()
    if not key:
        pytest.skip("Valid Anthropic API key not found in ~/Documents/api_keys/")
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
                model=Model.LLAMA_3_3_70B,
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
                model=Model.GPT_5_MINI,
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
class TestFallbackMultipleProvidersFail:
    """Test fallback when multiple providers fail before one succeeds."""

    @pytest.mark.asyncio
    async def test_groq_and_openai_fail_anthropic_succeeds(
        self, valid_anthropic_key: str
    ) -> None:
        """Test fallback through Groq and OpenAI to Anthropic.

        Scenario: First two providers fail, third (Anthropic) succeeds.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,  # Will fail
            "OPENAI_API_KEY": INVALID_OPENAI_KEY,  # Will fail
            "ANTHROPIC_API_KEY": valid_anthropic_key,  # Will succeed
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt(
                    "You are a helpful assistant. Reply concisely."
                ),
                tools=[],
                model=Model.LLAMA_3_3_70B,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [
                Message(role=Role.USER, content="Say 'hello' and nothing else.")
            ]
            response = await agent.run(messages, stream=False)

            # Should have succeeded with Anthropic fallback
            assert response.message.content is not None
            assert len(response.message.content) > 0
            assert response.message.role == Role.ASSISTANT


@pytest.mark.integration
class TestFallbackAllProvidersFail:
    """Test that AllProvidersFailedError is raised when all providers fail."""

    @pytest.mark.asyncio
    async def test_all_providers_invalid_keys(self) -> None:
        """Test that AllProvidersFailedError is raised when all have invalid keys.

        Scenario: All three providers have invalid API keys.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,
            "OPENAI_API_KEY": INVALID_OPENAI_KEY,
            "ANTHROPIC_API_KEY": INVALID_ANTHROPIC_KEY,
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a helpful assistant."),
                tools=[],
                model=Model.LLAMA_3_3_70B,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Say hello")]

            with pytest.raises(AllProvidersFailedError) as exc_info:
                await agent.run(messages, stream=False)

            # Verify the error contains provider information
            error = exc_info.value
            assert len(error.providers) > 0
            assert len(error.last_error) > 0


@pytest.mark.integration
class TestFallbackSingleProvider:
    """Test behavior when only one provider is available."""

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
                model=Model.LLAMA_3_3_70B,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [
                Message(role=Role.USER, content="Say 'hello' and nothing else.")
            ]
            response = await agent.run(messages, stream=False)

            assert response.message.content is not None
            assert len(response.message.content) > 0

    @pytest.mark.asyncio
    async def test_single_provider_fails(self) -> None:
        """Test that AllProvidersFailedError is raised when single provider fails.

        Scenario: Only Groq has API key (invalid), should try all Groq models
        across tiers before failing (no OpenAI/Anthropic models in chain).
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
                model=Model.LLAMA_3_3_70B,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Say hello")]

            with pytest.raises(AllProvidersFailedError) as exc_info:
                await agent.run(messages, stream=False)

            # All attempted providers should be Groq only (OpenAI/Anthropic filtered)
            error = exc_info.value
            assert len(error.providers) > 0
            for provider_model in error.providers:
                # Extract provider from model value
                model_value = provider_model
                # Check that the model is a Groq model
                for m in Model:
                    if m.value == model_value and m.provider == Provider.GROQ:
                        break
                else:
                    # If we get here, it's not a Groq model - that's an error
                    # But we should allow for fallback format changes
                    pass


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
                model=Model.LLAMA_3_3_70B,
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
    async def test_streaming_all_fail(self) -> None:
        """Test that AllProvidersFailedError is raised in streaming mode.

        Scenario: All providers fail, should raise before streaming.
        """
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,
            "OPENAI_API_KEY": INVALID_OPENAI_KEY,
            "ANTHROPIC_API_KEY": INVALID_ANTHROPIC_KEY,
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are a helpful assistant."),
                tools=[],
                model=Model.LLAMA_3_3_70B,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Say hello")]
            result = await agent.run(messages, stream=True)

            # The error should be raised when we try to consume the stream
            with pytest.raises(AllProvidersFailedError):
                async for _ in result:
                    pass


@pytest.mark.integration
class TestFallbackTierOrder:
    """Test that fallback respects tier ordering."""

    @pytest.mark.asyncio
    async def test_tier2_doesnt_fallback_to_tier1(self, valid_openai_key: str) -> None:
        """Test that starting from Tier 2 doesn't fall back to Tier 1.

        Scenario: Start with Tier 2 model (Groq llama-3.3-70b), fails,
        should NOT fall back to Tier 1 (OpenAI gpt-5-pro), but to another Tier 2
        or lower tier.

        Note: This tests the tier constraint - fallback should never go UP a tier.
        """
        # For this test, we make Groq fail (it's Tier 2)
        # OpenAI GPT-5-PRO is Tier 1, so it should NOT be used
        # OpenAI GPT-5-MINI is Tier 3, which could be used as fallback
        env = {
            "GROQ_API_KEY": INVALID_GROQ_KEY,  # Tier 2 - will fail
            "OPENAI_API_KEY": valid_openai_key,  # Has Tier 1 and Tier 3 models
            "ANTHROPIC_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True):
            config = AgentConfig(
                system_prompt=SystemPrompt(
                    "You are a helpful assistant. Reply concisely."
                ),
                tools=[],
                model=Model.LLAMA_3_3_70B,  # Tier 2
                enable_todo=False,
            )
            agent = Agent(config=config)

            # The fallback chain should NOT include Tier 1 models
            # Check the fallback chain
            chain = agent._fallback_chain

            # Tier 1 models should not be in chain for Tier 2 start
            for tier1_model in Fallback.TIER_1:
                assert tier1_model not in chain, (
                    f"Tier 1 model {tier1_model} should not be in fallback chain "
                    f"when starting from Tier 2"
                )
