"""`AgentConfig.timeout_seconds` reaches every SDK constructor; None keeps
each SDK's own default (NF #169, LL-21)."""

import anthropic
import openai
import pytest

from neosian import Agent, AgentConfig, Model, Provider
from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.openai import OpenAIClient
from neosian._foundation.llm.router import ProviderRouter


@pytest.mark.unit
class TestClientTimeout:
    def test_none_keeps_each_sdk_default(self) -> None:
        assert AnthropicClient("k")._client.timeout == anthropic.DEFAULT_TIMEOUT
        assert OpenAIClient("k")._client.timeout == openai.DEFAULT_TIMEOUT

    def test_a_deadline_reaches_the_sdk(self) -> None:
        assert AnthropicClient("k", timeout=1.5)._client.timeout == 1.5
        assert OpenAIClient("k", timeout=1.5)._client.timeout == 1.5

    def test_the_router_threads_it(self) -> None:
        router = ProviderRouter(timeout=2.5)
        for provider, cls in (
            (Provider.OPENAI, OpenAIClient),
            (Provider.ANTHROPIC, AnthropicClient),
        ):
            client = router.create_client(provider, api_key="k")
            assert isinstance(client, cls)
            assert client._client.timeout == 2.5
        door_client = router.create_client_for(Model.CEREBRAS_GPT_OSS_120B, "k")
        assert door_client._client.timeout == 2.5  # type: ignore[attr-defined]

    def test_the_config_reaches_the_router(self) -> None:
        agent = Agent(
            AgentConfig(system_prompt="s", model=Model.FAKE, timeout_seconds=3.0)
        )
        assert agent._router._timeout == 3.0
        assert (
            Agent(AgentConfig(system_prompt="s", model=Model.FAKE))._router._timeout
            is None
        )
