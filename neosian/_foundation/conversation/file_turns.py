"""FileStore turn persistence — the file-substrate half of DESIGN §9.8.

Mixed into `memory.file.FileStore`, which owns the root, clock and lock
(shared with the memory side, so a turn append and a memory write never
interleave). Layout, sibling of the percent-encoded scope directories —
every scope component carries a `%3A`, so `conversations/` never collides:

    root/conversations/<conversation_id>/
        turns.jsonl         # one codec-encoded turn per line
        projections.jsonl   # checkpointed compaction entries (slice B)

Pure appends — no rewrite path; the file's last line is the numbering's
source of truth, never a line count. A malformed or newer row raises,
never skips: skipping would silently drop a turn and corrupt the
numbering. Deliberately self-contained: `memory/journal.py` stays
MemoryVersion-typed, and importing it here would point an import edge
against `memory.file → conversation.file_turns`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.types import (
    CONVERSATION_FORMAT_VERSION,
    ConversationProjection,
    ConversationTurn,
    ProjectionKind,
)
from neosian._foundation.llm.codec import message_from_json, message_to_json
from neosian._foundation.shared.exceptions import (
    ConversationFormatUnsupportedError,
    ConversationIdInvalidError,
)
from neosian._foundation.shared.fileio import append_line, private_mkdir

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence
    from pathlib import Path

    from neosian._foundation.llm.base import Message
    from neosian._foundation.shared.clock import Clock

_CONVERSATIONS = "conversations"
_TURNS = "turns.jsonl"
_PROJECTIONS = "projections.jsonl"
_KINDS = ("log", "digest", "epoch")


class FileTurnStore(ConversationStore):
    """Turn persistence over a plain directory (mixin; the host class
    owns the three attributes below)."""

    _root: Path
    _clock: Clock
    _lock: asyncio.Lock

    async def append_turn(
        self, conversation_id: str, messages: Sequence[Message]
    ) -> ConversationTurn:
        conversation_id = parse_conversation_id(conversation_id)
        if not messages:
            raise ValueError("a turn must carry at least one message")
        async with self._lock:
            turns_file = self._turns_file(conversation_id)
            turn = self._last_number(conversation_id, turns_file) + 1
            created_at = self._turn_now()
            record = ConversationTurn(
                conversation_id=conversation_id,
                turn=turn,
                messages=tuple(messages),
                created_at=created_at,
            )
            private_mkdir(turns_file.parent)
            append_line(turns_file, _render_turn(record))
            return record

    async def read_turns(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationTurn, ...]:
        conversation_id = parse_conversation_id(conversation_id)
        _check_cursor(after, limit)
        turns = [turn for turn in self._all_turns(conversation_id) if turn.turn > after]
        return tuple(turns if limit is None else turns[:limit])

    async def last_turn_number(self, conversation_id: str) -> int:
        conversation_id = parse_conversation_id(conversation_id)
        return self._last_number(conversation_id, self._turns_file(conversation_id))

    async def append_projections(
        self, conversation_id: str, entries: Sequence[ConversationProjection]
    ) -> None:
        conversation_id = parse_conversation_id(conversation_id)
        if not entries:
            return
        async with self._lock:
            file = self._projections_file(conversation_id)
            private_mkdir(file.parent)
            append_line(file, "".join(_render_projection(entry) for entry in entries))

    async def read_projections(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationProjection, ...]:
        conversation_id = parse_conversation_id(conversation_id)
        _check_cursor(after, limit)
        file = self._projections_file(conversation_id)
        rows: list[ConversationProjection] = []
        if file.is_file():
            with file.open(encoding="utf-8", newline="") as handle:
                for number, line in enumerate(handle, start=1):
                    rows.append(_parse_projection(line, number, conversation_id))
        # Stable sort: insertion order breaks (turn, span) ties.
        rows.sort(key=lambda entry: (entry.turn, entry.span))
        selected = [entry for entry in rows if entry.turn > after]
        return tuple(selected if limit is None else selected[:limit])

    # Internal plumbing ----------------------------------------------------

    def _turn_now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Clock returned a naive datetime (ECOSYSTEM §9)")
        return now

    def _conversation_dir(self, conversation_id: str) -> Path:
        candidate = self._root / _CONVERSATIONS / conversation_id
        if not candidate.resolve().is_relative_to(self._root):
            raise ConversationIdInvalidError(conversation_id, "escapes the store root")
        return candidate

    def _turns_file(self, conversation_id: str) -> Path:
        return self._conversation_dir(conversation_id) / _TURNS

    def _projections_file(self, conversation_id: str) -> Path:
        return self._conversation_dir(conversation_id) / _PROJECTIONS

    def _all_turns(self, conversation_id: str) -> list[ConversationTurn]:
        file = self._turns_file(conversation_id)
        if not file.is_file():
            return []
        turns: list[ConversationTurn] = []
        with file.open(encoding="utf-8", newline="") as handle:
            for number, line in enumerate(handle, start=1):
                turns.append(_parse_turn(line, number, conversation_id))
        return turns

    def _last_number(self, conversation_id: str, turns_file: Path) -> int:
        if not turns_file.is_file():
            return 0
        last_line = ""
        last_number = 0
        with turns_file.open(encoding="utf-8", newline="") as handle:
            for line in handle:
                last_line = line
                last_number += 1
        if not last_line:
            return 0
        return _parse_turn(last_line, last_number, conversation_id).turn


def _check_cursor(after: int, limit: int | None) -> None:
    if after < 0:
        raise ValueError(f"after must be >= 0, got {after}")
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")


def _render_turn(record: ConversationTurn) -> str:
    data = {
        "neosian_format": CONVERSATION_FORMAT_VERSION,
        "turn": record.turn,
        "created_at": record.created_at.isoformat().replace("+00:00", "Z"),
        "messages": [message_to_json(message) for message in record.messages],
    }
    return json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n"


def _render_projection(entry: ConversationProjection) -> str:
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


def _parse_turn(line: str, number: int, conversation_id: str) -> ConversationTurn:
    where = f"turn-log line {number}"
    data = _parse_object(line, where, conversation_id)
    turn = data.get("turn")
    created_raw = data.get("created_at")
    encoded = data.get("messages")
    if (
        not isinstance(turn, int)
        or isinstance(turn, bool)
        or turn < 1
        or not isinstance(created_raw, str)
        or not isinstance(encoded, list)
        or not encoded
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
    )


def _parse_projection(
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
