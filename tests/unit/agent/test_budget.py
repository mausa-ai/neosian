"""The budget stop: a run may not bill past the ceiling it was given
(DESIGN §3, NC9 ledger #222).

The ceiling lives on the run's usage ledger, which is the one place every
billed call is folded — both drivers, every fallback rung, and the
guardrail classifier's own call — so these pin the rail once per path and
then pin the things only the ledger's position makes true: that a failed
leg's spend counts, that a crossed budget never buys a fallback attempt,
and that an unpriced model is honest about contributing nothing.
"""

from __future__ import annotations

import logging

import pytest

from neosian import (
    Agent,
    AgentConfig,
    BudgetExceededError,
    ErrorEvent,
    FallbackConfig,
    Model,
    OpenAICompatible,
    Provider,
    Tool,
    ToolResult,
    Usage,
    register_model,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.types import AnyModel, ToolCallId, ToolName

# Model.FAKE bills 1 µ$/input token and 10 µ$/output token, so this turn
# costs exactly 200 µ$ — two of them cross a 300 µ$ ceiling, one does not.
_TURN_USAGE = Usage(input_tokens=100, output_tokens=10)
_TURN_COST = 200
_USER = [Message(role=Role.USER, content="Hi")]


@Tool(name="ping", description="Ping")
async def ping() -> ToolResult[str]:
    return ToolResult.ok("pong")


def _script(turns: int = 2, usage: Usage = _TURN_USAGE) -> FakeScript:
    """A tool round then a final answer, each billing `usage`."""
    call = ToolCall(id=ToolCallId("c1"), name=ToolName("ping"), arguments={})
    return FakeScript(
        turns=(
            *(FakeTurn(content="", tool_calls=(call,), usage=usage),) * (turns - 1),
            FakeTurn(content="done", usage=usage),
        )
    )


def _agent(
    client: FakeClient,
    *,
    model: AnyModel = Model.FAKE,
    fallback: FallbackConfig | None = None,
    max_cost_micro_usd: int | None = None,
    max_total_tokens: int | None = None,
) -> Agent:
    return Agent(
        AgentConfig(
            system_prompt="You are a test agent.",
            model=model,
            tools=[ping],
            enable_todo=False,
            fallback=fallback,
            client_factory=lambda _m: client,
            max_cost_micro_usd=max_cost_micro_usd,
            max_total_tokens=max_total_tokens,
        )
    )


async def _run(agent: Agent, *, stream: bool) -> None:
    if not stream:
        await agent.run(_USER, stream=False)
        return
    async for _ in await agent.run(_USER, stream=True):
        pass


@pytest.mark.unit
class TestTheRailFires:
    @pytest.mark.parametrize("stream", [False, True], ids=["blocking", "streaming"])
    async def test_the_cost_ceiling_stops_the_run(self, stream: bool) -> None:
        """Both drivers stop at the same spend with the same details."""
        client = FakeClient(_script())
        with pytest.raises(BudgetExceededError) as info:
            await _run(_agent(client, max_cost_micro_usd=300), stream=stream)
        assert info.value.code == "agent_budget_exceeded"
        assert info.value.details == {
            "kind": "cost",
            "limit": 300,
            "spent": 2 * _TURN_COST,
        }

    @pytest.mark.parametrize("stream", [False, True], ids=["blocking", "streaming"])
    async def test_the_token_ceiling_stops_the_run(self, stream: bool) -> None:
        client = FakeClient(_script())
        with pytest.raises(BudgetExceededError) as info:
            await _run(_agent(client, max_total_tokens=150), stream=stream)
        assert info.value.kind == "tokens"
        assert info.value.spent == 220  # two turns of 110 tokens

    async def test_cached_tokens_count_toward_the_token_ceiling(self) -> None:
        """total_tokens is all four classes, not just input+output."""
        cached = Usage(
            input_tokens=1, output_tokens=1, cache_read_tokens=50, cache_write_tokens=50
        )
        client = FakeClient(_script(turns=1, usage=cached))
        with pytest.raises(BudgetExceededError) as info:
            await _run(_agent(client, max_total_tokens=10), stream=False)
        assert info.value.spent == 102

    async def test_nothing_is_billed_past_the_ceiling(self) -> None:
        """The run stops on the call that crossed — it never makes another."""
        client = FakeClient(_script(turns=4))
        with pytest.raises(BudgetExceededError):
            await _run(_agent(client, max_cost_micro_usd=300), stream=False)
        assert len(client.calls) == 2

    async def test_the_error_carries_what_was_billed(self) -> None:
        client = FakeClient(_script())
        with pytest.raises(BudgetExceededError) as info:
            await _run(_agent(client, max_cost_micro_usd=300), stream=False)
        assert info.value.usage == _TURN_USAGE + _TURN_USAGE
        assert [u.model for u in info.value.usage_by_model] == [Model.FAKE.value]

    async def test_the_streamed_path_relays_the_code(self) -> None:
        """run(stream=True) raises; a host's relay turns it into the frame."""
        client = FakeClient(_script())
        with pytest.raises(BudgetExceededError) as info:
            await _run(_agent(client, max_cost_micro_usd=300), stream=True)
        frame = ErrorEvent.from_exception(info.value, sequence=7)
        assert frame.code == "agent_budget_exceeded"
        assert frame.retryable is False
        assert frame.usage == _TURN_USAGE + _TURN_USAGE

    async def test_the_run_is_unbounded_by_default(self) -> None:
        client = FakeClient(_script())
        response = await _agent(client).run(_USER, stream=False)
        assert response.message.content == "done"


@pytest.mark.unit
class TestTheLedgerIsTheRightPlace:
    async def test_a_crossed_budget_never_buys_a_fallback_attempt(self) -> None:
        """The ceiling is the run's, not the model's: no rung can fix it,
        and trying one would bill past the cap."""
        client = FakeClient(_script(turns=4))
        agent = _agent(
            client,
            fallback=FallbackConfig(model=Model.FAKE_SMALL),
            max_cost_micro_usd=300,
        )
        with pytest.raises(BudgetExceededError):
            await _run(agent, stream=False)
        assert [call.model for call in client.calls] == [Model.FAKE, Model.FAKE]

    async def test_the_guardrail_classifier_spend_counts(self) -> None:
        """The classifier's call is billed, so it counts against the
        ceiling like any other — never undercount (TG-4)."""
        from neosian._foundation.shared.types import GuardrailMode, GuardrailsConfig

        guard = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(
                        content='{"violation": 0, "category": null, '
                        '"rationale": "safe"}',
                        usage=_TURN_USAGE,
                    ),
                )
            )
        )
        agent_fake = FakeClient(_script(turns=1))
        agent = Agent(
            AgentConfig(
                system_prompt="You are a test agent.",
                model=Model.FAKE,
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    input_mode=GuardrailMode.POLICY_ONLY,
                    input_policy="No unsafe content.",
                    model=Model.CLAUDE_HAIKU_4_5,
                ),
                client_factory=lambda m: (
                    guard if m.provider is Provider.ANTHROPIC else agent_fake
                ),
                max_cost_micro_usd=150,
            )
        )
        with pytest.raises(BudgetExceededError) as info:
            await agent.run(_USER, stream=False)
        assert len(guard.calls) == 1
        # The guard races the agent's call, so both are billed — and the
        # ledger carries both, priced per model: more than the agent's
        # call alone, split two ways.
        assert info.value.spent > _TURN_COST
        assert {u.model for u in info.value.usage_by_model} == {
            Model.FAKE.value,
            Model.CLAUDE_HAIKU_4_5.value,
        }


