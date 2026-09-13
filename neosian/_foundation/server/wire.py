"""The state process's wire codec (DESIGN §18) — one truth for both ends.

The wire mirrors the two storage ABCs: every payload is a method's
parameters by name, every response the return value under one key, and
the five frozen value types cross as flat JSON objects. Messages ride the
public codec (`message_to_json`/`message_from_json` — CS5's verbatim
round-trip). Timestamps are ISO-8601 `Z` strings, normalised to UTC both
ways; naive is refused on both ends (C4/CS4). `extra` is verbatim, JSON-safe (C6).

Errors cross as one envelope — `{"code", "message", "details"}` — and
decode back to the exact typed exception, so a `RemoteStore` caller
catches what a `FileStore` caller catches. Programmer errors (the ABCs'
bare `ValueError`) ride the sentinel code ``value_error``; an unknown
code decodes to the base `NeosianError`, never a silent swallow.

The wire is the process's API, versioned with the library — not an
ECOSYSTEM seam (ledger #108). `WIRE_VERSION` guards skew:
`RemoteStore.connect` refuses a server that speaks another version.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, cast

from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.codec import message_from_json, message_to_json
from neosian._foundation.memory.types import (
    MemoryDocument,
    MemoryEntry,
    MemoryRedaction,
    MemoryVersion,
)
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    ConversationConflictError,
    ConversationFormatUnsupportedError,
    ConversationIdInvalidError,
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryFormatUnsupportedError,
    MemoryPathInvalidError,
    MemoryScopeInvalidError,
    NeosianError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from neosian._foundation.memory.types import MemoryAction

WIRE_VERSION: Final = 4  # NC4: the four store/* routes; NQ2: every listing pages

# The envelope code for the ABCs' bare ValueError (programmer errors:
# negative cursors, empty message lists). Deliberately not a neosian
# error code — the §5 registry is a host contract, closed to plumbing.
VALUE_ERROR_CODE: Final = "value_error"

# The envelope code for a token reaching outside its allowance (§18.4,
# IN-4). Deliberately not a neosian error code either, and for the same
# reason: authorization is the daemon's plumbing, not a store's error.
FORBIDDEN_CODE: Final = "forbidden"


def encode_timestamp(value: datetime) -> str:
    """ISO-8601 `Z`, whatever zone the clock spoke (ECOSYSTEM §9)."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime cannot cross the wire (ECOSYSTEM §9)")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def decode_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"malformed wire timestamp: {value!r}")
    decoded = datetime.fromisoformat(value)
    if decoded.tzinfo is None or decoded.utcoffset() is None:
        raise ValueError(f"naive wire timestamp refused: {value!r}")
    return decoded.astimezone(UTC)


# Request parameters --------------------------------------------------------
#
# Every route reads its parameters through these: a mis-typed value is a
# `ValueError` — the sentinel envelope at 400 — never a JSON number written
# verbatim as document content, never a bare 500. `bool` is refused where
# an int is expected: one to Python, never on the wire.


def _param(
    payload: Mapping[str, Any], name: str, kind: type, label: str, *, required: bool
) -> Any:
    value = payload.get(name)
    if value is None:
        if required:
            raise ValueError(f"missing parameter: {name!r}")
        return None
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ValueError(f"parameter {name!r} must be {label}")
    return value


def require_str(payload: Mapping[str, Any], name: str) -> str:
    return cast(str, _param(payload, name, str, "a string", required=True))


def optional_str(payload: Mapping[str, Any], name: str) -> str | None:
    return cast("str | None", _param(payload, name, str, "a string", required=False))


def require_int(payload: Mapping[str, Any], name: str) -> int:
    return cast(int, _param(payload, name, int, "an integer", required=True))


def optional_int(payload: Mapping[str, Any], name: str) -> int | None:
    return cast("int | None", _param(payload, name, int, "an integer", required=False))


def optional_timestamp(payload: Mapping[str, Any], name: str) -> datetime | None:
    raw = optional_str(payload, name)
    return None if raw is None else decode_timestamp(raw)


