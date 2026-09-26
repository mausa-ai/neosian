"""The file substrate's row codec and layout names (DESIGN §9.8).

One compact `ensure_ascii=True` JSON object per line — no message
content can break framing — with `neosian_format` on every row and
ISO-Z timestamps; a malformed or newer row raises, never skips. Shared
by the turn store (`file_turns.py`), the search scan (`file_search.py`)
and the mobility restore (`memory/file_portable.py`).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from neosian._foundation.conversation.types import (
    CONVERSATION_FORMAT_VERSION,
    ConversationProjection,
    ConversationTurn,
    ProjectionKind,
)
from neosian._foundation.llm.codec import message_from_json, message_to_json
from neosian._foundation.shared.exceptions import ConversationFormatUnsupportedError

CONVERSATIONS = "conversations"
TURNS = "turns.jsonl"
PROJECTIONS = "projections.jsonl"
_KINDS = ("log", "digest", "epoch")


def render_turn(record: ConversationTurn) -> str:
    data = {
        "neosian_format": CONVERSATION_FORMAT_VERSION,
        "turn": record.turn,
        "created_at": record.created_at.isoformat().replace("+00:00", "Z"),
        "messages": [message_to_json(message) for message in record.messages],
        "actor": record.actor,
    }
    return json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n"


def render_projection(entry: ConversationProjection) -> str:
    data = {
        "neosian_format": CONVERSATION_FORMAT_VERSION,
        "turn": entry.turn,
        "span": entry.span,
        "kind": entry.kind,
        "text": entry.text,
    }
    return json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n"


def _parse_object(line: str, where: str, conversation_id: str) -> dict[str, Any]:
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"malformed {where}"
        ) from exc
    if not isinstance(data, dict):
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} is not an object"
        )
    declared = data.get("neosian_format")
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 1:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} missing format"
        )
    if declared > CONVERSATION_FORMAT_VERSION:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} format {declared} is newer than supported"
        )
    return cast("dict[str, Any]", data)


def parse_turn(line: str, number: int, conversation_id: str) -> ConversationTurn:
    where = f"turn-log line {number}"
    data = _parse_object(line, where, conversation_id)
    turn = data.get("turn")
    created_raw = data.get("created_at")
    encoded = data.get("messages")
    actor = data.get("actor")  # absent on pre-NL rows: None
    if (
        not isinstance(turn, int)
        or isinstance(turn, bool)
        or turn < 1
        or not isinstance(created_raw, str)
        or not isinstance(encoded, list)
        or not encoded
        or not (actor is None or isinstance(actor, str))
    ):
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} has invalid fields"
        )
    try:
        created_at = datetime.fromisoformat(created_raw)
    except ValueError as exc:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} has an unreadable timestamp"
        ) from exc
    if created_at.tzinfo is None:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} has a naive timestamp"
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
        created_at=created_at,
        actor=actor,
    )


def parse_projection(
    line: str, number: int, conversation_id: str
) -> ConversationProjection:
    where = f"projection line {number}"
    data = _parse_object(line, where, conversation_id)
    turn = data.get("turn")
    span = data.get("span")
    kind = data.get("kind")
    text = data.get("text")
    if (
        not isinstance(turn, int)
        or isinstance(turn, bool)
        or turn < 1
        or not isinstance(span, int)
        or isinstance(span, bool)
        or not 1 <= span <= turn
        or kind not in _KINDS
        or not isinstance(text, str)
    ):
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} has invalid fields"
        )
    return ConversationProjection(
        turn=turn, kind=cast(ProjectionKind, kind), text=text, span=span
    )
