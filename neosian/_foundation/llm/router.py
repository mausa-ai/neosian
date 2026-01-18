"""Provider router with tier-aware fallback.

Routes requests to LLM providers with automatic fallback when providers fail.
"""

import logging
import os

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import BaseLLMClient
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.llm.openai import OpenAIClient
from neosian._foundation.shared.constants import (
    EnvVars,
    ErrorMessages,
    Fallback,
)
from neosian._foundation.shared.types import Model, Provider

logger = logging.getLogger(__name__)


class ProviderRouter:
    """Routes requests to providers with tier-aware fallback.

    The router builds a fallback chain based on the selected model's tier:
    1. Cycle through remaining models in the same tier
    2. Drop to the next tier, repeat
    3. Never go UP a tier
    4. Filter out models whose provider has no API key

    Usage:
        router = ProviderRouter()
        chain = router.get_fallback_chain(Model.LLAMA_3_3_70B)
        for model in chain:
            try:
                client = router.create_client(model.provider)
                # Use client...
                break
            except ProviderError:
                continue
    """

    def __init__(self) -> None:
        """Initialize the router and detect available API keys."""
        self._available_providers = self._detect_available_providers()

    def _detect_available_providers(self) -> set[Provider]:
        """Detect which providers have API keys configured.

        Returns:
            Set of Provider enums with available API keys.
        """
        available: set[Provider] = set()

        if os.environ.get(EnvVars.GROQ_API_KEY):
            available.add(Provider.GROQ)
        if os.environ.get(EnvVars.OPENAI_API_KEY):
            available.add(Provider.OPENAI)
        if os.environ.get(EnvVars.ANTHROPIC_API_KEY):
            available.add(Provider.ANTHROPIC)

        return available

    def has_provider(self, provider: Provider) -> bool:
        """Check if a provider has an API key configured.

        Args:
            provider: Provider enum.

        Returns:
            True if provider has API key, False otherwise.
        """
        return provider in self._available_providers

    def get_fallback_chain(self, model: Model) -> list[Model]:
        """Build fallback chain starting from the given model.

        The chain follows tier-aware cyclic fallback:
        1. Find the tier containing the model
        2. Cycle through remaining models in that tier
        3. Append all lower tiers in order
        4. Filter out providers without API keys

        Args:
            model: Starting model.

        Returns:
            Ordered list of Models to try.
        """
        chain: list[Model] = []

        # Find which tier contains the target model
        start_tier_idx = self._find_tier_index(model)

        # If model not in any tier, use it as primary with all tiers as fallback
        if start_tier_idx == -1:
            # Add the specified model as primary (if provider is available)
            if self.has_provider(model.provider):
                chain.append(model)

            # Add all tiers as fallback
            for tier in Fallback.ALL_TIERS:
                self._add_tier_to_chain(chain, tier, set())
        else:
            # Start from the model's tier and cycle from its position
            self._build_chain_from_tier(chain, start_tier_idx, model)

        return chain

    def _find_tier_index(self, target: Model) -> int:
        """Find which tier contains the target model.

        Args:
            target: Model to find.

        Returns:
            Tier index (0-3) or -1 if not found.
        """
        for idx, tier in enumerate(Fallback.ALL_TIERS):
            if target in tier:
                return idx
        return -1

    def _build_chain_from_tier(
        self,
        chain: list[Model],
        start_tier_idx: int,
        target: Model,
    ) -> None:
        """Build fallback chain starting from a specific tier and model.

        Args:
            chain: List to append results to.
            start_tier_idx: Index of the starting tier.
            target: The starting model.
        """
        seen: set[Model] = set()

        # Process starting tier with cyclic ordering from target position
        start_tier = Fallback.ALL_TIERS[start_tier_idx]
        self._add_tier_cyclic(chain, start_tier, target, seen)

        # Add remaining tiers in order
        for tier_idx in range(start_tier_idx + 1, len(Fallback.ALL_TIERS)):
            tier = Fallback.ALL_TIERS[tier_idx]
            self._add_tier_to_chain(chain, tier, seen)

    def _add_tier_cyclic(
        self,
        chain: list[Model],
        tier: tuple[Model, ...],
        target: Model,
        seen: set[Model],
    ) -> None:
        """Add tier models starting from target position, cycling through.

        Args:
            chain: List to append results to.
            tier: Tuple of Models in the tier.
            target: The starting model (should be in this tier).
            seen: Set of already-seen models.
        """
        # Find position of target in tier
        try:
            start_idx = tier.index(target)
        except ValueError:
            # Target not in tier, add all
            self._add_tier_to_chain(chain, tier, seen)
            return

        # Create ordered list: start from target, cycle through rest
        ordered = list(tier[start_idx:]) + list(tier[:start_idx])

        for model in ordered:
            if model in seen:
                continue
            seen.add(model)

            if self.has_provider(model.provider):
                chain.append(model)

    def _add_tier_to_chain(
        self,
        chain: list[Model],
        tier: tuple[Model, ...],
        seen: set[Model],
    ) -> None:
        """Add all models from a tier to the chain.

        Args:
            chain: List to append results to.
            tier: Tuple of Models.
            seen: Set of already-seen models.
        """
        for model in tier:
            if model in seen:
                continue
            seen.add(model)

            if self.has_provider(model.provider):
                chain.append(model)

    def create_client(
        self, provider: Provider, api_key: str | None = None
    ) -> BaseLLMClient:
        """Create an LLM client for the given provider.

        Args:
            provider: Provider enum.
            api_key: Optional API key. If None, reads from environment.

        Returns:
            Configured LLM client.

        Raises:
            ValueError: If provider is not supported.
            MissingAPIKeyError: If no API key available.
        """
        if provider == Provider.GROQ:
            key = api_key or os.environ.get(EnvVars.GROQ_API_KEY, "")
            return GroqClient(api_key=key)

        if provider == Provider.OPENAI:
            key = api_key or os.environ.get(EnvVars.OPENAI_API_KEY, "")
            return OpenAIClient(api_key=key)

        if provider == Provider.ANTHROPIC:
            key = api_key or os.environ.get(EnvVars.ANTHROPIC_API_KEY, "")
            return AnthropicClient(api_key=key)

        raise ValueError(ErrorMessages.UNSUPPORTED_PROVIDER.format(provider=provider))
