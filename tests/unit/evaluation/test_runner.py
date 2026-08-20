"""run_case — replace-not-mutate, hook composition, the turn loop (§13.5)."""

import json
from typing import Any

import pytest

from neosian._foundation.agent.hooks import AgentHooks, ToolEvent, TurnEvent
from neosian._foundation.evaluation.runner import run_case
from neosian._foundation.evaluation.types import (
    BASE_VARIANT,
    EvalCase,
    EvalTurn,
    Expectation,
    MatchMode,
    ValueMatcher,
    Variant,
)
from neosian._foundation.llm.base import Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalRunError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    FallbackConfig,
    Model,
    SystemPrompt,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult

NONE: frozenset[ToolName] = frozenset()


@Tool(name="lookup", description="Look something up")
async def lookup(q: str) -> ToolResult[str]:
    return ToolResult.ok(f"real:{q}")


def _config(**overrides: Any) -> AgentConfig:
    defaults: dict[str, Any] = {
        "system_prompt": SystemPrompt("You are a test agent."),
        "tools": [lookup],
        "model": Model.FAKE,
        "enable_todo": False,
    }
    return AgentConfig(**{**defaults, **overrides})


def _call(name: str = "lookup", **arguments: Any) -> ToolCall:
    return ToolCall(id=ToolCallId("c1"), name=ToolName(name), arguments=arguments)


def _tool_case(**kwargs: Any) -> EvalCase:
    return EvalCase(
        name="case",
        turns=(
            EvalTurn(
                user="find x",
                expect=Expectation(
                    tool=ToolName("lookup"),
                    params={"q": ValueMatcher(mode=MatchMode.EQUALS, value="x")},
                ),
            ),
        ),
        script=(
            FakeTurn(tool_calls=(_call(q="x"),)),
            FakeTurn(content="Found."),
        ),
        **kwargs,
    )


@pytest.mark.unit
class TestCallerConfigUntouched:
    async def test_nothing_on_the_base_config_changes(self) -> None:
        hooks = AgentHooks(on_turn=lambda _event: None)
        factory_calls: list[str] = []
        base = _config(hooks=hooks, client_factory=None)
        tools_before = base.tools
        variant = Variant(
            name="v",
            system_prompt=SystemPrompt("Variant prompt."),
            tool_descriptions={ToolName("lookup"): "Overridden"},
        )

        result = await run_case(base, variant, Model.FAKE_SMALL, _tool_case())

        assert result.passed is True
        assert base.model is Model.FAKE
        assert base.system_prompt == "You are a test agent."
        assert base.hooks is hooks
        assert base.tools is tools_before
        assert base.tools == [lookup]
        assert base.client_factory is None
        assert factory_calls == []

    async def test_result_carries_the_cell_axes(self) -> None:
        result = await run_case(_config(), BASE_VARIANT, Model.FAKE, _tool_case())
        assert (result.case, result.variant, result.model) == (
            "case",
            "base",
            "fake",
        )
        assert result.turns[0].tool_calls[0].executed is False
        assert result.tool_sequence == ("lookup",)


@pytest.mark.unit
class TestHookComposition:
    async def test_caller_hooks_still_fire(self) -> None:
        seen_tools: list[ToolEvent] = []
        seen_turns: list[TurnEvent] = []
        hooks = AgentHooks(on_tool=seen_tools.append, on_turn=seen_turns.append)

        result = await run_case(
            _config(hooks=hooks), BASE_VARIANT, Model.FAKE, _tool_case()
        )

        assert result.passed is True  # the harness capture still scored
        assert [e.name for e in seen_tools] == ["lookup"]
        assert len(seen_turns) == 1


@pytest.mark.unit
class TestScriptedClients:
    async def test_script_wins_over_a_caller_factory(self) -> None:
        bystander = FakeClient(FakeScript(turns=(FakeTurn(content="unused"),)))
        base = _config(client_factory=lambda _: bystander)
        result = await run_case(base, BASE_VARIANT, Model.FAKE, _tool_case())
        assert result.passed is True
        assert bystander.calls == []

    async def test_caller_factory_serves_scriptless_cases(self) -> None:
        fake = FakeClient(FakeScript(turns=(FakeTurn(content="hi there"),)))
        base = _config(client_factory=lambda _: fake)
        case = EvalCase(
            name="text",
            turns=(
                EvalTurn(
                    user="say hi",
                    expect=Expectation(
                        no_tool=True,
                        response=(ValueMatcher(mode=MatchMode.CONTAINS, value="hi"),),
                    ),
                ),
            ),
        )
        result = await run_case(base, BASE_VARIANT, Model.FAKE, case)
        assert result.passed is True
        assert len(fake.calls) == 1


