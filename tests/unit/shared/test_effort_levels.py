"""New effort levels are explicit capabilities, including on registered doors."""

import pytest

from neosian import (
    AgentConfig,
    ConfigurationError,
    FallbackConfig,
    Model,
    OpenAICompatible,
    ReasoningEffort,
    UnsupportedParameterError,
    register_model,
)
from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.openai import OpenAIClient

pytestmark = pytest.mark.unit

CURRENT = (
    Model.GPT_6_1_SOL,
    Model.GPT_6_ASTRA,
    Model.GPT_6_LUNA,
    Model.CLAUDE_SONNET_5_5,
    Model.CLAUDE_OPUS_5_5,
    Model.CLAUDE_FABLE_5_1,
)


@pytest.mark.parametrize("model", CURRENT)
@pytest.mark.parametrize(
    "effort", [e for e in ReasoningEffort if e is not ReasoningEffort.NONE]
)
def test_current_tiers_accept_every_positive_effort(
    model: Model, effort: ReasoningEffort
) -> None:
    assert (
        AgentConfig(
            system_prompt="x", model=model, reasoning_effort=effort
        ).reasoning_effort
        is effort
    )


@pytest.mark.parametrize("model", [m for m in CURRENT if m is not Model.GPT_6_LUNA])
def test_omitted_effort_is_not_explicit_none(model: Model) -> None:
    assert AgentConfig(system_prompt="x", model=model).reasoning_effort is None
    with pytest.raises(UnsupportedParameterError, match="none"):
        AgentConfig(
            system_prompt="x", model=model, reasoning_effort=ReasoningEffort.NONE
        )


def test_luna_accepts_explicit_none() -> None:
    assert (
        AgentConfig(
            system_prompt="x",
            model=Model.GPT_6_LUNA,
            reasoning_effort=ReasoningEffort.NONE,
        ).reasoning_effort
        is ReasoningEffort.NONE
    )


def test_new_effort_must_work_on_every_fallback() -> None:
    with pytest.raises(UnsupportedParameterError, match="none"):
        AgentConfig(
            system_prompt="x",
            model=Model.GPT_6_LUNA,
            reasoning_effort=ReasoningEffort.NONE,
            fallback=FallbackConfig(model=Model.GPT_6_1_SOL),
        )


@pytest.mark.asyncio
async def test_clients_validate_before_network_even_without_agent_config() -> None:
    from neosian import Message, Role

    clients = [
        (AnthropicClient(api_key="test"), Model.CLAUDE_OPUS_5_5),
        (OpenAIClient(api_key="test"), Model.GPT_6_ASTRA),
    ]
    for client, model in clients:
        try:
            with pytest.raises(UnsupportedParameterError, match="none"):
                await client.complete(
                    [Message(role=Role.USER, content="x")],
                    model,
                    reasoning_effort=ReasoningEffort.NONE,
                )
            with pytest.raises(UnsupportedParameterError, match="none"):
                async for _ in client.stream(
                    [Message(role=Role.USER, content="x")],
                    model,
                    reasoning_effort=ReasoningEffort.NONE,
                ):
                    pass
        finally:
            await client.close()


def test_registered_capabilities_must_match_the_door() -> None:
    door = OpenAICompatible(name="test", api_key_env="TEST_KEY")
    model = register_model(
        "effort-full",
        provider=door,
        context_window=10000,
        max_output_tokens=8192,
        supports_reasoning=True,
        supports_xhigh_effort=True,
        supports_no_effort=True,
    )
    assert model.supports_xhigh_effort and model.supports_no_effort
    AgentConfig(system_prompt="x", model=model, reasoning_effort=ReasoningEffort.XHIGH)
    with pytest.raises(ConfigurationError, match="reasoning-effort"):
        register_model(
            "effort-invalid",
            provider=door,
            context_window=10000,
            max_output_tokens=8192,
            supports_xhigh_effort=True,
        )
    with pytest.raises(ConfigurationError, match="reasoning-effort"):
        register_model(
            "effort-switch",
            provider=OpenAICompatible(
                name="switch", api_key_env="TEST_KEY", reasoning_effort=False
            ),
            context_window=10000,
            max_output_tokens=8192,
            supports_reasoning=True,
            supports_no_effort=True,
        )
