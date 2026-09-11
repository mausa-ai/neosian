"""NC2's done-when: a registered model runs the quickstart and prices in
µ$ — keyless, FakeProvider-shaped (DESIGN §19)."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian import (
    Agent,
    AgentConfig,
    AnyModel,
    Conversation,
    DoneEvent,
    FallbackConfig,
    FileStore,
    GuardrailMode,
    GuardrailsConfig,
    Model,
    ModelPricing,
    OpenAICompatible,
    Provider,
    ReadyEvent,
    ReflectionConfig,
    RegisteredModel,
    register_model,
)
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.llm.base import ImageBlock, Message, Role, TextBlock, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    InvalidModelError,
    MissingAPIKeyError,
    ModelFailedError,
)

XAI = OpenAICompatible(
    name="xai", api_key_env="XAI_API_KEY", base_url="https://api.x.ai/v1"
)
DEEPSEEK = OpenAICompatible(
    name="deepseek", api_key_env="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com"
)
# 3 µ$ per input token, 15 µ$ per output token — hand-computable.
PRICING = ModelPricing(input_per_mtok=3_000_000, output_per_mtok=15_000_000)
_SYSTEM = "You are a test agent."
_USER = [Message(role=Role.USER, content="Hi")]
_USAGE = Usage(input_tokens=10, output_tokens=5)


def _grok(value: str = "grok-4", **overrides: object) -> RegisteredModel:
    kwargs: dict[str, object] = {
        "provider": XAI,
        "context_window": 131_072,
        "max_output_tokens": 16_384,
        "pricing": PRICING,
        "supports_reasoning": True,
    }
    kwargs.update(overrides)
    return register_model(value, **kwargs)  # type: ignore[arg-type]


def _fake(*turns: FakeTurn) -> FakeClient:
    if not turns:
        turns = (FakeTurn(content="scripted", usage=_USAGE),)
    return FakeClient(FakeScript(turns=turns, repeat_last=True))


def _config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "system_prompt": _SYSTEM,
        "enable_todo": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestTheQuickstart:
    async def test_blocking_run_prices_in_micro_usd(self) -> None:
        grok = _grok()
        fake = _fake()
        seen: list[AnyModel] = []

        def factory(model: AnyModel) -> FakeClient:
            seen.append(model)
            return fake

        agent = Agent(_config(model=grok, client_factory=factory))
        response = await agent.run(_USER, stream=False)

        assert response.message.content == "scripted"
        assert seen == [grok]  # the door itself, never collapsed to its row
        assert fake.calls[0].model is grok
        assert response.usage is not None
        assert response.usage.cost_micro_usd(grok) == 10 * 3 + 5 * 15
        assert [u.model for u in response.usage_by_model] == ["grok-4"]

    async def test_streaming_run_labels_the_ready_frame_with_the_door(self) -> None:
        grok = _grok()
        agent = Agent(_config(model=grok, client_factory=lambda _: _fake()))

        events = [event async for event in await agent.run(_USER, stream=True)]

        ready, done = events[0], events[-1]
        assert isinstance(ready, ReadyEvent)
        assert ready.provider == "xai"
        assert ready.requested_model == "grok-4"
        assert isinstance(done, DoneEvent)
        assert done.usage is not None
        assert done.usage.cost_micro_usd(grok) == 10 * 3 + 5 * 15

    async def test_conversation_quickstart(self, tmp_path: Path) -> None:
        grok = _grok()
        config = _config(model=grok, client_factory=lambda _: _fake())
        convo = Conversation(
            Agent(config),
            store=FileStore(tmp_path),
            conversation_id="thread-1",
            memory_scope="user:1",
            reflection=ReflectionConfig(enabled=False),
        )
        async with convo:
            response = await convo.send("Where did we leave off?")

        assert response.message.content == "scripted"
        assert response.usage is not None
        assert response.usage.cost_micro_usd(grok) == 10 * 3 + 5 * 15
        assert config.model is grok  # the caller's config is never mutated


@pytest.mark.unit
class TestTheGates:
    async def test_falls_back_onto_a_registered_model(self) -> None:
        grok = _grok()
        clients = {
            Provider.FAKE: _fake(FakeTurn(error=TimeoutError("main down"))),
            Provider.OPENAI_COMPATIBLE: _fake(),
        }
        agent = Agent(
            _config(
                model=Model.FAKE,
                fallback=FallbackConfig(model=grok),
                client_factory=lambda model: clients[model.provider],
            )
        )
        response = await agent.run(_USER, stream=False)

        assert response.message.content == "scripted"
        assert response.model == "grok-4"

    async def test_media_history_skips_the_text_only_door(self) -> None:
        grok = _grok()
        clients = {
            Provider.FAKE: _fake(FakeTurn(error=TimeoutError("main down"))),
            Provider.OPENAI_COMPATIBLE: _fake(),
        }
        agent = Agent(
            _config(
                model=Model.FAKE,
                fallback=FallbackConfig(model=grok),
                client_factory=lambda model: clients[model.provider],
            )
        )
        with_image = [
            Message(
                role=Role.USER,
                content=[
                    TextBlock(text="what is this"),
                    ImageBlock(media_type="image/png", data="aGk="),
                ],
            )
        ]
        with pytest.raises(ModelFailedError):
            await agent.run(with_image, stream=False)
        assert len(clients[Provider.OPENAI_COMPATIBLE].calls) == 0

    async def test_overflow_never_falls_back_onto_a_smaller_window(self) -> None:
        grok = _grok()
        overflow = ContextWindowExceededError(
            "grok-4", context_window=131_072, provider="xai"
        )
        clients = {
            Provider.OPENAI_COMPATIBLE: _fake(FakeTurn(error=overflow)),
            Provider.FAKE: _fake(),
        }
        agent = Agent(
            _config(
                model=grok,
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda model: clients[model.provider],
            )
        )
        with pytest.raises(ContextWindowExceededError) as exc_info:
            await agent.run(_USER, stream=False)
        assert exc_info.value is overflow
        assert len(clients[Provider.FAKE].calls) == 0

    async def test_context_policy_names_the_door(self) -> None:
        tiny = _grok("grok-tiny", context_window=50)
        fake = _fake()
        agent = Agent(_config(model=tiny, client_factory=lambda _: fake))

        with pytest.raises(ContextWindowExceededError) as exc_info:
            await agent.run(
                [Message(role=Role.USER, content="x" * 2_000)], stream=False
            )
        assert exc_info.value.provider == "xai"
        assert exc_info.value.model == "grok-tiny"
        assert fake.calls == []


@pytest.mark.unit
class TestKeysAndConfig:
    async def test_missing_door_key_names_the_env_var(self) -> None:
        grok = _grok()
        agent = Agent(_config(model=grok))
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ModelFailedError, match="XAI_API_KEY") as exc_info,
        ):
            await agent.run(_USER, stream=False)
        assert isinstance(exc_info.value.__cause__, MissingAPIKeyError)

    def test_guardrails_on_a_door_need_its_key_at_construction(self) -> None:
        grok = _grok()
        guardrails = GuardrailsConfig(
            input_mode=GuardrailMode.POLICY_ONLY, input_policy="Be kind."
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(MissingAPIKeyError, match="XAI_API_KEY"),
        ):
            Agent(_config(model=grok, guardrails=guardrails))
        # A factory owns credentials — construction is keyless through it.
        Agent(
            _config(model=grok, guardrails=guardrails, client_factory=lambda _: _fake())
        )

    def test_invalid_model_text_lists_registered_ids(self) -> None:
        grok = _grok()
        assert _config(model="grok-4").model is grok  # a registered id resolves (§31)
        with pytest.raises(InvalidModelError) as exc_info:
            _config(model="grok-5")
        assert "Model.FAKE" in str(exc_info.value)
        assert "'grok-4'" in str(exc_info.value)

    def test_session_caches_one_client_per_door(self) -> None:
        grok = _grok()
        grok_mini = _grok("grok-4-mini")
        deepseek = register_model(
            "deepseek-chat",
            provider=DEEPSEEK,
            context_window=65_536,
            max_output_tokens=8_192,
        )
        created: list[FakeClient] = []

        def factory(_: AnyModel) -> FakeClient:
            created.append(_fake())
            return created[-1]

        session = AgentSession(Agent(_config(model=grok, client_factory=factory)))

        first = session._get_or_create_client(grok)
        assert session._get_or_create_client(grok) is first
        assert session._get_or_create_client(grok_mini) is first
        assert session._get_or_create_client(deepseek) is not first
        assert session._get_or_create_client(Model.FAKE) is not first
        assert len(created) == 3
