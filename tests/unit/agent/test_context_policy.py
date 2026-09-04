"""Agent integration of ContextPolicy — keyless via FakeProvider.

Covers the proactive pre-call raise (zero spend, zero client calls), the
opt-out, and the no-fallback-to-smaller-window rule on both paths
(DESIGN §5).
"""

import os
from typing import Any
from unittest.mock import patch

import pytest

from neosian import Agent, AgentConfig, ContextPolicy, FallbackConfig, Model
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import ContextWindowExceededError

OVERSIZED = [Message(role=Role.USER, content="x" * 40_000)]  # ~10k tokens
SMALL = [Message(role=Role.USER, content="hi")]


def _config(**overrides: Any) -> AgentConfig:
    defaults: dict[str, Any] = {
        "system_prompt": "sys",
        "tools": [],
        "enable_todo": False,
        "model": Model.FAKE_SMALL,  # 8_192-token window, by design
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


async def _run(agent: Agent, messages: list[Message], *, stream: bool) -> None:
    if stream:
        async for _ in await agent.run(messages, stream=True):
            pass
    else:
        await agent.run(messages, stream=False)


@pytest.mark.unit
class TestProactiveCheck:
    @pytest.mark.parametrize("stream", [False, True])
    async def test_oversized_prompt_fails_fast_and_free(self, stream: bool) -> None:
        fake = FakeClient()
        agent = Agent(_config(client_factory=lambda _: fake))
        with pytest.raises(ContextWindowExceededError) as exc_info:
            await _run(agent, OVERSIZED, stream=stream)
        error = exc_info.value
        assert error.context_window == 8_192
        assert error.estimated_tokens is not None
        assert error.estimated_tokens > 8_192
        assert fake.calls == []  # rejected before any spend

    @pytest.mark.parametrize("stream", [False, True])
    async def test_none_disables_the_check(self, stream: bool) -> None:
        fake = FakeClient()
        agent = Agent(_config(context_policy=None, client_factory=lambda _: fake))
        await _run(agent, OVERSIZED, stream=stream)
        assert len(fake.calls) == 1  # the call went through to the client

    async def test_keyless_boot_default_policy(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(_config())
            with pytest.raises(ContextWindowExceededError):
                await agent.run(OVERSIZED, stream=False)


@pytest.mark.unit
class TestFallbackWindowRule:
    @pytest.mark.parametrize("stream", [False, True])
    async def test_overflow_falls_back_onto_a_larger_window(self, stream: bool) -> None:
        """FAKE_SMALL (8k) overflows proactively; FAKE (128k) absorbs it."""
        fake = FakeClient()
        agent = Agent(
            _config(
                model=Model.FAKE_SMALL,
                fallback=FallbackConfig(model=Model.FAKE),
                client_factory=lambda _: fake,
            )
        )
        await _run(agent, OVERSIZED, stream=stream)
        assert [call.model for call in fake.calls] == [Model.FAKE]

    @pytest.mark.parametrize("stream", [False, True])
    async def test_overflow_never_falls_back_smaller(self, stream: bool) -> None:
        """A reactive overflow on FAKE (128k) must not retry on FAKE_SMALL."""
        script = FakeScript(
            turns=(
                FakeTurn(
                    error=ContextWindowExceededError(
                        Model.FAKE.value, context_window=128_000
                    )
                ),
            )
        )
        fake = FakeClient(script)
        agent = Agent(
            _config(
                model=Model.FAKE,
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                context_policy=None,  # force the reactive path
                client_factory=lambda _: fake,
            )
        )
        with pytest.raises(ContextWindowExceededError) as exc_info:
            await _run(agent, SMALL, stream=stream)
        assert exc_info.value.context_window == 128_000
        assert [call.model for call in fake.calls] == [Model.FAKE]  # no 2nd bill

    @pytest.mark.parametrize("stream", [False, True])
    async def test_no_fallback_surfaces_the_error_unwrapped(self, stream: bool) -> None:
        agent = Agent(
            _config(
                context_policy=ContextPolicy(), client_factory=lambda _: FakeClient()
            )
        )
        with pytest.raises(ContextWindowExceededError):
            await _run(agent, OVERSIZED, stream=stream)