@pytest.mark.unit
class TestConversationalThreading:
    async def test_stub_payload_is_what_the_model_sees(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(tool_calls=(_call(q="x"),)),
                    FakeTurn(content="drew it"),
                    FakeTurn(content="done"),
                )
            )
        )
        case = EvalCase(
            name="conv",
            turns=(
                EvalTurn(
                    user="find x",
                    expect=Expectation(tool=ToolName("lookup")),
                    tool_results={ToolName("lookup"): {"url": "https://x/y.png"}},
                ),
                EvalTurn(
                    user="thanks",
                    expect=Expectation(no_tool=True),
                ),
            ),
        )
        base = _config(client_factory=lambda _: fake)

        result = await run_case(base, BASE_VARIANT, Model.FAKE, case)

        assert result.passed is True
        # The turn-2 call replays turn 1 verbatim — including the stubbed
        # TOOL message carrying the configured payload.
        replayed = fake.calls[2].messages
        tool_messages = [m for m in replayed if m.role is Role.TOOL]
        assert len(tool_messages) == 1
        assert isinstance(tool_messages[0].content, str)
        payload = json.loads(tool_messages[0].content)
        assert payload == {"success": True, "data": {"url": "https://x/y.png"}}

    async def test_stop_on_failure_controls_later_turns(self) -> None:
        def case() -> EvalCase:
            return EvalCase(
                name="conv",
                turns=(
                    EvalTurn(user="a", expect=Expectation(tool=ToolName("lookup"))),
                    EvalTurn(user="b", expect=Expectation(no_tool=True)),
                ),
                script=(FakeTurn(content="no tool"), FakeTurn(content="still none")),
            )

        stopped = await run_case(_config(), BASE_VARIANT, Model.FAKE, case())
        assert stopped.passed is False
        assert stopped.total_turns == 1

        full = await run_case(
            _config(), BASE_VARIANT, Model.FAKE, case(), stop_on_failure=False
        )
        assert full.passed is False
        assert full.total_turns == 2
        assert full.turns[1].passed is True


@pytest.mark.unit
class TestExecuteAllowlist:
    async def test_allowlisted_tool_runs_for_real(self) -> None:
        log: list[str] = []

        @Tool(name="write", description="Write")
        async def write(value: str) -> ToolResult[str]:
            log.append(value)
            return ToolResult.ok("written")

        case = EvalCase(
            name="real",
            turns=(
                EvalTurn(user="write x", expect=Expectation(tool=ToolName("write"))),
            ),
            script=(
                FakeTurn(tool_calls=(_call("write", value="x"),)),
                FakeTurn(content="done"),
            ),
            execute_tools=frozenset({ToolName("write")}),
        )
        base = _config(tools=[write, lookup])

        result = await run_case(base, BASE_VARIANT, Model.FAKE, case)

        assert result.passed is True
        assert log == ["x"]
        assert result.turns[0].tool_calls[0].executed is True

    async def test_unknown_execute_name_is_a_case_error(self) -> None:
        case = _tool_case(execute_tools=frozenset({ToolName("memory")}))
        with pytest.raises(EvalCaseInvalidError, match="always execute"):
            await run_case(_config(), BASE_VARIANT, Model.FAKE, case)


@pytest.mark.unit
class TestHarnessFailures:
    async def test_fallback_fails_the_case(self) -> None:
        case = EvalCase(
            name="fb",
            turns=(EvalTurn(user="hi", expect=Expectation(no_tool=True)),),
            script=(
                FakeTurn(error=TimeoutError("main model down")),
                FakeTurn(content="recovered"),
            ),
        )
        base = _config(fallback=FallbackConfig(model=Model.FAKE_SMALL))

        result = await run_case(base, BASE_VARIANT, Model.FAKE, case)

        assert result.passed is False
        assert result.error is not None
        assert "Falling back from" in result.error

    async def test_exhausted_script_raises_eval_run_failed(self) -> None:
        case = EvalCase(
            name="short",
            turns=(
                EvalTurn(user="a", expect=Expectation(no_tool=True)),
                EvalTurn(user="b", expect=Expectation(no_tool=True)),
            ),
            script=(FakeTurn(content="only one"),),
        )
        with pytest.raises(EvalRunError) as excinfo:
            await run_case(_config(), BASE_VARIANT, Model.FAKE, case)
        assert excinfo.value.case == "short"
        assert excinfo.value.variant == "base"
