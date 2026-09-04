"""Unit tests for the v2 typed streaming events (DESIGN §6, ECOSYSTEM §5)."""

import dataclasses
import json
from collections.abc import AsyncIterator

import pytest
from pydantic import TypeAdapter

from neosian._foundation.agent.event_schemas import (
    _PAYLOAD_TYPES,
    AGENT_EVENT_SCHEMA_KEY,
    event_schemas,
)
from neosian._foundation.agent.events import (
    EVENT_PROTOCOL_VERSION,
    AgentEvent,
    AgentEventType,
    BlockedEvent,
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    EventSequencer,
    MemoryWriteEvent,
    ReadyEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    sse_stream,
)
from neosian._foundation.llm.base import ModelUsage, Usage
from neosian._foundation.shared.exceptions import LLMError, NeosianError

USAGE = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=2)
USAGE_PAYLOAD = {
    "input_tokens": 10,
    "output_tokens": 5,
    "cache_read_tokens": 2,
    "cache_write_tokens": 0,
    "total_tokens": 17,
}
BY_MODEL = (ModelUsage(model="fake-1", usage=USAGE),)
BY_MODEL_PAYLOAD = [{"model": "fake-1", "usage": USAGE_PAYLOAD}]


def _one_of_each() -> list[AgentEvent]:
    """A representative instance of every event type."""
    return [
        ReadyEvent(requested_model="fake", provider="fake"),
        ContentEvent(content="Hello"),
        ReasoningEvent(reasoning="thinking..."),
        ToolCallEvent(id="call_1", name="lookup", arguments={"q": "x"}),
        ToolResultEvent(tool_call_id="call_1", success=True, data={"n": 1}),
        ToolProgressEvent(tool_call_id="call_1", elapsed_ms=15000),
        MemoryWriteEvent(
            tool_call_id="call_1", command="create", path="/memories/x", version=1
        ),
        BlockedEvent(rationale="policy", usage=USAGE, usage_by_model=BY_MODEL),
        DoneEvent(
            model="fake-1",
            stop_reason="stop",
            raw_stop_reason="end_turn",
            usage=USAGE,
            usage_by_model=BY_MODEL,
        ),
        ErrorEvent(code="llm_error", retryable=True, usage=USAGE),
    ]