@pytest.mark.unit
class TestUnpricedModels:
    def _unpriced(self) -> AnyModel:
        return register_model(
            "budget-unpriced-model",
            provider=OpenAICompatible(name="budget-door", api_key_env="BUDGET_KEY"),
            context_window=128_000,
            max_output_tokens=8_192,
        )

    async def test_an_unpriced_model_contributes_nothing_to_the_cost_tally(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Usage.cost_micro_usd is None for it, so the cost rail cannot
        see its spend — a named hole, said out loud once per run."""
        client = FakeClient(_script(turns=6))
        agent = _agent(client, model=self._unpriced(), max_cost_micro_usd=1)
        with caplog.at_level(logging.WARNING):
            await _run(agent, stream=False)
        warnings = [r for r in caplog.records if "no verified pricing" in r.message]
        assert len(warnings) == 1

    async def test_the_token_ceiling_still_fires_on_an_unpriced_model(self) -> None:
        """max_total_tokens is the rail that works on every model."""
        client = FakeClient(_script(turns=4))
        agent = _agent(client, model=self._unpriced(), max_total_tokens=150)
        with pytest.raises(BudgetExceededError) as info:
            await _run(agent, stream=False)
        assert info.value.kind == "tokens"


@pytest.mark.unit
class TestConfigValidation:
    @pytest.mark.parametrize("value", [0, -1])
    def test_a_cost_ceiling_must_be_positive(self, value: int) -> None:
        with pytest.raises(UnsupportedParameterError, match="max_cost_micro_usd"):
            AgentConfig(system_prompt="s", model=Model.FAKE, max_cost_micro_usd=value)

    @pytest.mark.parametrize("value", [0, -1])
    def test_a_token_ceiling_must_be_positive(self, value: int) -> None:
        with pytest.raises(UnsupportedParameterError, match="max_total_tokens"):
            AgentConfig(system_prompt="s", model=Model.FAKE, max_total_tokens=value)

    def test_both_rails_are_off_by_default(self) -> None:
        config = AgentConfig(system_prompt="s", model=Model.FAKE)
        assert config.max_cost_micro_usd is None
        assert config.max_total_tokens is None


@pytest.mark.unit
class TestTheLedgerPricesAtTheRunsTtl:
    """A run writes every breakpoint at one lifetime, so pricing the whole
    of its cache-write tokens at that rate is exact (NC9 #227)."""

    async def test_an_hour_long_run_crosses_the_cap_a_five_minute_one_clears(
        self,
    ) -> None:
        # 300k cache-write on Model.FAKE: 1.25x input = 375_000 µ$ clears a
        # 400_000 ceiling, 2x = 600_000 µ$ does not.
        usage = Usage(input_tokens=0, output_tokens=0, cache_write_tokens=300_000)
        client = FakeClient(_script(turns=1, usage=usage))
        await _run(_agent(client, max_cost_micro_usd=400_000), stream=False)

        client = FakeClient(_script(turns=1, usage=usage))
        agent = Agent(
            AgentConfig(
                system_prompt="You are a test agent.",
                model=Model.FAKE,
                tools=[ping],
                enable_todo=False,
                client_factory=lambda _m: client,
                max_cost_micro_usd=400_000,
                cache_ttl="1h",
            )
        )
        with pytest.raises(BudgetExceededError) as info:
            await agent.run(_USER, stream=False)
        assert info.value.kind == "cost"
        assert info.value.spent == 600_000

    async def test_the_ttl_reaches_the_wire(self) -> None:
        client = FakeClient(_script(turns=1))
        agent = Agent(
            AgentConfig(
                system_prompt="You are a test agent.",
                model=Model.FAKE,
                enable_todo=False,
                client_factory=lambda _m: client,
                cache_ttl="1h",
            )
        )
        await agent.run(_USER, stream=False)
        assert client.calls[0].cache_ttl == "1h"

    async def test_five_minutes_is_the_default(self) -> None:
        client = FakeClient(_script(turns=1))
        await _run(_agent(client), stream=False)
        assert client.calls[0].cache_ttl == "5m"
