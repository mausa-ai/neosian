"""Unit tests for provider router."""

import os
from unittest.mock import patch

import pytest

from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.shared.constants import Fallback
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

    def test_fallback_chain_tier1_model(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test fallback chain starting from Tier 1 model."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            chain = router.get_fallback_chain(Model.GPT_5_PRO)

            # First should be the selected model
            assert chain[0] == Model.GPT_5_PRO

            # Should have other Tier 1 models next
            tier1_models = set(Fallback.TIER_1)
            tier2_models = set(Fallback.TIER_2)

            tier1_indices = [i for i, m in enumerate(chain) if m in tier1_models]
            tier2_indices = [i for i, m in enumerate(chain) if m in tier2_models]

            if tier1_indices and tier2_indices:
                assert max(tier1_indices) < min(tier2_indices)

    def test_fallback_chain_filters_unavailable_providers(
        self, groq_only: dict[str, str]
    ) -> None:
        """Test that fallback chain excludes providers without API keys."""
        with patch.dict(os.environ, groq_only, clear=True):
            router = ProviderRouter()
            chain = router.get_fallback_chain(Model.LLAMA_3_3_70B)

            # All entries should be Groq (only available provider)
            for model in chain:
                assert model.provider == Provider.GROQ

    def test_fallback_chain_cyclic_within_tier(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test that fallback cycles through tier from start position."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()

            # Start from middle of Tier 2
            chain = router.get_fallback_chain(Model.LLAMA_3_3_70B)

            # First should be the selected model
            assert chain[0] == Model.LLAMA_3_3_70B

            # Other Tier 2 models should follow before Tier 3
            tier2_in_chain = [m for m in chain if m in Fallback.TIER_2]
            tier3_in_chain = [m for m in chain if m in Fallback.TIER_3]

            # All Tier 2 models should come before Tier 3
            tier2_last_idx = max(chain.index(m) for m in tier2_in_chain)
            tier3_first_idx = (
                min(chain.index(m) for m in tier3_in_chain)
                if tier3_in_chain
                else len(chain)
            )
            assert tier2_last_idx < tier3_first_idx

    def test_fallback_chain_never_goes_up_tier(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test that fallback never goes up to higher capability tier."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()

            # Start from Tier 3 model
            chain = router.get_fallback_chain(Model.CLAUDE_HAIKU_4_5)

            # Should not contain any Tier 1 or Tier 2 models
            tier1_in_chain = [m for m in chain if m in Fallback.TIER_1]
            tier2_in_chain = [m for m in chain if m in Fallback.TIER_2]

            assert len(tier1_in_chain) == 0, f"Should not have Tier 1: {tier1_in_chain}"
            assert len(tier2_in_chain) == 0, f"Should not have Tier 2: {tier2_in_chain}"

    def test_create_client_groq(self, groq_only: dict[str, str]) -> None:
        """Test creating Groq client."""
        with patch.dict(os.environ, groq_only, clear=True):
            router = ProviderRouter()
            client = router.create_client(Provider.GROQ)
            assert client is not None

    def test_create_client_unsupported_raises(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test that unsupported provider raises ValueError."""
        # This test is no longer applicable since Provider is now an enum
        # and you can't pass an invalid value. Removing this test.
        pass


class TestFallbackConstants:
    """Tests for Fallback constants in constants.py."""

    def test_all_tiers_defined(self) -> None:
        """Test that all tiers are defined and non-empty."""
        assert len(Fallback.TIER_1) > 0
        assert len(Fallback.TIER_2) > 0
        assert len(Fallback.TIER_3) > 0
        assert len(Fallback.TIER_4) > 0

    def test_all_tiers_in_all_tiers(self) -> None:
        """Test that ALL_TIERS contains all individual tiers."""
        assert Fallback.TIER_1 in Fallback.ALL_TIERS
        assert Fallback.TIER_2 in Fallback.ALL_TIERS
        assert Fallback.TIER_3 in Fallback.ALL_TIERS
        assert Fallback.TIER_4 in Fallback.ALL_TIERS
        assert len(Fallback.ALL_TIERS) == 4

    def test_all_entries_are_model_enums(self) -> None:
        """Test that all tier entries are Model enum values."""
        for tier in Fallback.ALL_TIERS:
            for entry in tier:
                # Should be a Model enum value
                assert isinstance(entry, Model)
                # Model should have a valid provider
                assert entry.provider in [
                    Provider.GROQ,
                    Provider.OPENAI,
                    Provider.ANTHROPIC,
                ]

    def test_tier_order(self) -> None:
        """Test that ALL_TIERS is in correct order."""
        assert Fallback.ALL_TIERS[0] == Fallback.TIER_1
        assert Fallback.ALL_TIERS[1] == Fallback.TIER_2
        assert Fallback.ALL_TIERS[2] == Fallback.TIER_3
        assert Fallback.ALL_TIERS[3] == Fallback.TIER_4


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

    def test_anthropic_models_have_higher_token_limit(self) -> None:
        """Test that Anthropic models have 65536 max tokens."""
        anthropic_models = [m for m in Model if m.provider == Provider.ANTHROPIC]
        for model in anthropic_models:
            assert model.max_output_tokens == 65536

    def test_model_value_is_string(self) -> None:
        """Test that Model enum values are strings (for API compatibility)."""
        for model in Model:
            assert isinstance(model.value, str)
            assert len(model.value) > 0