class TestProtocol:
    def test_protocol_version_is_2(self) -> None:
        assert EVENT_PROTOCOL_VERSION == 2

    def test_ready_carries_protocol_by_default(self) -> None:
        event = ReadyEvent(requested_model="fake", provider="fake")
        assert event.protocol == EVENT_PROTOCOL_VERSION

    def test_vocabulary_is_frozen(self) -> None:
        assert {t.value for t in AgentEventType} == {
            "ready",
            "content",
            "reasoning",
            "tool_call",
            "tool_result",
            "tool_progress",
            "memory_write",
            "blocked",
            "done",
            "error",
        }

    def test_events_are_frozen(self) -> None:
        event = ContentEvent(content="x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.content = "y"  # type: ignore[misc]


class TestPayloads:
    def test_ready(self) -> None:
        event = ReadyEvent(requested_model="fake", provider="fake", sequence=1)
        assert event.to_dict() == {
            "event": "ready",
            "sequence": 1,
            "protocol": 2,
            "requested_model": "fake",
            "provider": "fake",
        }

    def test_content(self) -> None:
        assert ContentEvent(content="Hi", sequence=2).to_dict() == {
            "event": "content",
            "sequence": 2,
            "content": "Hi",
        }

    def test_reasoning(self) -> None:
        assert ReasoningEvent(reasoning="hmm", sequence=3).to_dict() == {
            "event": "reasoning",
            "sequence": 3,
            "reasoning": "hmm",
        }

    def test_tool_call(self) -> None:
        event = ToolCallEvent(id="c1", name="lookup", arguments={"q": "x"})
        assert event.to_dict() == {
            "event": "tool_call",
            "sequence": 0,
            "id": "c1",
            "name": "lookup",
            "arguments": {"q": "x"},
        }

    def test_tool_result(self) -> None:
        event = ToolResultEvent(tool_call_id="c1", success=False, error="boom")
        assert event.to_dict() == {
            "event": "tool_result",
            "sequence": 0,
            "tool_call_id": "c1",
            "success": False,
            "data": None,
            "error": "boom",
        }

    def test_tool_progress_elapsed_is_int_ms(self) -> None:
        event = ToolProgressEvent(tool_call_id="c1", elapsed_ms=15000)
        payload = event.to_dict()
        assert payload == {
            "event": "tool_progress",
            "sequence": 0,
            "tool_call_id": "c1",
            "elapsed_ms": 15000,
        }
        assert isinstance(payload["elapsed_ms"], int)

    def test_memory_write_never_carries_content(self) -> None:
        event = MemoryWriteEvent(
            tool_call_id="c1",
            command="rename",
            path="/memories/preferences",
            version=4,
            previous_path="/memories/prefs",
        )
        payload = event.to_dict()
        assert payload == {
            "event": "memory_write",
            "sequence": 0,
            "tool_call_id": "c1",
            "command": "rename",
            "path": "/memories/preferences",
            "version": 4,
            "previous_path": "/memories/prefs",
        }
        # The frame says a write happened and names the version to undo —
        # never what was written (ECOSYSTEM §5, NP amendment).
        assert "content" not in payload

    def test_blocked(self) -> None:
        event = BlockedEvent(rationale="policy", usage=USAGE, usage_by_model=BY_MODEL)
        assert event.to_dict() == {
            "event": "blocked",
            "sequence": 0,
            "rationale": "policy",
            "usage": USAGE_PAYLOAD,
            "usage_by_model": BY_MODEL_PAYLOAD,
        }

    def test_done_terminal_carries_usage_and_split(self) -> None:
        event = DoneEvent(
            model="fake-1",
            stop_reason="stop",
            raw_stop_reason="end_turn",
            usage=USAGE,
            usage_by_model=BY_MODEL,
        )
        assert event.to_dict() == {
            "event": "done",
            "sequence": 0,
            "model": "fake-1",
            "stop_reason": "stop",
            "raw_stop_reason": "end_turn",
            "usage": USAGE_PAYLOAD,
            "usage_by_model": BY_MODEL_PAYLOAD,
            "iterations_exhausted": False,
        }

    def test_done_fields_nullable(self) -> None:
        assert DoneEvent().to_dict() == {
            "event": "done",
            "sequence": 0,
            "model": None,
            "stop_reason": None,
            "raw_stop_reason": None,
            "usage": None,
            "usage_by_model": [],
            "iterations_exhausted": False,
        }

    def test_error_has_no_message_field(self) -> None:
        event = ErrorEvent(code="llm_error", retryable=True)
        assert event.to_dict() == {
            "event": "error",
            "sequence": 0,
            "code": "llm_error",
            "retryable": True,
            "usage": None,
            "usage_by_model": [],
        }


class TestWireForm:
    def test_to_sse_golden_bytes(self) -> None:
        event = ContentEvent(content="Hi", sequence=1)
        assert event.to_sse() == (
            'event: content\ndata: {"event":"content","sequence":1,"content":"Hi"}\n\n'
        )

    def test_compact_single_line_json(self) -> None:
        event = DoneEvent(model="m", usage=USAGE, sequence=9)
        _, data_line, _ = event.to_sse().split("\n", 2)
        payload = data_line.removeprefix("data: ")
        assert ", " not in payload and ": " not in payload
        assert json.loads(payload) == event.to_dict()

    def test_model_output_cannot_break_framing(self) -> None:
        frame = ContentEvent(content="line1\nline2 é").to_sse()
        assert frame.count("\n\n") == 1  # only the terminator
        assert "\\n" in frame and frame.isascii()

    async def test_sse_stream_relays_verbatim(self) -> None:
        events: list[AgentEvent] = [
            ContentEvent(content="a", sequence=1),
            DoneEvent(sequence=2),
        ]

        async def _gen() -> AsyncIterator[AgentEvent]:
            for e in events:
                yield e

        frames = [frame async for frame in sse_stream(_gen())]
        assert frames == [e.to_sse() for e in events]


class TestEventSequencer:
    def test_starts_at_one_and_increments(self) -> None:
        seq = EventSequencer()
        first = seq.stamp(ContentEvent(content="a"))
        second = seq.stamp(DoneEvent())
        assert (first.sequence, second.sequence) == (1, 2)

    def test_stamp_returns_new_frozen_event(self) -> None:
        seq = EventSequencer()
        original = ContentEvent(content="a")
        stamped = seq.stamp(original)
        assert stamped is not original and original.sequence == 0

    def test_sequencers_are_independent(self) -> None:
        a, b = EventSequencer(), EventSequencer()
        a.stamp(ContentEvent(content="x"))
        assert b.stamp(ContentEvent(content="y")).sequence == 1


class TestErrorEventFromException:
    def test_reads_public_error_surface(self) -> None:
        exc = LLMError(
            "secret internals",
            retryable=True,
            usage=USAGE,
            usage_by_model=BY_MODEL,
        )
        event = ErrorEvent.from_exception(exc, sequence=7)
        assert event == ErrorEvent(
            code="llm_error",
            retryable=True,
            usage=USAGE,
            usage_by_model=BY_MODEL,
            sequence=7,
        )

    def test_message_text_never_reaches_the_wire(self) -> None:
        exc = LLMError("secret internals", usage=USAGE)
        frame = ErrorEvent.from_exception(exc, sequence=1).to_sse()
        assert "secret" not in frame

    def test_usage_less_exceptions_degrade_cleanly(self) -> None:
        event = ErrorEvent.from_exception(NeosianError("nope"), sequence=1)
        assert (event.code, event.usage, event.usage_by_model) == (
            "neosian_error",
            None,
            (),
        )


class TestEventSchemas:
    def test_one_schema_per_event_plus_root(self) -> None:
        schemas = event_schemas()
        assert set(schemas) == {t.value for t in AgentEventType} | {
            AGENT_EVENT_SCHEMA_KEY
        }
        root = schemas[AGENT_EVENT_SCHEMA_KEY]
        assert root["title"] == "AgentEvent" and len(root["oneOf"]) == 10

    def test_schemas_discriminate_on_event(self) -> None:
        for name, schema in event_schemas().items():
            if name == AGENT_EVENT_SCHEMA_KEY:
                continue
            assert schema["properties"]["event"]["const"] == name

    def test_every_payload_validates_against_its_schema(self) -> None:
        """to_dict() and event_schemas() cannot drift — same TypedDict source."""
        for event in _one_of_each():
            payload = event.to_dict()
            TypeAdapter(_PAYLOAD_TYPES[payload["event"]]).validate_python(
                payload, strict=True
            )
