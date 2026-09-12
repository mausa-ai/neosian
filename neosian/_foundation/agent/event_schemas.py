"""The wire-payload TypedDicts and schema export for agent events (§6).

Split from ``events.py`` (NP) purely for the size gate — one contract,
two modules: the dataclasses' ``to_dict()`` returns these shapes and
``event_schemas()`` exports them, so payload and schema cannot drift
(a unit test validates one against the other). The import edge is
one-way (this module reads ``events``; ``events`` never reads back) —
pydantic touches this module only at schema-export time.
"""

from __future__ import annotations

from typing import Any, Final, Literal, TypedDict

from neosian._foundation.agent.events import (
    AgentEventType,
    _ModelUsagePayload,
    _UsagePayload,
)


class _ReadyPayload(TypedDict):
    event: Literal["ready"]
    sequence: int
    protocol: int
    requested_model: str
    provider: str


class _ContentPayload(TypedDict):
    event: Literal["content"]
    sequence: int
    content: str


class _ReasoningPayload(TypedDict):
    event: Literal["reasoning"]
    sequence: int
    reasoning: str


class _ToolCallPayload(TypedDict):
    event: Literal["tool_call"]
    sequence: int
    id: str
    name: str
    arguments: dict[str, Any]


class _ToolCallDeltaPayload(TypedDict):
    event: Literal["tool_call_delta"]
    sequence: int
    tool_call_id: str
    name: str
    fragment: str


class _ToolResultPayload(TypedDict):
    event: Literal["tool_result"]
    sequence: int
    tool_call_id: str
    success: bool
    data: Any
    error: str | None


class _ToolProgressPayload(TypedDict):
    event: Literal["tool_progress"]
    sequence: int
    tool_call_id: str
    elapsed_ms: int


class _MemoryWritePayload(TypedDict):
    event: Literal["memory_write"]
    sequence: int
    tool_call_id: str
    command: str
    path: str
    version: int
    previous_path: str | None


class _BlockedPayload(TypedDict):
    event: Literal["blocked"]
    sequence: int
    rationale: str | None
    usage: _UsagePayload | None
    usage_by_model: list[_ModelUsagePayload]


class _DonePayload(TypedDict):
    event: Literal["done"]
    sequence: int
    model: str | None
    stop_reason: str | None
    raw_stop_reason: str | None
    usage: _UsagePayload | None
    usage_by_model: list[_ModelUsagePayload]
    iterations_exhausted: bool


class _ErrorPayload(TypedDict):
    event: Literal["error"]
    sequence: int
    code: str
    retryable: bool
    usage: _UsagePayload | None
    usage_by_model: list[_ModelUsagePayload]


_PAYLOAD_TYPES: Final[dict[str, Any]] = {
    AgentEventType.READY.value: _ReadyPayload,
    AgentEventType.CONTENT.value: _ContentPayload,
    AgentEventType.REASONING.value: _ReasoningPayload,
    AgentEventType.TOOL_CALL.value: _ToolCallPayload,
    AgentEventType.TOOL_CALL_DELTA.value: _ToolCallDeltaPayload,
    AgentEventType.TOOL_RESULT.value: _ToolResultPayload,
    AgentEventType.TOOL_PROGRESS.value: _ToolProgressPayload,
    AgentEventType.MEMORY_WRITE.value: _MemoryWritePayload,
    AgentEventType.BLOCKED.value: _BlockedPayload,
    AgentEventType.DONE.value: _DonePayload,
    AgentEventType.ERROR.value: _ErrorPayload,
}

AGENT_EVENT_SCHEMA_KEY: Final = "agent_event"


def event_schemas() -> dict[str, Any]:
    """JSON Schemas of the wire payloads — hosts codegen their SSE seam.

    One schema per event name, plus an ``agent_event`` root: a ``oneOf``
    over all eleven, discriminated on the ``event`` key. pydantic runs here
    only — zero hot-path cost.
    """
    from pydantic import TypeAdapter  # schema-export time only

    schemas: dict[str, Any] = {
        name: TypeAdapter(payload).json_schema()
        for name, payload in _PAYLOAD_TYPES.items()
    }
    schemas[AGENT_EVENT_SCHEMA_KEY] = {
        "title": "AgentEvent",
        "oneOf": [schemas[name] for name in _PAYLOAD_TYPES],
    }
    return schemas
