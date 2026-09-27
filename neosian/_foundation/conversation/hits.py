"""How a search answers (DESIGN §32): one rendering for the tool, its
MCP twin and the shell verb.

A hit is a header naming the turn, `[<conversation> #<turn>] <stamp>
<actor>`, and a snippet: the first line of the turn's searchable text
that holds a term, windowed around the earliest match. The window's
edges are moved off any atom they land in (§23): a URL, path, id or
`[link N]` handle is kept whole when it is short and dropped whole when
it is not, never split. Pure functions, no I/O.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.conversation.links import boundary
from neosian._foundation.conversation.search import turn_text

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from neosian._foundation.conversation.types import ConversationTurn

SNIPPET_CHARS: Final = 160
# The match sits a quarter in; an atom longer than half the window is
# dropped whole rather than stretched into it.
_LEAD: Final = SNIPPET_CHARS // 4
_ATOM_MAX: Final = SNIPPET_CHARS // 2
_CUT: Final = "…"


def stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def hit_header(turn: ConversationTurn) -> str:
    return (
        f"[{turn.conversation_id} #{turn.turn}] "
        f"{stamp(turn.created_at)} {turn.actor or '-'}"
    )


def _first_match(text: str, terms: Sequence[str]) -> tuple[str, int]:
    """The flattened line holding the most terms (the earliest on a tie)
    and its earliest match's offset; the first line at 0 when none
    matches."""
    lines = [" ".join(line.split()) for line in text.splitlines()] or [""]
    best: tuple[int, int, str] = (0, 0, lines[0])
    for flat in lines:
        starts = [
            found.start()
            for term in terms
            if (found := re.search(re.escape(term), flat, re.IGNORECASE))
        ]
        if len(starts) > best[0]:
            best = (len(starts), min(starts), flat)
    return best[2], best[1]


def snippet(text: str, terms: Sequence[str]) -> str:
    """The matching line, whole when it fits, else `SNIPPET_CHARS` around
    the first match with `…` marking each cut edge."""
    flat, at = _first_match(text, terms)
    if len(flat) <= SNIPPET_CHARS:
        return flat
    start = min(max(at - _LEAD, 0), len(flat) - SNIPPET_CHARS)
    end = start + SNIPPET_CHARS
    head = boundary(flat, start)
    tail = boundary(flat, end)
    lo = head[0] if head[1] - head[0] <= _ATOM_MAX else head[1]
    hi = tail[1] if tail[1] - tail[0] <= _ATOM_MAX else tail[0]
    piece = flat[lo:hi].strip()
    before = f"{_CUT} " if lo > 0 else ""
    after = f" {_CUT}" if hi < len(flat) else ""
    return f"{before}{piece}{after}"


def hit_block(turn: ConversationTurn, terms: Sequence[str]) -> str:
    """The tool's rendering: the header, the snippet indented under it."""
    return f"{hit_header(turn)}\n  {snippet(turn_text(turn), terms)}"


def hit_line(turn: ConversationTurn, terms: Sequence[str]) -> str:
    """The shell's rendering: one line, the audit verb's two-space columns."""
    return f"{hit_header(turn)}  {snippet(turn_text(turn), terms)}"


def hit_json(turn: ConversationTurn, terms: Sequence[str]) -> dict[str, Any]:
    return {
        "conversation_id": turn.conversation_id,
        "turn": turn.turn,
        "created_at": stamp(turn.created_at),
        "actor": turn.actor,
        "snippet": snippet(turn_text(turn), terms),
    }


def render_hits(
    turns: Sequence[ConversationTurn], terms: Sequence[str], *, footer: str
) -> str:
    return "\n".join([*(hit_block(turn, terms) for turn in turns), footer])
