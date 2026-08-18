"""Unit tests for provider router."""

import os
from unittest.mock import patch

import pytest

from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.shared.types import Model, Provider


class TestProviderRouter:
    """Tests for ProviderRouter class."""

    @pytest.fixture
    def all_keys_available(self) -> dict[str, str]:
        """Return environment with all API keys set."""
        return {
            "GROQ_API_KEY": "test-groq-key",
            "OPENAI_API_KEY": "test-openai-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
        }

    @pytest.fixture
    def groq_only(self) -> dict[str, str]:
        """Return environment with only Groq key."""
        return {"GROQ_API_KEY": "test-groq-key"}

    def test_detect_available_providers_all(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test detecting all providers when all keys are set."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            assert router.has_provider(Provider.GROQ)
            assert router.has_provider(Provider.OPENAI)
            assert router.has_provider(Provider.ANTHROPIC)

    def test_detect_available_providers_partial(
        self, groq_only: dict[str, str]
    ) -> None:
        """Test detecting providers when only some keys are set."""
        with patch.dict(os.environ, groq_only, clear=True):
            router = ProviderRouter()
            assert router.has_provider(Provider.GROQ)
            assert not router.has_provider(Provider.OPENAI)
            assert not router.has_provider(Provider.ANTHROPIC)

    def test_detect_no_providers_when_no_keys(self) -> None:
        """Test detecting no providers when no keys are set."""
        with patch.dict(os.environ, {}, clear=True):
            router = ProviderRouter()
            assert not router.has_provider(Provider.GROQ)
            assert not router.has_provider(Provider.OPENAI)
            assert not router.has_provider(Provider.ANTHROPIC)

    def test_create_client_groq(self, groq_only: dict[str, str]) -> None:
        """Test creating Groq client."""
        with patch.dict(os.environ, groq_only, clear=True):
            router = ProviderRouter()
            client = router.create_client(Provider.GROQ)
            assert client is not None

    def test_create_client_openai(self, all_keys_available: dict[str, str]) -> None:
        """Test creating OpenAI client."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            client = router.create_client(Provider.OPENAI)
            assert client is not None

    def test_create_client_anthropic(self, all_keys_available: dict[str, str]) -> None:
        """Test creating Anthropic client."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            client = router.create_client(Provider.ANTHROPIC)
            assert client is not None


class TestModelEnum:
    """Tests for Model enum."""

    def test_model_has_provider(self) -> None:
        """Test that all models have a provider property."""
        for model in Model:
            assert hasattr(model, "provider")
            assert isinstance(model.provider, Provider)

    def test_model_has_max_output_tokens(self) -> None:
        """Test that all models have max_output_tokens property."""
        for model in Model:
            assert hasattr(model, "max_output_tokens")
            assert isinstance(model.max_output_tokens, int)
            assert model.max_output_tokens > 0

    def test_anthropic_models_have_expected_token_limits(self) -> None:
        """Test that Anthropic models have correct max output tokens."""
        assert Model.CLAUDE_OPUS_5.max_output_tokens == 128_000
        assert Model.CLAUDE_OPUS_4_6.max_output_tokens == 128_000
        assert Model.CLAUDE_SONNET_5.max_output_tokens == 128_000
        assert Model.CLAUDE_HAIKU_4_5.max_output_tokens == 64_000

    def test_model_has_context_window(self) -> None:
        """Test that all models have context_window property."""
        for model in Model:
            assert isinstance(model.context_window, int)
            assert model.context_window > 0

    def test_openai_models_have_400k_context_window(self) -> None:
        """Test that OpenAI models have 400k context window."""
        openai_models = [m for m in Model if m.provider == Provider.OPENAI]
        for model in openai_models:
            assert model.context_window == 400_000

    def test_anthropic_models_have_expected_context_windows(self) -> None:
        """Test that Anthropic models have the expected context windows."""
        assert Model.CLAUDE_OPUS_5.context_window == 1_000_000
        assert Model.CLAUDE_OPUS_4_6.context_window == 1_000_000
        assert Model.CLAUDE_SONNET_5.context_window == 1_000_000
        assert Model.CLAUDE_HAIKU_4_5.context_window == 200_000

    def test_model_value_is_string(self) -> None:
        """Test that Model enum values are strings (for API compatibility)."""
        for model in Model:
            assert isinstance(model.value, str)
            assert len(model.value) > 0
