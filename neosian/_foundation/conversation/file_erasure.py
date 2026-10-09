"""FileStore's eraser (N8, DESIGN §38; mixin over the root, clock and
lock, the `FileTurnStore` shape).

The turn substrate's one rewrite path. `turns.jsonl` is parsed whole (a
malformed row raises, as a read does: nothing is rewritten over a log
the store cannot read), the selected rows are replaced by their
skeletons, `projections.jsonl` loses the text of every entry covering
one, and both land atomically, fsync'd, private (#126); then one act is
appended to `redactions.jsonl` beside them, the trail the memory side
keeps per scope. All of it in a worker thread under the root-wide lock,
the memory `redact` idiom. The trail read walks the conversation
directories the search scan walks.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

from neosian._foundation.conversation.erasable import (
    check_trail_window,
    covers,
    newest_first,
    parse_selection,
    selected,
)
from neosian._foundation.conversation.file_rows import (
    CONVERSATIONS,
    PROJECTIONS,
    REDACTIONS,
    TURNS,
    parse_projection,
    parse_redaction,
    parse_turn,
    render_projection,
    render_redaction,
    render_turn,
)
from neosian._foundation.conversation.file_search import held_ids
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.search import parse_conversations
from neosian._foundation.conversation.types import ConversationRedaction
from neosian._foundation.shared.fileio import append_line, atomic_write
from neosian._foundation.shared.filelock import FileLock, locked

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from pathlib import Path

    from neosian._foundation.shared.clock import Clock


class FileErasureStore:
    """The `Erasable` protocol over a plain directory (mixin; the host
    class owns the three attributes below)."""

    _root: Path
    _clock: Clock
    _lock: FileLock

    @locked
    async def redact_turns(
        self,
        conversation_id: str,
        *,
        through: int | None = None,
        turns: Sequence[int] | None = None,
        actor: str | None = None,
    ) -> int:
        conversation_id = parse_conversation_id(conversation_id)
        through, named = parse_selection(through, turns)
        act = ConversationRedaction(
            conversation_id=conversation_id,
            turns=(),
            actor=actor,
            created_at=self._erasure_now(),
        )
        directory = self._root / CONVERSATIONS / conversation_id
        return await asyncio.to_thread(_redact, directory, act, through, named)

    @locked
    async def turn_redactions(
        self,
        *,
        conversations: Sequence[str] | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> tuple[ConversationRedaction, ...]:
        ids = parse_conversations(conversations)
        check_trail_window(since, limit)
        if ids == ():
            return ()
        parent = self._root / CONVERSATIONS
        return await asyncio.to_thread(_trail, parent, ids, since, limit)

    def _erasure_now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Clock returned a naive datetime (ECOSYSTEM §9)")
        return now


def _redact(
    directory: Path,
    act: ConversationRedaction,
    through: int | None,
    named: tuple[int, ...] | None,
) -> int:
    turns_file = directory / TURNS
    if not turns_file.is_file():
        return 0
    conversation_id = act.conversation_id
    rows = _rows(turns_file, conversation_id, parse_turn)
    matched = tuple(row.turn for row in rows if selected(row.turn, through, named))
    if not matched:
        return 0
    hit = set(matched)
    skeletons = [
        dataclasses.replace(row, messages=(), redacted=True) if row.turn in hit else row
        for row in rows
    ]
    atomic_write(turns_file, "".join(map(render_turn, skeletons)), fsync=True)
    projections_file = directory / PROJECTIONS
    if projections_file.is_file():
        entries = _rows(projections_file, conversation_id, parse_projection)
        blanked = [
            dataclasses.replace(entry, text="") if covers(entry, hit) else entry
            for entry in entries
        ]
        if blanked != entries:
            text = "".join(map(render_projection, blanked))
            atomic_write(projections_file, text, fsync=True)
    recorded = dataclasses.replace(act, turns=matched)
    append_line(directory / REDACTIONS, render_redaction(recorded), fsync=True)
    return len(matched)


def _trail(
    parent: Path,
    ids: tuple[str, ...] | None,
    since: datetime | None,
    limit: int,
) -> tuple[ConversationRedaction, ...]:
    acts: list[tuple[ConversationRedaction, int]] = []
    for conversation_id in held_ids(parent) if ids is None else ids:
        trail = parent / conversation_id / REDACTIONS
        if trail.is_file():
            rows = _rows(trail, conversation_id, parse_redaction)
            acts.extend((act, position) for position, act in enumerate(rows))
    return newest_first(acts, since)[:limit]


def _rows[T](
    file: Path, conversation_id: str, parse: Callable[[str, int, str], T]
) -> list[T]:
    with file.open(encoding="utf-8", newline="") as handle:
        return [
            parse(line, number, conversation_id)
            for number, line in enumerate(handle, start=1)
        ]
