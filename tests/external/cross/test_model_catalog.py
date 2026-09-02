"""Model-catalog smoke test: every shipped model, and every door lane,
answers a 1-token request.

Catches silent provider catalog churn (deprecated/renamed model IDs) that
unit tests cannot see. Run per provider with the matching env var set:

    ANTHROPIC_API_KEY=sk-ant-xxx uv run pytest -m external_anthropic tests/external/cross -v
"""

import pytest

from neosian import AnyModel
from neosian._foundation.llm.base import BaseLLMClient, Message, Role
from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.shared.types import Model, Provider
from tests.external.lanes import LANES, Lane
from tests.external.pacing import PacedClient, Pacer

_PROVIDER_FIXTURES: dict[Provider, str] = {
    Provider.OPENAI: "openai_api_key",
    Provider.ANTHROPIC: "anthropic_api_key",
    Provider.CEREBRAS: "cerebras_api_key",
}

# Registry members the smoke test cannot reach, each with its reason —
# found by the first real run (NV, 2026-08-21), not assumptions.
_UNREACHABLE: dict[Model, str] = {
    Model.GPT_5_PRO: (
        "OpenAI serves gpt-5-pro only via the Responses API; the client "
        "speaks chat completions (404, observed 2026-08-21)"
    ),
}


async def _answers(client: BaseLLMClient, model: AnyModel) -> None:
    """A 404 / "model not found" here means the provider retired or renamed
    the id and the entry is stale."""
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


@pytest.mark.parametrize("model", list(Model), ids=lambda m: m.value)
async def test_model_answers_minimal_completion(
    model: Model, request: pytest.FixtureRequest
) -> None:
    """Each registry model must accept a minimal completion request."""
    if model.provider is Provider.FAKE:
        pytest.skip("FAKE models are keyless registry members (DESIGN §2)")
    if model in _UNREACHABLE:
        pytest.skip(_UNREACHABLE[model])
    fixture_name = _PROVIDER_FIXTURES[model.provider]
    request.getfixturevalue(fixture_name)  # skips when the env var is unset
    await _answers(ProviderRouter().create_client(model.provider), model)


@pytest.mark.parametrize("lane", LANES, ids=lambda lane: lane.name)
async def test_door_answers_minimal_completion(
    lane: Lane, request: pytest.FixtureRequest
) -> None:
    """A door's id resolves live, through the path a registered model
    takes (`create_client_for`, DESIGN §19.2) — the catalog's rows and the
    candidates alike."""
    request.getfixturevalue(lane.key_fixture)  # skips when unset
    model = lane.registered()
    client = ProviderRouter().create_client_for(model)
    pacer = Pacer.of(lane)  # the lane's one clock, shared with the probes
    await _answers(client if pacer is None else PacedClient(client, pacer), model)
