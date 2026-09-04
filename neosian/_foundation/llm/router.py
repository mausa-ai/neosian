"""Provider router for LLM client creation.

Routes requests to LLM providers based on model configuration.
"""

import os

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import BaseLLMClient
from neosian._foundation.llm.cerebras import CerebrasClient
from neosian._foundation.llm.fake import FakeClient
from neosian._foundation.llm.openai import OpenAIClient, OpenAICompatibleClient
from neosian._foundation.shared.constants import (
    EnvVars,
    ErrorMessages,
    LLMDefaults,
)
from neosian._foundation.shared.exceptions import MissingAPIKeyError
from neosian._foundation.shared.types import AnyModel, Provider, RegisteredModel


class ProviderRouter:
    """Routes requests to providers based on model configuration.

    The router creates LLM clients for the requested provider and validates
    that API keys are available.

    Usage:
        router = ProviderRouter()
        if router.has_provider(Provider.ANTHROPIC):
            client = router.create_client(Provider.ANTHROPIC)
    """

    def __init__(
        self,
        max_retries: int = LLMDefaults.MAX_RETRIES,
        timeout: float | None = None,
    ) -> None:
        """Initialize the router and detect available API keys.

        Args:
            max_retries: Transport-level retries passed to each SDK client
                (429/5xx/connection errors, with native backoff).
            timeout: Per-request deadline in seconds passed to each SDK
                client; None keeps the SDK's default.
        """
        self._available_providers = self._detect_available_providers()
        self._max_retries = max_retries
        self._timeout = timeout

    def _detect_available_providers(self) -> set[Provider]:
        """Detect which providers have API keys configured.

        Returns:
            Set of Provider enums with available API keys.
        """
        available: set[Provider] = set()

        if os.environ.get(EnvVars.OPENAI_API_KEY):
            available.add(Provider.OPENAI)
        if os.environ.get(EnvVars.ANTHROPIC_API_KEY):
            available.add(Provider.ANTHROPIC)
        if os.environ.get(EnvVars.CEREBRAS_API_KEY):
            available.add(Provider.CEREBRAS)

        # FAKE needs no key — keyless boot (DESIGN §2, ECOSYSTEM §7).
        available.add(Provider.FAKE)

        return available

    def has_provider(self, provider: Provider) -> bool:
        """Check if a provider has an API key configured.

        Args:
            provider: Provider enum.

        Returns:
            True if provider has API key, False otherwise.
        """
        return provider in self._available_providers

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
        if provider == Provider.OPENAI:
            key = api_key or os.environ.get(EnvVars.OPENAI_API_KEY, "")
            return OpenAIClient(
                api_key=key, max_retries=self._max_retries, timeout=self._timeout
            )

        if provider == Provider.ANTHROPIC:
            key = api_key or os.environ.get(EnvVars.ANTHROPIC_API_KEY, "")
            return AnthropicClient(
                api_key=key, max_retries=self._max_retries, timeout=self._timeout
            )

        if provider == Provider.CEREBRAS:
            key = api_key or os.environ.get(EnvVars.CEREBRAS_API_KEY, "")
            return CerebrasClient(
                api_key=key, max_retries=self._max_retries, timeout=self._timeout
            )

        if provider == Provider.FAKE:
            # Canned, repeat-last behavior; scripted fakes are injected via
            # AgentConfig.client_factory, never through the router.
            return FakeClient()

        if provider == Provider.OPENAI_COMPATIBLE:
            raise ValueError(
                "Provider.OPENAI_COMPATIBLE has no client of its own: the client "
                "is built from a registered model's door — use create_client_for"
            )

        raise ValueError(ErrorMessages.UNSUPPORTED_PROVIDER.format(provider=provider))

    def create_client_for(
        self, model: AnyModel, api_key: str | None = None
    ) -> BaseLLMClient:
        """Create the client that serves `model` (DESIGN §19).

        Shipped models route by provider exactly as `create_client`; a
        registered model's door names the endpoint and the env var that
        signs requests to it — absence is loud, naming the variable.
        """
        if not isinstance(model, RegisteredModel):
            return self.create_client(model.provider, api_key)
        door = model.door
        key = api_key or os.environ.get(door.api_key_env, "")
        if not key:
            raise MissingAPIKeyError(
                f"{door.api_key_env} environment variable not set "
                f"(the key for the {door.name!r} door)"
            )
        return OpenAICompatibleClient(
            api_key=key, door=door, max_retries=self._max_retries, timeout=self._timeout
        )
