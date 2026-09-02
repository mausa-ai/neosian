"""AgentHooks + HookRunner dispatch semantics (DESIGN §3)."""

import asyncio
import logging

import pytest

from neosian._foundation.agent.hooks import (
    AgentHooks,
    FallbackEvent,
    HookRunner,
    LlmCallEvent,
    TurnEvent,
)
from neosian._foundation.shared.types import Provider


def _llm_event() -> LlmCallEvent:
    return LlmCallEvent(
        requested_model="fake",
        model="fake",
        provider=Provider.FAKE,
        iteration=0,
        streamed=False,
        usage=None,
        stop_reason="stop",
        duration_ms=1,
        error_code=None,
    )


def _fallback_event() -> FallbackEvent:
    return FallbackEvent(
        from_model="fake",
        to_model="fake-small",
        reason="boom",
        cause_code="llm_provider_error",
        provider_status=500,
        sticky=False,
        streamed=False,
    )


@pytest.mark.unit
class TestHookRunner:
    async def test_sync_callable_fires(self) -> None:
        seen: list[LlmCallEvent] = []
        runner = HookRunner(AgentHooks(on_llm_call=seen.append))
        await runner.llm_call(_llm_event())
        assert seen == [_llm_event()]

    async def test_async_callable_awaited(self) -> None:
        seen: list[FallbackEvent] = []

        async def hook(event: FallbackEvent) -> None:
            await asyncio.sleep(0)
            seen.append(event)

        runner = HookRunner(AgentHooks(on_fallback=hook))
        await runner.fallback(_fallback_event())
        assert seen == [_fallback_event()]

    async def test_exception_swallowed_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def bad(_event: LlmCallEvent) -> None:
            raise RuntimeError("hook exploded")

        runner = HookRunner(AgentHooks(on_llm_call=bad))
        with caplog.at_level(logging.ERROR, "neosian._foundation.agent.hooks"):
            await runner.llm_call(_llm_event())
        assert "on_llm_call" in caplog.text

    async def test_strict_reraises(self) -> None:
        def bad(_event: LlmCallEvent) -> None:
            raise RuntimeError("hook exploded")

        runner = HookRunner(AgentHooks(on_llm_call=bad, strict=True))
        with pytest.raises(RuntimeError, match="hook exploded"):
            await runner.llm_call(_llm_event())

    async def test_cancelled_error_propagates(self) -> None:
        """CancelledError is BaseException — never swallowed, strict or not."""

        async def cancelled(_event: LlmCallEvent) -> None:
            raise asyncio.CancelledError

        runner = HookRunner(AgentHooks(on_llm_call=cancelled))
        with pytest.raises(asyncio.CancelledError):
            await runner.llm_call(_llm_event())

    async def test_unregistered_hook_is_noop(self) -> None:
        runner = HookRunner(AgentHooks(on_llm_call=lambda _e: None))
        await runner.fallback(_fallback_event())  # no on_fallback: no raise

    def test_enabled_short_circuits(self) -> None:
        assert HookRunner(None).enabled is False
        assert HookRunner(AgentHooks()).enabled is False
        assert HookRunner(AgentHooks(on_turn=lambda _e: None)).enabled is True

    def test_events_are_frozen(self) -> None:
        event = _llm_event()
        with pytest.raises(AttributeError):
            event.model = "other"  # type: ignore[misc]
        hooks = AgentHooks()
        with pytest.raises(AttributeError):
            hooks.strict = True  # type: ignore[misc]

    def test_turn_event_shape(self) -> None:
        """TurnEvent is constructible with any response object (typed check
        rides on the agent integration tests once call sites land)."""
        assert TurnEvent.__dataclass_fields__.keys() == {
            "response",
            "streamed",
            "duration_ms",
        }


@pytest.mark.unit
class TestRunContext:
    """A context built without `started` still times from its birth (AG-11)."""

    async def test_started_defaults_to_now(self) -> None:
        from neosian._foundation.agent.base import Agent
        from neosian._foundation.agent.context import RunContext
        from neosian._foundation.agent.emit import emit_turn
        from neosian._foundation.agent.response import AgentResponse
        from neosian._foundation.llm.base import Message, Role
        from neosian._foundation.shared.types import AgentConfig, Model, SystemPrompt

        seen: list[TurnEvent] = []
        agent = Agent(
            config=AgentConfig(model=Model.FAKE, system_prompt=SystemPrompt("x"))
        )
        ctx = RunContext(
            agent=agent,
            acquire=agent._create_client,
            hooks=HookRunner(AgentHooks(on_turn=seen.append)),
        )
        await emit_turn(
            ctx,
            AgentResponse(message=Message(role=Role.ASSISTANT, content="hi")),
            streamed=False,
        )
        assert seen[0].duration_ms < 1000
