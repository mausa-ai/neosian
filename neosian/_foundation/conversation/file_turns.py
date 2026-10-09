"""FileStore turn persistence — the file-substrate half of DESIGN §9.8.

Mixed into `memory.file.FileStore`, which owns the root, clock and lock
(shared with the memory side, so a turn append and a memory write never
interleave). Layout, sibling of the percent-encoded scope directories —
every scope component carries a `%3A`, so `conversations/` never collides:

    root/conversations/<conversation_id>/
        turns.jsonl         # one codec-encoded turn per line
        projections.jsonl   # checkpointed compaction entries (slice B)
        redactions.jsonl    # the erasure trail (N8, `file_erasure.py`)

Pure appends here; the one rewrite path is the eraser's (N8, §38). The
file's last line is the numbering's source of truth, never a line
count. A malformed or newer row raises, never skips: skipping would
silently drop a turn and corrupt the numbering. The row codec and the
layout names live in `file_rows.py`, the search scan in
`file_search.py` (a worker thread, like the memory listings), the
eraser in `file_erasure.py`. Deliberately self-contained: `memory/journal.py` stays
MemoryVersion-typed, and importing it here would point an import edge
against `memory.file → conversation.file_turns`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.file_rows import (
    CONVERSATIONS,
    PROJECTIONS,
    TURNS,
    parse_projection,
    parse_turn,
    render_projection,
    render_turn,
)
from neosian._foundation.conversation.file_search import search_files
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.search import (
    check_limit,
    parse_conversations,
    parse_query,
)
from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.shared.exceptions import ConversationIdInvalidError
from neosian._foundation.shared.fileio import append_line, private_mkdir
from neosian._foundation.shared.filelock import FileLock, locked

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    from neosian._foundation.llm.base import Message
    from neosian._foundation.shared.clock import Clock


class FileTurnStore(ConversationStore):
    """Turn persistence over a plain directory (mixin; the host class
    owns the three attributes below)."""

    _root: Path
    _clock: Clock
    _lock: FileLock

    @locked
    async def append_turn(
        self,
        conversation_id: str,
        messages: Sequence[Message],
        *,
        actor: str | None = None,
    ) -> ConversationTurn:
        conversation_id = parse_conversation_id(conversation_id)
        if not messages:
            raise ValueError("a turn must carry at least one message")
        turns_file = self._turns_file(conversation_id)
        turn = self._last_number(conversation_id, turns_file) + 1
        created_at = self._turn_now()
        record = ConversationTurn(
            conversation_id=conversation_id,
            turn=turn,
            messages=tuple(messages),
            created_at=created_at,
            actor=actor,
        )
        private_mkdir(turns_file.parent)
        append_line(turns_file, render_turn(record))
        return record

    @locked
    async def read_turns(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationTurn, ...]:
        conversation_id = parse_conversation_id(conversation_id)
        _check_cursor(after, limit)
        turns = [turn for turn in self._all_turns(conversation_id) if turn.turn > after]
        return tuple(turns if limit is None else turns[:limit])

    @locked
    async def last_turn_number(self, conversation_id: str) -> int:
        conversation_id = parse_conversation_id(conversation_id)
        return self._last_number(conversation_id, self._turns_file(conversation_id))

    @locked
    async def search_turns(
        self,
        query: str,
        *,
        conversations: Sequence[str] | None = None,
        limit: int = 50,
    ) -> tuple[ConversationTurn, ...]:
        terms = parse_query(query)
        ids = parse_conversations(conversations)
        check_limit(limit)
        if ids == ():
            return ()
        return await asyncio.to_thread(
            search_files, self._root / CONVERSATIONS, ids, terms, limit
        )

    @locked
    async def append_projections(
        self, conversation_id: str, entries: Sequence[ConversationProjection]
    ) -> None:
        conversation_id = parse_conversation_id(conversation_id)
        if not entries:
            return
        file = self._projections_file(conversation_id)
        private_mkdir(file.parent)
        append_line(file, "".join(render_projection(entry) for entry in entries))

    @locked
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
                    rows.append(parse_projection(line, number, conversation_id))
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
        candidate = self._root / CONVERSATIONS / conversation_id
        if not candidate.resolve().is_relative_to(self._root):
            raise ConversationIdInvalidError(conversation_id, "escapes the store root")
        return candidate

    def _turns_file(self, conversation_id: str) -> Path:
        return self._conversation_dir(conversation_id) / TURNS

    def _projections_file(self, conversation_id: str) -> Path:
        return self._conversation_dir(conversation_id) / PROJECTIONS

    def _all_turns(self, conversation_id: str) -> list[ConversationTurn]:
        file = self._turns_file(conversation_id)
        if not file.is_file():
            return []
        turns: list[ConversationTurn] = []
        with file.open(encoding="utf-8", newline="") as handle:
            for number, line in enumerate(handle, start=1):
                turns.append(parse_turn(line, number, conversation_id))
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
        return parse_turn(last_line, last_number, conversation_id).turn


def _check_cursor(after: int, limit: int | None) -> None:
    if after < 0:
        raise ValueError(f"after must be >= 0, got {after}")
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
