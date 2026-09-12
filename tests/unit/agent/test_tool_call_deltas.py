"""The `tool_call_delta` frame: a tool call's arguments as they arrive
(DESIGN §6, ECOSYSTEM §5, NC9 ledger #226).

Opt-in, because it is the one frame a turn can emit many of per call. The
pins that matter are the two halves of that: with the knob off the stream
is what it always was, and with it on the fragments land before the
finished `tool_call` frame and concatenate back to its arguments. Fully
keyless — FakeClient scripts the call, and the fake slices the arguments
the way a wire does.
"""

import json

import pytest

from neosian import (
    Agent,
    AgentConfig,
    AgentEvent,
    Model,
    Tool,
    ToolCallDeltaEvent,
    ToolCallEvent,
    ToolResult,
    Usage,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import ToolCallId, ToolName

_USER = [Message(role=Role.USER, content="Hi")]
_USAGE = Usage(input_tokens=10, output_tokens=1)
_ARGUMENTS = {"city": "Istanbul", "unit": "celsius"}


@Tool(name="weather", description="Look up the weather")
async def weather(city: str, unit: str) -> ToolResult[str]:
    return ToolResult.ok(f"{city}: mild ({unit})")


def _script() -> FakeScript:
    """One tool round, then a plain answer; arguments sliced in eights."""
    call = ToolCall(
        id=ToolCallId("call_1"), name=ToolName("weather"), arguments=_ARGUMENTS
    )
    return FakeScript(
        turns=(
            FakeTurn(tool_calls=(call,), usage=_USAGE),
            FakeTurn(content="mild", usage=_USAGE),
        ),
        chunk_chars=8,
    )


def _agent(client: FakeClient, *, stream_tool_arguments: bool) -> Agent:
    return Agent(
        AgentConfig(
            system_prompt="You are a test agent.",
            model=Model.FAKE,
            tools=[weather],
            enable_todo=False,
            client_factory=lambda _m: client,
            stream_tool_arguments=stream_tool_arguments,
        )
    )


async def _events(agent: Agent) -> list[AgentEvent]:
    return [event async for event in await agent.run(_USER, stream=True)]


@pytest.mark.unit
class TestTheKnobIsTheDefault:
    async def test_off_by_default(self) -> None:
        assert AgentConfig(system_prompt="x").stream_tool_arguments is False

    async def test_off_yields_no_delta_frames(self) -> None:
        """The regression the default exists for: a host that never asked
        sees the stream it always saw."""
        events = await _events(
            _agent(FakeClient(_script()), stream_tool_arguments=False)
        )
        assert not any(isinstance(e, ToolCallDeltaEvent) for e in events)

    async def test_the_knob_changes_nothing_else(self) -> None:
        """Every other frame is identical with the knob on and off."""
        off = await _events(_agent(FakeClient(_script()), stream_tool_arguments=False))
        on = await _events(_agent(FakeClient(_script()), stream_tool_arguments=True))
        kept = [e for e in on if not isinstance(e, ToolCallDeltaEvent)]
        assert [type(e) for e in kept] == [type(e) for e in off]
        assert [e.to_dict()["event"] for e in kept] == [
            e.to_dict()["event"] for e in off
        ]


@pytest.mark.unit
class TestTheFragmentsCarryTheCall:
    async def test_fragments_precede_the_finished_call(self) -> None:
        events = await _events(
            _agent(FakeClient(_script()), stream_tool_arguments=True)
        )
        deltas = [i for i, e in enumerate(events) if isinstance(e, ToolCallDeltaEvent)]
        call = next(i for i, e in enumerate(events) if isinstance(e, ToolCallEvent))
        assert deltas, "the knob was on; fragments were expected"
        assert max(deltas) < call

    async def test_the_fragments_concatenate_to_the_arguments(self) -> None:
        """A fragment is a slice of JSON text, never JSON: only the whole
        of them parses, and it parses to what the tool_call frame carries."""
        events = await _events(
            _agent(FakeClient(_script()), stream_tool_arguments=True)
        )
        deltas = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
        joined = "".join(e.fragment for e in deltas)
        assert json.loads(joined) == _ARGUMENTS
        call = next(e for e in events if isinstance(e, ToolCallEvent))
        assert dict(call.arguments) == _ARGUMENTS

    async def test_every_fragment_names_its_call(self) -> None:
        events = await _events(
            _agent(FakeClient(_script()), stream_tool_arguments=True)
        )
        deltas = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
        assert {e.tool_call_id for e in deltas} == {"call_1"}
        assert {e.name for e in deltas} == {"weather"}

    async def test_the_sequence_numbers_are_one_run(self) -> None:
        """Fragments are stamped by the run's one sequencer, like any frame."""
        events = await _events(
            _agent(FakeClient(_script()), stream_tool_arguments=True)
        )
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
