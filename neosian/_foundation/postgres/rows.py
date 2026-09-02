"""Row → value-type mappers, format guards, UTC normalization.

Every timestamp column is timestamptz, so psycopg always hands back an
aware datetime; mappers normalize to UTC so the session timezone is
irrelevant (C4/CS4). Format discipline (C6/CS6): a row declaring a
`neosian_format` outside [1, current] is refused, never coerced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from neosian._foundation.conversation.types import (
    CONVERSATION_FORMAT_VERSION,
    ConversationProjection,
    ConversationTurn,
    ProjectionKind,
)
from neosian._foundation.llm.codec import message_from_json
from neosian._foundation.memory.types import (
    MEMORY_FORMAT_VERSION,
    MemoryAction,
    MemoryDocument,
    MemoryEntry,
    MemoryRedaction,
    MemoryVersion,
)
from neosian._foundation.shared.exceptions import (
    ConversationFormatUnsupportedError,
    MemoryFormatUnsupportedError,
)

if TYPE_CHECKING:
    from neosian._foundation.postgres.pool import Row
    from neosian._foundation.shared.clock import Clock


def aware_now(clock: Clock) -> datetime:
    now = clock.now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Clock returned a naive datetime (ECOSYSTEM §9)")
    return now


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _check_memory_format(scope: str, path: str, declared: int) -> None:
    if not 1 <= declared <= MEMORY_FORMAT_VERSION:
        raise MemoryFormatUnsupportedError(
            scope, path, f"neosian_format {declared} is not supported"
        )


def memory_document(scope: str, path: str, row: Row) -> MemoryDocument:
    content, version, created_at, updated_at, actor, redacted, declared, extra = row
    _check_memory_format(scope, path, declared)
    if not isinstance(extra, dict):
        raise MemoryFormatUnsupportedError(scope, path, "extra is not an object")
    return MemoryDocument(
        scope=scope,
        path=path,
        content=content,
        version=version,
        created_at=_utc(created_at),
        updated_at=_utc(updated_at),
        actor=actor,
        redacted=redacted,
        extra=MappingProxyType(dict(cast("dict[str, Any]", extra))),
    )


def memory_entry(scope: str, row: Row) -> MemoryEntry:
    path, version, created_at, updated_at, redacted, declared = row
    _check_memory_format(scope, path, declared)
    return MemoryEntry(
        path=path,
        version=version,
        created_at=_utc(created_at),
        updated_at=_utc(updated_at),
        redacted=redacted,
    )


def memory_version(scope: str, row: Row) -> MemoryVersion:
    path, version, action, content, actor, created_at, redacted, declared = row
    _check_memory_format(scope, path, declared)
    return MemoryVersion(
        path=path,
        version=version,
        action=cast(MemoryAction, action),
        content=content,
        actor=actor,
        created_at=_utc(created_at),
        redacted=redacted,
    )


def memory_redaction(row: Row) -> MemoryRedaction:
    path, actor, created_at, count = row
    return MemoryRedaction(
        path=path, actor=actor, created_at=_utc(created_at), count=int(count)
    )


def _check_turn_format(conversation_id: str, where: str, declared: int) -> None:
    if not 1 <= declared <= CONVERSATION_FORMAT_VERSION:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} declares unsupported format {declared}"
        )


def conversation_turn(conversation_id: str, row: Row) -> ConversationTurn:
    turn, encoded, created_at, declared, actor = row
    where = f"turn row {turn}"
    _check_turn_format(conversation_id, where, declared)
    if not isinstance(encoded, list) or not encoded:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} has no message array"
        )
    try:
        messages = tuple(message_from_json(item) for item in encoded)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} has an undecodable message"
        ) from exc
    return ConversationTurn(
        conversation_id=conversation_id,
        turn=turn,
        messages=messages,
        created_at=_utc(created_at),
        actor=actor,
    )


def conversation_projection(conversation_id: str, row: Row) -> ConversationProjection:
    turn, kind, text, span, declared = row
    where = f"projection row (turn {turn})"
    _check_turn_format(conversation_id, where, declared)
    try:
        return ConversationProjection(
            turn=turn, kind=cast(ProjectionKind, kind), text=text, span=span
        )
    except ValueError as exc:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} has invalid fields"
        ) from exc
