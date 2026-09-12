"""The call's tool scope: which tools, which choice, and where a schema
rides when tools are in play (DESIGN §3, NC9 ledger #224 and #225).

The scope is resolved once at the dispatch funnel and carried on the run
context, so these pin it where it lands: on the body the client is
handed. `FakeCall` snapshots every argument, which is why the assertions
read off `client.calls` rather than a mock's call_args.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from neosian import (
    Agent,
    AgentConfig,
    AgentResponse,
    ConfigurationError,
    Model,
    ResponseFormat,
    Tool,
    ToolChoice,
    ToolResult,
    Usage,
)
from neosian._foundation.agent.tool_scope import FINAL_TOOL
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.types import ToolCallId, ToolFunction, ToolName

_USER = [Message(role=Role.USER, content="Hi")]
_USAGE = Usage(input_tokens=10, output_tokens=1)


class Answer(BaseModel):
    text: str
    confidence: int


@Tool(name="ping", description="Ping")
async def ping() -> ToolResult[str]:
    return ToolResult.ok("pong")


@Tool(name="pong", description="Pong")
async def pong() -> ToolResult[str]:
    return ToolResult.ok("ping")


def _answer_call(**arguments: object) -> ToolCall:
    return ToolCall(
        id=ToolCallId("f1"), name=FINAL_TOOL, arguments=dict(arguments) or {}
    )


def _script(*turns: FakeTurn) -> FakeScript:
    return FakeScript(turns=turns)


def _text(content: str = "done") -> FakeScript:
    return _script(FakeTurn(content=content, usage=_USAGE))


def _agent(client: FakeClient, *, tools: list[ToolFunction] | None = None) -> Agent:
    return Agent(
        AgentConfig(
            system_prompt="You are a test agent.",
            model=Model.FAKE,
            tools=[ping, pong] if tools is None else tools,
            enable_todo=False,
            client_factory=lambda _m: client,
        )
    )


async def _run(
    agent: Agent,
    *,
    stream: bool,
    tools: list[str | ToolFunction] | None = None,
    tool_choice: ToolChoice | None = None,
    response_format: ResponseFormat | None = None,
) -> None:
    """Drive one run to exhaustion, discarding what it returns."""
    if not stream:
        await agent.run(
            _USER,
            stream=False,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
        )
        return
    async for _ in await agent.run(
        _USER, stream=True, tools=tools, tool_choice=tool_choice
    ):
        pass


async def _answer(
    agent: Agent,
    *,
    response_format: ResponseFormat,
    tool_choice: ToolChoice | None = None,
) -> AgentResponse:
    """One typed blocking run, returning its response."""
    return await agent.run(
        _USER,
        stream=False,
        response_format=response_format,
        tool_choice=tool_choice,
    )


@pytest.mark.unit
class TestTheChoiceIsAValueType:
    """ToolChoice's four constructors are the contract; the field pair
    behind them is what the converters read."""

    def test_the_four_shapes(self) -> None:
        assert ToolChoice.auto().mode == "auto"
        assert ToolChoice.required().mode == "required"
        assert ToolChoice.none().mode == "none"
        assert ToolChoice.tool("ping") == ToolChoice(mode="tool", name="ping")

    def test_only_a_forced_tool_may_name_one(self) -> None:
        with pytest.raises(UnsupportedParameterError):
            ToolChoice(mode="auto", name="ping")
        with pytest.raises(UnsupportedParameterError):
            ToolChoice(mode="tool", name="")

    def test_which_shapes_force_a_call(self) -> None:
        assert ToolChoice.required().forces_a_call
        assert ToolChoice.tool("ping").forces_a_call
        assert not ToolChoice.auto().forces_a_call
        assert not ToolChoice.none().forces_a_call


@pytest.mark.unit
class TestTheSubsetReachesTheWire:
    """`tools=` narrows the registry for one call, in the caller's order;
    the registry itself is untouched."""

    @pytest.mark.parametrize("stream", [False, True], ids=["blocking", "streaming"])
    async def test_none_sends_every_registered_tool(self, stream: bool) -> None:
        client = FakeClient(_text())
        await _run(_agent(client), stream=stream)
        assert [tool.name for tool in client.calls[0].tools] == ["ping", "pong"]

    @pytest.mark.parametrize("stream", [False, True], ids=["blocking", "streaming"])
    async def test_a_subset_sends_only_it(self, stream: bool) -> None:
        client = FakeClient(_text())
        await _run(_agent(client), stream=stream, tools=["pong"])
        assert [tool.name for tool in client.calls[0].tools] == ["pong"]

    async def test_the_empty_list_is_a_tool_free_call(self) -> None:
        client = FakeClient(_text())
        agent = _agent(client)
        await _run(agent, stream=False, tools=[])
        assert client.calls[0].tools == ()
        # The registry is the run's to narrow, never to lose.
        assert [d.name for d in agent._tool_definitions] == ["ping", "pong"]

    async def test_a_decorated_function_names_its_tool(self) -> None:
        client = FakeClient(_text())
        await _run(_agent(client), stream=False, tools=[ping])
        assert [tool.name for tool in client.calls[0].tools] == ["ping"]

    async def test_an_unregistered_name_is_refused_before_any_call(self) -> None:
        client = FakeClient(_text())
        with pytest.raises(ConfigurationError) as info:
            await _run(_agent(client), stream=False, tools=["nope"])
        assert "ping, pong" in str(info.value)
        assert client.calls == []


@pytest.mark.unit
class TestTheChoiceReachesTheWire:
    @pytest.mark.parametrize("stream", [False, True], ids=["blocking", "streaming"])
    async def test_the_choice_rides_the_call(self, stream: bool) -> None:
        client = FakeClient(_text())
        await _run(_agent(client), stream=stream, tool_choice=ToolChoice.required())
        assert client.calls[0].tool_choice == ToolChoice.required()

    async def test_no_choice_is_the_default(self) -> None:
        client = FakeClient(_text())
        await _run(_agent(client), stream=False)
        assert client.calls[0].tool_choice is None

    async def test_a_choice_without_tools_never_reaches_the_wire(self) -> None:
        """`none` beside an empty tool set would be a 400: the wire takes a
        choice only beside a tool list."""
        client = FakeClient(_text())
        await _run(
            _agent(client), stream=False, tools=[], tool_choice=ToolChoice.none()
        )
        assert client.calls[0].tools == ()
        assert client.calls[0].tool_choice is None

    async def test_a_forced_choice_needs_tools(self) -> None:
        client = FakeClient(_text())
        with pytest.raises(ConfigurationError):
            await _run(
                _agent(client),
                stream=False,
                tools=[],
                tool_choice=ToolChoice.required(),
            )
        assert client.calls == []

    async def test_a_forced_tool_must_be_in_this_run_s_scope(self) -> None:
        client = FakeClient(_text())
        with pytest.raises(ConfigurationError) as info:
            await _run(
                _agent(client),
                stream=False,
                tools=["ping"],
                tool_choice=ToolChoice.tool("pong"),
            )
        assert "'pong'" in str(info.value)


@pytest.mark.unit
class TestTheLastResortCall:
    """Once the tool budget is spent the last call sends no real tools, so
    a forced choice must not ride it — that would be a deadlock, the
    model required to call what it was not given."""

    async def test_a_forced_choice_is_dropped_with_the_tools(self) -> None:
        call = ToolCall(id=ToolCallId("c1"), name=ToolName("ping"), arguments={})
        client = FakeClient(
            FakeScript(
                turns=(FakeTurn(content="", tool_calls=(call,), usage=_USAGE),),
                repeat_last=True,
            )
        )
        agent = Agent(
            AgentConfig(
                system_prompt="You are a test agent.",
                model=Model.FAKE,
                tools=[ping],
                enable_todo=False,
                max_tool_iterations=2,
                client_factory=lambda _m: client,
            )
        )
        response = await agent.run(
            _USER, stream=False, tool_choice=ToolChoice.required()
        )
        assert response.iterations_exhausted
        last = client.calls[-1]
        assert last.tools == ()
        assert last.tool_choice is None


@pytest.mark.unit
class TestTheSchemaRidesAFinalTool:
    """A tool-enabled agent cannot be constrained to JSON by the wire while
    it is still calling tools, so the schema becomes one more tool (#225)."""

    async def test_the_final_tool_joins_the_list_and_the_wire_loses_the_format(
        self,
    ) -> None:
        client = FakeClient(_script(FakeTurn(tool_calls=(), usage=_USAGE)))
        await _run(
            _agent(client), stream=False, response_format=ResponseFormat(schema=Answer)
        )
        sent = client.calls[0]
        assert [tool.name for tool in sent.tools] == ["ping", "pong", FINAL_TOOL]
        # The schema is on the tool, so it is not also on the wire.
        assert sent.response_format is None
        final = sent.tools[-1]
        assert final.parameters["properties"].keys() == {"text", "confidence"}

    async def test_calling_it_ends_the_run_with_the_parsed_answer(self) -> None:
        client = FakeClient(
            _script(
                FakeTurn(
                    tool_calls=(_answer_call(text="hi", confidence=3),), usage=_USAGE
                )
            )
        )
        response = await _answer(
            _agent(client), response_format=ResponseFormat(schema=Answer)
        )
        assert isinstance(response.parsed, Answer)
        assert response.parsed.text == "hi"
        assert response.parsed.confidence == 3
        # It is the answer, not a tool that ran.
        assert response.tool_results == ()
        assert len(client.calls) == 1

    async def test_a_real_tool_still_runs_before_the_answer(self) -> None:
        ping_call = ToolCall(id=ToolCallId("c1"), name=ToolName("ping"), arguments={})
        client = FakeClient(
            _script(
                FakeTurn(tool_calls=(ping_call,), usage=_USAGE),
                FakeTurn(
                    tool_calls=(_answer_call(text="after", confidence=1),), usage=_USAGE
                ),
            )
        )
        response = await _answer(
            _agent(client), response_format=ResponseFormat(schema=Answer)
        )
        assert [result.data for result in response.tool_results] == ["pong"]
        assert isinstance(response.parsed, Answer)
        assert response.parsed.text == "after"

    async def test_without_tools_the_wire_still_carries_the_schema(self) -> None:
        client = FakeClient(_text('{"text": "plain", "confidence": 9}'))
        response = await _answer(
            _agent(client, tools=[]), response_format=ResponseFormat(schema=Answer)
        )
        assert client.calls[0].tools == ()
        assert client.calls[0].response_format is not None
        assert isinstance(response.parsed, Answer)

    async def test_tool_choice_none_leaves_the_schema_on_the_wire(self) -> None:
        """Nothing will be called, so no final tool is needed: the tools
        ride along as description and the wire constrains the answer."""
        client = FakeClient(_text('{"text": "none", "confidence": 0}'))
        response = await _answer(
            _agent(client),
            response_format=ResponseFormat(schema=Answer),
            tool_choice=ToolChoice.none(),
        )
        assert [tool.name for tool in client.calls[0].tools] == ["ping", "pong"]
        assert client.calls[0].response_format is not None
        assert isinstance(response.parsed, Answer)

    async def test_the_last_resort_call_still_forces_the_answer(self) -> None:
        """A typed run that spends its tool budget returns its type, not
        prose: the final tool stays on the last call, forced."""
        ping_call = ToolCall(id=ToolCallId("c1"), name=ToolName("ping"), arguments={})
        client = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(tool_calls=(ping_call,), usage=_USAGE),
                    FakeTurn(tool_calls=(ping_call,), usage=_USAGE),
                    FakeTurn(
                        tool_calls=(_answer_call(text="last", confidence=2),),
                        usage=_USAGE,
                    ),
                )
            )
        )
        agent = Agent(
            AgentConfig(
                system_prompt="You are a test agent.",
                model=Model.FAKE,
                tools=[ping],
                enable_todo=False,
                max_tool_iterations=2,
                client_factory=lambda _m: client,
            )
        )
        response = await agent.run(
            _USER, stream=False, response_format=ResponseFormat(schema=Answer)
        )
        last = client.calls[-1]
        assert [tool.name for tool in last.tools] == [FINAL_TOOL]
        assert last.tool_choice == ToolChoice.tool(FINAL_TOOL)
        assert isinstance(response.parsed, Answer)
        assert response.parsed.text == "last"

    async def test_a_tool_named_like_the_final_one_is_refused(self) -> None:
        @Tool(name=FINAL_TOOL, description="A collision")
        async def final_response() -> ToolResult[str]:
            return ToolResult.ok("no")

        client = FakeClient(_text())
        agent = _agent(client, tools=[final_response])
        with pytest.raises(ConfigurationError) as info:
            await _run(
                agent, stream=False, response_format=ResponseFormat(schema=Answer)
            )
        assert FINAL_TOOL in str(info.value)
        # Without a schema the same agent runs: the name is only taken
        # when something needs it.
        await _run(agent, stream=False)
        assert len(client.calls) == 1
