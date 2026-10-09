"""The file substrate's search scan (DESIGN §32): `turns.jsonl` read line
by line, one rule confirmed on the decoded turn.

A line is decoded only when it can match: every term that the file
could hold verbatim — printable ASCII free of `"` and `\\`, everything
else being escaped by `json.dumps(ensure_ascii=True)` — must appear in
the raw line first. A non-candidate line is never parsed, so a search
costs one substring test per turn it does not answer, and a malformed
line lacking every such term is invisible to a search (reads still
raise; the numbering's truth is `read_turns`). A candidate line that
is malformed raises exactly as a read does. Runs in a worker thread
like the memory listings; imports no `memory` module (the edge
`file_turns.py` records).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.conversation.file_rows import TURNS, parse_turn
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.search import (
    match_terms,
    newest_first,
    turn_text,
)
from neosian._foundation.shared.exceptions import ConversationIdInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from neosian._foundation.conversation.types import ConversationTurn


def prefilterable(term: str) -> bool:
    """Whether the file holds the term verbatim when a turn holds it."""
    return (
        term.isascii() and term.isprintable() and '"' not in term and "\\" not in term
    )


def scan_turns(
    file: Path, conversation_id: str, terms: tuple[str, ...]
) -> list[ConversationTurn]:
    raw = [term for term in terms if prefilterable(term)]
    found: list[ConversationTurn] = []
    with file.open(encoding="utf-8", newline="") as handle:
        for number, line in enumerate(handle, start=1):
            lowered = line.lower()
            if not all(term in lowered for term in raw):
                continue
            turn = parse_turn(line, number, conversation_id)
            if match_terms(turn_text(turn), terms):
                found.append(turn)
    return found


def search_files(
    parent: Path,
    ids: tuple[str, ...] | None,
    terms: tuple[str, ...],
    limit: int,
) -> tuple[ConversationTurn, ...]:
    """Every matching turn of the named conversations — or of every one
    under `parent` — newest first, cut to `limit`."""
    found: list[ConversationTurn] = []
    for conversation_id in held_ids(parent) if ids is None else ids:
        file = parent / conversation_id / TURNS
        if file.is_file():
            found.extend(scan_turns(file, conversation_id, terms))
    return newest_first(found)[:limit]


def held_ids(parent: Path) -> tuple[str, ...]:
    """Every conversation directory under `parent` with a grammatical
    name; a foreign name is skipped, as listings skip foreign files."""
    if not parent.is_dir():
        return ()
    ids: list[str] = []
    for child in parent.iterdir():
        try:
            ids.append(parse_conversation_id(child.name))
        except ConversationIdInvalidError:
            continue  # a foreign name is not ours
    return tuple(ids)
