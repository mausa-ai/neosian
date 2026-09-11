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
            "CEREBRAS_API_KEY": "test-cerebras-key",
            "OPENAI_API_KEY": "test-openai-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
        }

    @pytest.fixture
    def cerebras_only(self) -> dict[str, str]:
        """Return environment with only Cerebras key."""
        return {"CEREBRAS_API_KEY": "test-cerebras-key"}

    def test_detect_available_providers_all(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test detecting all providers when all keys are set."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            assert router.has_provider(Provider.CEREBRAS)
            assert router.has_provider(Provider.OPENAI)
            assert router.has_provider(Provider.ANTHROPIC)

    def test_detect_available_providers_partial(
        self, cerebras_only: dict[str, str]
    ) -> None:
        """Test detecting providers when only some keys are set."""
        with patch.dict(os.environ, cerebras_only, clear=True):
            router = ProviderRouter()
            assert router.has_provider(Provider.CEREBRAS)
            assert not router.has_provider(Provider.OPENAI)
            assert not router.has_provider(Provider.ANTHROPIC)

    def test_detect_no_providers_when_no_keys(self) -> None:
        """Test detecting no providers when no keys are set."""
        with patch.dict(os.environ, {}, clear=True):
            router = ProviderRouter()
            assert not router.has_provider(Provider.CEREBRAS)
            assert not router.has_provider(Provider.OPENAI)
            assert not router.has_provider(Provider.ANTHROPIC)

    def test_fake_is_always_available(self) -> None:
        """FAKE needs no key — the keyless-boot guarantee (ECOSYSTEM §7)."""
        with patch.dict(os.environ, {}, clear=True):
            router = ProviderRouter()
            assert router.has_provider(Provider.FAKE)

    def test_create_client_fake(self) -> None:
        """create_client(FAKE) returns a canned FakeClient, keylessly."""
        from neosian._foundation.llm.fake import FakeClient

        with patch.dict(os.environ, {}, clear=True):
            router = ProviderRouter()
            assert isinstance(router.create_client(Provider.FAKE), FakeClient)

    def test_create_client_cerebras(self, cerebras_only: dict[str, str]) -> None:
        """The Cerebras rows ride a door (#218): the model-keyed path serves them."""
        with patch.dict(os.environ, cerebras_only, clear=True):
            router = ProviderRouter()
            with pytest.raises(ValueError, match="create_client_for"):
                router.create_client(Provider.CEREBRAS)
            assert router.create_client_for(Model.CEREBRAS_GPT_OSS_120B) is not None

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
        assert Model.CLAUDE_FABLE_5_1.max_output_tokens == 128_000
        assert Model.CLAUDE_SONNET_5.max_output_tokens == 128_000
        assert Model.CLAUDE_HAIKU_4_5.max_output_tokens == 64_000

    def test_model_has_context_window(self) -> None:
        """Test that all models have context_window property."""
        for model in Model:
            assert isinstance(model.context_window, int)
            assert model.context_window > 0

    def test_openai_models_have_expected_context_windows(self) -> None:
        """The GPT-5.6 rows carry the 1,050,000 window; gpt-5.1 its 400k."""
        for model in (Model.GPT_5_6_SOL, Model.GPT_5_6_TERRA, Model.GPT_5_6_LUNA):
            assert model.context_window == 1_050_000
        assert Model.GPT_5_1.context_window == 400_000

    def test_anthropic_models_have_expected_context_windows(self) -> None:
        """Test that Anthropic models have the expected context windows."""
        assert Model.CLAUDE_OPUS_5.context_window == 1_000_000
        assert Model.CLAUDE_FABLE_5_1.context_window == 1_000_000
        assert Model.CLAUDE_SONNET_5.context_window == 1_000_000
        assert Model.CLAUDE_HAIKU_4_5.context_window == 200_000

    def test_model_value_is_string(self) -> None:
        """Test that Model enum values are strings (for API compatibility)."""
        for model in Model:
            assert isinstance(model.value, str)
            assert len(model.value) > 0