def require_objects(payload: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    items = _param(payload, name, list, "a list of objects", required=True)
    if not all(isinstance(item, dict) for item in items):
        raise ValueError(f"parameter {name!r} must be a list of objects")
    return cast(list[dict[str, Any]], items)


# Value types ---------------------------------------------------------------


def encode_document(document: MemoryDocument) -> dict[str, Any]:
    return {
        "scope": document.scope,
        "path": document.path,
        "content": document.content,
        "version": document.version,
        "created_at": encode_timestamp(document.created_at),
        "updated_at": encode_timestamp(document.updated_at),
        "actor": document.actor,
        "redacted": document.redacted,
        "extra": dict(document.extra),
    }


def decode_document(data: Mapping[str, Any]) -> MemoryDocument:
    return MemoryDocument(
        scope=data["scope"],
        path=data["path"],
        content=data["content"],
        version=data["version"],
        created_at=decode_timestamp(data["created_at"]),
        updated_at=decode_timestamp(data["updated_at"]),
        actor=data["actor"],
        redacted=bool(data["redacted"]),
        extra=data.get("extra") or {},
    )


def encode_entry(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "path": entry.path,
        "version": entry.version,
        "created_at": encode_timestamp(entry.created_at),
        "updated_at": encode_timestamp(entry.updated_at),
        "redacted": entry.redacted,
    }


def decode_entry(data: Mapping[str, Any]) -> MemoryEntry:
    return MemoryEntry(
        path=data["path"],
        version=data["version"],
        created_at=decode_timestamp(data["created_at"]),
        updated_at=decode_timestamp(data["updated_at"]),
        redacted=bool(data["redacted"]),
    )


def encode_version(row: MemoryVersion) -> dict[str, Any]:
    return {
        "path": row.path,
        "version": row.version,
        "action": row.action,
        "content": row.content,
        "actor": row.actor,
        "created_at": encode_timestamp(row.created_at),
        "redacted": row.redacted,
    }


def decode_version(data: Mapping[str, Any]) -> MemoryVersion:
    return MemoryVersion(
        path=data["path"],
        version=data["version"],
        action=cast("MemoryAction", data["action"]),
        content=data["content"],
        actor=data["actor"],
        created_at=decode_timestamp(data["created_at"]),
        redacted=bool(data["redacted"]),
    )


def encode_redaction(act: MemoryRedaction) -> dict[str, Any]:
    return {
        "path": act.path,
        "actor": act.actor,
        "created_at": encode_timestamp(act.created_at),
        "count": act.count,
    }


def decode_redaction(data: Mapping[str, Any]) -> MemoryRedaction:
    return MemoryRedaction(
        path=data["path"],
        actor=data["actor"],
        created_at=decode_timestamp(data["created_at"]),
        count=int(data["count"]),
    )


def encode_turn(turn: ConversationTurn) -> dict[str, Any]:
    return {
        "conversation_id": turn.conversation_id,
        "turn": turn.turn,
        "messages": [message_to_json(message) for message in turn.messages],
        "created_at": encode_timestamp(turn.created_at),
        "actor": turn.actor,
    }


def decode_turn(data: Mapping[str, Any]) -> ConversationTurn:
    return ConversationTurn(
        conversation_id=data["conversation_id"],
        turn=data["turn"],
        messages=tuple(message_from_json(encoded) for encoded in data["messages"]),
        created_at=decode_timestamp(data["created_at"]),
        actor=data.get("actor"),
    )


def encode_projection(entry: ConversationProjection) -> dict[str, Any]:
    return {
        "turn": entry.turn,
        "kind": entry.kind,
        "text": entry.text,
        "span": entry.span,
    }


def decode_projection(data: Mapping[str, Any]) -> ConversationProjection:
    # __post_init__ validates turn/span/kind — invalid wire values raise
    # the same ValueError the in-process constructor raises.
    return ConversationProjection(
        turn=data["turn"],
        kind=data["kind"],
        text=data["text"],
        span=data.get("span", 1),
    )


# The error envelope --------------------------------------------------------


def encode_error(exc: NeosianError | ValueError) -> dict[str, Any]:
    """One envelope for everything a store method may raise."""
    if isinstance(exc, NeosianError):
        return {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details or {},
        }
    return {"code": VALUE_ERROR_CODE, "message": str(exc), "details": {}}


def _memory_conflict(details: Mapping[str, Any]) -> NeosianError:
    return MemoryConflictError(
        details["scope"],
        details["path"],
        details["reason"],
        expected_version=details.get("expected_version"),
        actual_version=details.get("actual_version"),
    )


_DECODERS: Final[dict[str, Callable[[Mapping[str, Any]], NeosianError]]] = {
    MemoryDocumentNotFoundError.code: lambda d: MemoryDocumentNotFoundError(
        d["scope"], d["path"]
    ),
    MemoryScopeInvalidError.code: lambda d: MemoryScopeInvalidError(
        d["scope"], d["reason"]
    ),
    MemoryPathInvalidError.code: lambda d: MemoryPathInvalidError(
        d["path"], d["reason"]
    ),
    MemoryConflictError.code: _memory_conflict,
    MemoryFormatUnsupportedError.code: lambda d: MemoryFormatUnsupportedError(
        d["scope"], d["path"], d["reason"]
    ),
    ConversationIdInvalidError.code: lambda d: ConversationIdInvalidError(
        d["conversation_id"], d["reason"]
    ),
    ConversationFormatUnsupportedError.code: lambda d: (
        ConversationFormatUnsupportedError(d["conversation_id"], d["reason"])
    ),
    ConversationConflictError.code: lambda d: ConversationConflictError(
        d["conversation_id"], d["reason"]
    ),
}


def decode_error(envelope: Mapping[str, Any]) -> NeosianError | ValueError:
    """The envelope back to the exact typed exception.

    An unknown code — or a typed one whose details are malformed — decodes
    to the base `NeosianError` carrying the message and details verbatim:
    degraded fidelity, never a swallow.
    """
    code = envelope.get("code")
    message = str(envelope.get("message", ""))
    details_raw = envelope.get("details")
    details: Mapping[str, Any] = details_raw if isinstance(details_raw, dict) else {}
    if code == VALUE_ERROR_CODE:
        return ValueError(message)
    if code == ConfigurationError.code:
        # The daemon's own refusals (a backend without the NC4 protocol):
        # message-shaped, no typed fields to rebuild.
        return ConfigurationError(message, details=dict(details) or None)
    decoder = _DECODERS.get(code) if isinstance(code, str) else None
    if decoder is not None:
        try:
            return decoder(details)
        except (KeyError, TypeError):
            pass
    return NeosianError(message, details=dict(details))
