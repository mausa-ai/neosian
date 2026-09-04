"""The shared run-entry guards (DESIGN §3 found-bug register #1 and #2).

Identical scenarios over Agent.run AND AgentSession.run, asserting
identical raises and identical hook sequences (empty — guards fire
before any hook) — the session skipping guards was a defect class, not
a variant. Plus the model-preservation regression for the
dataclasses.replace fix.
"""

import os
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from neosian import (
    Agent,
    AgentConfig,
    AgentHooks,
    GuardrailMode,
    GuardrailsConfig,
    Model,
    PolicyBuilder,
    ResponseFormat,
)
from neosian._foundation.agent.hooks import HookRunner
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.llm.fake import FakeClient
from neosian._foundation.shared.exceptions import (
    GuardrailStreamingError,
    StructuredOutputStreamingError,
    StructuredOutputToolsError,
)
from neosian._foundation.shared.types import ToolName
from neosian._foundation.tools.base import Tool, ToolResult

_USER = [Message(role=Role.USER, content="Hi")]


def _recording_hooks(events: list[object]) -> AgentHooks:
    return AgentHooks(
        on_turn=events.append,
        on_llm_call=events.append,
        on_tool=events.append,
        on_fallback=events.append,
        strict=True,
    )


class _Answer(BaseModel):
    text: str


@Tool(name=ToolName("noop"), description="Do nothing")
async def _noop() -> ToolResult[str]:
    return ToolResult.ok("ok")


def _structured_agent() -> Agent:
    return Agent(
        AgentConfig(
            system_prompt="test",
            model=Model.FAKE,
            enable_todo=False,
        )
    )


def _tool_agent() -> Agent:
    return Agent(
        AgentConfig(
            system_prompt="test",
            model=Model.FAKE,
            enable_todo=False,
            tools=[_noop],
        )
    )


def _output_guarded_agent() -> Agent:
    with patch.dict(os.environ, {"CEREBRAS_API_KEY": "test-key"}):
        return Agent(
            AgentConfig(
                system_prompt="test",
                model=Model.FAKE,
                enable_todo=False,
                guardrails=GuardrailsConfig(
                    output_mode=GuardrailMode.POLICY_ONLY,
                    output_policy=PolicyBuilder.test(),
                ),
            )
        )


_SCENARIOS = [
    pytest.param(
        _structured_agent,
        {"stream": True, "response_format": ResponseFormat(schema=_Answer)},
        StructuredOutputStreamingError,
        id="structured-output-requires-blocking",
    ),
    pytest.param(
        _tool_agent,
        {"stream": False, "response_format": ResponseFormat(schema=_Answer)},
        StructuredOutputToolsError,
        id="structured-output-incompatible-with-tools",
    ),
    pytest.param(
        _output_guarded_agent,
        {"stream": True},
        GuardrailStreamingError,
        id="output-guardrails-require-blocking",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("make_agent", "kwargs", "expected"), _SCENARIOS)
class TestGuardsOnBothEntryPoints:
    async def test_agent_run_raises(
        self,
        make_agent: object,
        kwargs: dict[str, object],
        expected: type[Exception],
    ) -> None:
        agent = make_agent()  # type: ignore[operator]
        events: list[object] = []
        agent._hooks = HookRunner(_recording_hooks(events))
        with pytest.raises(expected):
            await agent.run(_USER, **kwargs)
        assert events == []  # guards precede every hook site

    async def test_session_run_raises_identically(
        self,
        make_agent: object,
        kwargs: dict[str, object],
        expected: type[Exception],
    ) -> None:
        agent = make_agent()  # type: ignore[operator]
        events: list[object] = []
        agent._hooks = HookRunner(_recording_hooks(events))
        async with agent.session() as session:
            with pytest.raises(expected):
                await session.run(_USER, **kwargs)
        assert events == []  # identical (empty) hook sequence to Agent.run


@pytest.mark.unit
class TestInputGuardModelPreservation:
    async def test_model_survives_input_guard_attachment(self) -> None:
        """The manual rebuild dropped model=; dataclasses.replace keeps it."""
        with patch.dict(os.environ, {"CEREBRAS_API_KEY": "test-key"}):
            agent = Agent(
                AgentConfig(
                    system_prompt="test",
                    model=Model.FAKE,
                    enable_todo=False,
                    guardrails=GuardrailsConfig(
                        input_mode=GuardrailMode.POLICY_ONLY,
                        input_policy=PolicyBuilder.test(),
                        block_on_input=False,
                    ),
                    client_factory=lambda _: FakeClient(),
                )
            )
            response = await agent.run(_USER, stream=False)
        assert response.model == Model.FAKE.value
        assert response.guardrail_result is not None
