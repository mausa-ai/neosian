"""Unit tests for provider router."""

import os
from unittest.mock import patch

import pytest

from neosian._foundation.llm.router import (
    ProviderRouter,
    format_provider_model,
    parse_provider_model,
)
from neosian._foundation.shared.constants import Fallback, Provider


class TestParseProviderModel:
    """Tests for parse_provider_model function."""

    def test_parse_valid_format(self) -> None:
        """Test parsing a valid provider:model string."""
        provider, model = parse_provider_model("groq:llama-3.3-70b-versatile")
        assert provider == "groq"
        assert model == "llama-3.3-70b-versatile"

    def test_parse_with_colons_in_model(self) -> None:
        """Test parsing when model contains colons (like openai/gpt-oss-20b)."""
        # This shouldn't happen with our format, but let's handle it
        provider, model = parse_provider_model("groq:openai/gpt-oss-20b")
        assert provider == "groq"
        assert model == "openai/gpt-oss-20b"

    def test_parse_invalid_format_raises(self) -> None:
        """Test that invalid format raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            parse_provider_model("invalid-no-colon")
        assert "Invalid provider:model format" in str(exc_info.value)


class TestFormatProviderModel:
    """Tests for format_provider_model function."""

    def test_format_basic(self) -> None:
        """Test formatting provider and model into string."""
        result = format_provider_model("groq", "llama-3.3-70b-versatile")
        assert result == "groq:llama-3.3-70b-versatile"

    def test_roundtrip(self) -> None:
        """Test that format and parse are inverse operations."""
        original = "anthropic:claude-opus-4-5-20251101"
        provider, model = parse_provider_model(original)
        result = format_provider_model(provider, model)
        assert result == original


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
            assert router.has_provider(Provider.Groq.ID)
            assert router.has_provider(Provider.OpenAI.ID)
            assert router.has_provider(Provider.Anthropic.ID)

    def test_detect_available_providers_partial(
        self, groq_only: dict[str, str]
    ) -> None:
        """Test detecting providers when only some keys are set."""
        with patch.dict(os.environ, groq_only, clear=True):
            router = ProviderRouter()
            assert router.has_provider(Provider.Groq.ID)
            assert not router.has_provider(Provider.OpenAI.ID)
            assert not router.has_provider(Provider.Anthropic.ID)

    def test_fallback_chain_tier1_model(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test fallback chain starting from Tier 1 model."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            chain = router.get_fallback_chain(
                Provider.OpenAI.ID, Provider.OpenAI.Models.GPT_5_PRO
            )

            # First should be the selected model
            assert chain[0] == (Provider.OpenAI.ID, Provider.OpenAI.Models.GPT_5_PRO)

            # Should have other Tier 1 models next
            chain_strings = [f"{p}:{m}" for p, m in chain]

            # Verify Tier 1 models come before Tier 2
            tier1_models = set(Fallback.TIER_1)
            tier2_models = set(Fallback.TIER_2)

            tier1_indices = [
                i for i, s in enumerate(chain_strings) if s in tier1_models
            ]
            tier2_indices = [
                i for i, s in enumerate(chain_strings) if s in tier2_models
            ]

            if tier1_indices and tier2_indices:
                assert max(tier1_indices) < min(tier2_indices)

    def test_fallback_chain_filters_unavailable_providers(
        self, groq_only: dict[str, str]
    ) -> None:
        """Test that fallback chain excludes providers without API keys."""
        with patch.dict(os.environ, groq_only, clear=True):
            router = ProviderRouter()
            chain = router.get_fallback_chain(
                Provider.Groq.ID, Provider.Groq.Production.LLAMA_3_3_70B
            )

            # All entries should be Groq (only available provider)
            for provider_id, _ in chain:
                assert provider_id == Provider.Groq.ID

    def test_fallback_chain_cyclic_within_tier(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test that fallback cycles through tier from start position."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()

            # Start from middle of Tier 2
            chain = router.get_fallback_chain(
                Provider.Groq.ID, Provider.Groq.Production.LLAMA_3_3_70B
            )

            chain_strings = [f"{p}:{m}" for p, m in chain]

            # First should be the selected model
            assert (
                chain_strings[0]
                == f"{Provider.Groq.ID}:{Provider.Groq.Production.LLAMA_3_3_70B}"
            )

            # Other Tier 2 models should follow before Tier 3
            tier2_in_chain = [s for s in chain_strings if s in Fallback.TIER_2]
            tier3_in_chain = [s for s in chain_strings if s in Fallback.TIER_3]

            # All Tier 2 models should come before Tier 3
            tier2_last_idx = max(chain_strings.index(s) for s in tier2_in_chain)
            tier3_first_idx = (
                min(chain_strings.index(s) for s in tier3_in_chain)
                if tier3_in_chain
                else len(chain_strings)
            )
            assert tier2_last_idx < tier3_first_idx

    def test_fallback_chain_never_goes_up_tier(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test that fallback never goes up to higher capability tier."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()

            # Start from Tier 3 model
            chain = router.get_fallback_chain(
                Provider.Anthropic.ID, Provider.Anthropic.Models.CLAUDE_HAIKU_4_5
            )

            chain_strings = [f"{p}:{m}" for p, m in chain]

            # Should not contain any Tier 1 or Tier 2 models
            tier1_in_chain = [s for s in chain_strings if s in Fallback.TIER_1]
            tier2_in_chain = [s for s in chain_strings if s in Fallback.TIER_2]

            assert len(tier1_in_chain) == 0, f"Should not have Tier 1: {tier1_in_chain}"
            assert len(tier2_in_chain) == 0, f"Should not have Tier 2: {tier2_in_chain}"

    def test_fallback_chain_unknown_model(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test fallback for model not in any tier."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            chain = router.get_fallback_chain("groq", "unknown-model")

            # First should be the specified model
            assert chain[0] == ("groq", "unknown-model")

            # Should then have all tiers as fallback
            assert len(chain) > 1

    def test_create_client_groq(self, groq_only: dict[str, str]) -> None:
        """Test creating Groq client."""
        with patch.dict(os.environ, groq_only, clear=True):
            router = ProviderRouter()
            client = router.create_client(Provider.Groq.ID)
            assert client is not None

    def test_create_client_unsupported_raises(
        self, all_keys_available: dict[str, str]
    ) -> None:
        """Test that unsupported provider raises ValueError."""
        with patch.dict(os.environ, all_keys_available, clear=True):
            router = ProviderRouter()
            with pytest.raises(ValueError) as exc_info:
                router.create_client("unsupported")
            assert "Unsupported provider" in str(exc_info.value)


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

    def test_all_entries_valid_format(self) -> None:
        """Test that all tier entries are in provider:model format."""
        for tier in Fallback.ALL_TIERS:
            for entry in tier:
                # Should not raise
                provider, model = parse_provider_model(entry)
                assert provider in [
                    Provider.Groq.ID,
                    Provider.OpenAI.ID,
                    Provider.Anthropic.ID,
                ]
                assert len(model) > 0

    def test_tier_order(self) -> None:
        """Test that ALL_TIERS is in correct order."""
        assert Fallback.ALL_TIERS[0] == Fallback.TIER_1
        assert Fallback.ALL_TIERS[1] == Fallback.TIER_2
        assert Fallback.ALL_TIERS[2] == Fallback.TIER_3
        assert Fallback.ALL_TIERS[3] == Fallback.TIER_4
