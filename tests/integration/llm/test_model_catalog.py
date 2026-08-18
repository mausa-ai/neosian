"""Model-catalog smoke test: every registered model answers a 1-token request.

Catches silent provider catalog churn (deprecated/renamed model IDs) that
unit tests cannot see. Run per provider with the matching env var set:

    GROQ_API_KEY=gsk_xxx uv run pytest tests/integration/llm/test_model_catalog.py -v
"""

import pytest

from neosian._foundation.llm.base import Message, Role
from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.shared.types import Model, Provider

_PROVIDER_FIXTURES: dict[Provider, str] = {
    Provider.GROQ: "groq_api_key",
    Provider.OPENAI: "openai_api_key",
    Provider.ANTHROPIC: "anthropic_api_key",
    Provider.CEREBRAS: "cerebras_api_key",
}


@pytest.mark.integration
@pytest.mark.parametrize("model", list(Model), ids=lambda m: m.value)
async def test_model_answers_minimal_completion(
    model: Model, request: pytest.FixtureRequest
) -> None:
    """Each registry model must accept a minimal completion request.

    A 404 / "model not found" failure here means the provider retired or
    renamed the ID and the registry entry is stale.
    """
    fixture_name = _PROVIDER_FIXTURES[model.provider]
    request.getfixturevalue(fixture_name)  # skips when the env var is unset

    router = ProviderRouter()
    client = router.create_client(model.provider)
    try:
        response = await client.complete(
            messages=[Message(role=Role.USER, content="Reply with OK.")],
            model=model,
            max_tokens=16,
        )
        assert response.model
        assert response.usage.output_tokens >= 0
    finally:
        await client.close()
