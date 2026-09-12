"""Typed streaming events — the v2 wire contract (DESIGN §6, ECOSYSTEM §5).

Frozen, slotted dataclasses — one class per event, ``match``-able,
behavior-carrying (``to_dict()``, ``to_sse()``); the dataclasses are the
constructors. ``run(stream=True)`` yields these and still raises on failure;
a relaying host converts in its own generator (``ErrorEvent.from_exception``)
or relays verbatim with ``sse_stream()``. The error frame carries a machine
code and no message text — it cannot leak internals by construction.

The ``_*Payload`` TypedDicts in ``event_schemas.py`` are the wire shapes:
``to_dict()`` returns them and ``event_schemas()`` exports them, so payload
and schema cannot drift (a unit test validates one against the other) —
split modules, one contract, one-way import edge (NP, size gate).
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Final, TypedDict

from neosian._foundation.llm.base import ModelUsage, Usage
from neosian._foundation.shared.exceptions import NeosianError
from neosian._foundation.shared.serialization import safe_json_dumps

EVENT_PROTOCOL_VERSION: Final = 2


class AgentEventType(str, Enum):
    """Streaming event vocabulary (frozen, ECOSYSTEM §5)."""

    READY = "ready"
    CONTENT = "content"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_RESULT = "tool_result"
    TOOL_PROGRESS = "tool_progress"
    MEMORY_WRITE = "memory_write"
    BLOCKED = "blocked"
    DONE = "done"
    ERROR = "error"


class _UsagePayload(TypedDict):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int


class _ModelUsagePayload(TypedDict):
    model: str
    usage: _UsagePayload


def _usage_payload(usage: Usage) -> _UsagePayload:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "total_tokens": usage.total_tokens,
    }


def _usage_by_model_payload(
    entries: tuple[ModelUsage, ...],
) -> list[_ModelUsagePayload]:
    return [{"model": e.model, "usage": _usage_payload(e.usage)} for e in entries]


class _EventBehavior:
    """Shared SSE framing; each event implements ``to_dict()``."""

    __slots__ = ()

    type: ClassVar[AgentEventType]

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_sse(self) -> str:
        """Wire form: ``event: <name>`` + one compact single-line JSON object.

        ``ensure_ascii`` keeps newlines escaped — no model output can break
        framing. No ``id:`` field — resume is host territory.
        """
        data = safe_json_dumps(self.to_dict(), "agent_event.data", compact=True)
        return f"event: {self.type.value}\ndata: {data}\n\n"


@dataclass(frozen=True, slots=True)
class ReadyEvent(_EventBehavior):
    """The stream is accepted and a model chosen; always the first event."""

    type: ClassVar[AgentEventType] = AgentEventType.READY

    requested_model: str
    provider: str
    protocol: int = EVENT_PROTOCOL_VERSION
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "protocol": self.protocol,
            "requested_model": self.requested_model,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class ContentEvent(_EventBehavior):
    """A delta of assistant output text."""

    type: ClassVar[AgentEventType] = AgentEventType.CONTENT

    content: str
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class ReasoningEvent(_EventBehavior):
    """A delta of model thinking/reasoning text."""

    type: ClassVar[AgentEventType] = AgentEventType.REASONING

    reasoning: str
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "reasoning": self.reasoning,
        }


@dataclass(frozen=True, slots=True)
class ToolCallEvent(_EventBehavior):
    """The model requested a tool call."""

    type: ClassVar[AgentEventType] = AgentEventType.TOOL_CALL

    id: str
    name: str
    arguments: Mapping[str, Any]
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "id": self.id,
            "name": self.name,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class ToolCallDeltaEvent(_EventBehavior):
    """One piece of a tool call's arguments, while the model is writing it.

    Opt-in (`AgentConfig.stream_tool_arguments`): it is the only frame a
    turn can emit many of per call, so a host asks for it rather than
    inheriting it (#226). `fragment` is a slice of JSON text and is never
    valid JSON on its own — a consumer concatenates the fragments of one
    `tool_call_id`, and the finished, parsed arguments still arrive on the
    `tool_call` frame that follows.
    """

    type: ClassVar[AgentEventType] = AgentEventType.TOOL_CALL_DELTA

    tool_call_id: str
    name: str
    fragment: str
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "fragment": self.fragment,
        }


@dataclass(frozen=True, slots=True)
class ToolResultEvent(_EventBehavior):
    """One tool call finished; exactly one of data/error is set."""

    type: ClassVar[AgentEventType] = AgentEventType.TOOL_RESULT

    tool_call_id: str
    success: bool
    data: Any = None
    error: str | None = None
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "tool_call_id": self.tool_call_id,
            "success": self.success,
            "data": self.data,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ToolProgressEvent(_EventBehavior):
    """This tool is still running — which no keepalive means.

    Keepalive is the host's SSE comment on the host's timer (only the host
    knows its proxy's idle timeout); neosian emits none.
    """

    type: ClassVar[AgentEventType] = AgentEventType.TOOL_PROGRESS

    tool_call_id: str
    elapsed_ms: int
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "tool_call_id": self.tool_call_id,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True, slots=True)
class MemoryWriteEvent(_EventBehavior):
    """A memory mutation landed (NP): the receipt frame, never the content.

    One frame per successful mutating memory command (``create``,
    ``str_replace``, ``insert``, ``delete``, ``rename``), emitted
    immediately after that call's ``tool_result``. ``version`` is the
    version row the command appended (for ``delete``, the row the deletion
    consumed) — the argument an undo passes to ``revert_memory``.
    ``view``, failed calls and off-stream writes (reflection, maintenance,
    redaction) emit nothing.
    """

    type: ClassVar[AgentEventType] = AgentEventType.MEMORY_WRITE

    tool_call_id: str
    command: str
    path: str
    version: int
    previous_path: str | None = None
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "tool_call_id": self.tool_call_id,
            "command": self.command,
            "path": self.path,
            "version": self.version,
            "previous_path": self.previous_path,
        }


@dataclass(frozen=True, slots=True)
class BlockedEvent(_EventBehavior):
    """Terminal: a guardrail blocked the turn; carries billed usage."""

    type: ClassVar[AgentEventType] = AgentEventType.BLOCKED

    rationale: str | None = None
    usage: Usage | None = None
    usage_by_model: tuple[ModelUsage, ...] = ()
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "rationale": self.rationale,
            "usage": _usage_payload(self.usage) if self.usage else None,
            "usage_by_model": _usage_by_model_payload(self.usage_by_model),
        }


@dataclass(frozen=True, slots=True)
class DoneEvent(_EventBehavior):
    """Terminal: the run completed.

    ``model`` is the API-reported string of the final completion — unified
    with ``AgentResponse.model``. ``stop_reason`` is the normalized view
    (``StopReason`` value), ``raw_stop_reason`` the provider-native string.
    ``iterations_exhausted`` says the tool loop ran out and this is the
    toolless final call's answer (additive, NF #170).
    """

    type: ClassVar[AgentEventType] = AgentEventType.DONE

    model: str | None = None
    stop_reason: str | None = None
    raw_stop_reason: str | None = None
    usage: Usage | None = None
    usage_by_model: tuple[ModelUsage, ...] = ()
    iterations_exhausted: bool = False
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "raw_stop_reason": self.raw_stop_reason,
            "usage": _usage_payload(self.usage) if self.usage else None,
            "usage_by_model": _usage_by_model_payload(self.usage_by_model),
            "iterations_exhausted": self.iterations_exhausted,
        }


@dataclass(frozen=True, slots=True)
class ErrorEvent(_EventBehavior):
    """Terminal: a machine code and billed usage — no message field.

    Emitted by relaying hosts (``from_exception``), never by the library:
    ``run(stream=True)`` raises, full text goes to the host's logs, only
    the code goes on the wire.
    """

    type: ClassVar[AgentEventType] = AgentEventType.ERROR

    code: str
    retryable: bool = False
    usage: Usage | None = None
    usage_by_model: tuple[ModelUsage, ...] = ()
    sequence: int = 0

    @classmethod
    def from_exception(cls, exc: NeosianError, *, sequence: int) -> ErrorEvent:
        """Build the wire frame from any NeosianError's public attributes."""
        return cls(
            code=exc.code,
            retryable=exc.retryable,
            usage=getattr(exc, "usage", None),
            usage_by_model=getattr(exc, "usage_by_model", ()),
            sequence=sequence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.type.value,
            "sequence": self.sequence,
            "code": self.code,
            "retryable": self.retryable,
            "usage": _usage_payload(self.usage) if self.usage else None,
            "usage_by_model": _usage_by_model_payload(self.usage_by_model),
        }


AgentEvent = (
    ReadyEvent
    | ContentEvent
    | ReasoningEvent
    | ToolCallEvent
    | ToolCallDeltaEvent
    | ToolResultEvent
    | ToolProgressEvent
    | MemoryWriteEvent
    | BlockedEvent
    | DoneEvent
    | ErrorEvent
)


class EventSequencer:
    """Stamps monotonically increasing sequence numbers onto events.

    Starts at 1 — the frontend owns 0 (the user message). One sequencer per
    run; ``stamp`` returns a new frozen event, never mutates.
    """

    __slots__ = ("_next",)

    def __init__(self) -> None:
        self._next = 1

    def stamp(self, event: AgentEvent) -> AgentEvent:
        """Return a copy of ``event`` carrying the next sequence number."""
        stamped = dataclasses.replace(event, sequence=self._next)
        self._next += 1
        return stamped


async def sse_stream(events: AsyncIterator[AgentEvent]) -> AsyncIterator[str]:
    """Verbatim-relay adapter: typed events → SSE wire strings."""
    async for event in events:
        yield event.to_sse()
