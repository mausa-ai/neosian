"""History search — the one rule every substrate applies (DESIGN §32, N5).

`ConversationStore.search_turns` answers the same on files, Postgres,
the wire and any host store because the rule lives here and nowhere
else: a query is split on whitespace into lowercased terms; a turn
matches when every term is a case-insensitive substring of its
*searchable text*; hits come newest first under one total order; the
answer is bounded by `limit`. No ranking, no wildcards, no stemming.

The searchable text of a turn is `turn_text`: each message's text
content in order, each tool call as its name and compact JSON
arguments, tool results' text, joined by newlines, with no role labels
(a query "user" must never match every turn). A substrate stores the
rendering (Postgres, the SQLite example) or renders at read (files);
either way it is this function. `messages_text` is the same rule for a
turn that has no number yet (Postgres assigns it in SQL).

ASCII case folding is pinned by the kit; beyond ASCII each substrate
folds as its engine does (Python here, `ILIKE` there, SQLite's ASCII
`lower()`), which the docs say and the kit does not pin.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.llm.base import text_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from neosian._foundation.conversation.ids import ConversationId
    from neosian._foundation.conversation.types import ConversationTurn
    from neosian._foundation.llm.base import Message


def parse_query(query: str) -> tuple[str, ...]:
    """The lowercased, whitespace-split terms; a blank query is a
    programmer error like a negative cursor."""
    terms = tuple(query.lower().split())
    if not terms:
        raise ValueError("query must contain at least one term")
    return terms


def parse_conversations(
    conversations: Sequence[str] | None,
) -> tuple[ConversationId, ...] | None:
    """`None` is the whole store; a sequence is validated id by id, so a
    bad name fails before any I/O and an empty sequence stays empty."""
    if conversations is None:
        return None
    return tuple(parse_conversation_id(name) for name in conversations)


def check_limit(limit: int) -> None:
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")


def messages_text(messages: Sequence[Message]) -> str:
    """The searchable text of a turn's messages (see the module docstring)."""
    pieces: list[str] = []
    for message in messages:
        text = text_of(message)
        if text:
            pieces.append(text)
        for call in message.tool_calls:
            arguments = json.dumps(
                call.arguments, ensure_ascii=False, separators=(",", ":")
            )
            pieces.append(f"{call.name} {arguments}")
    return "\n".join(pieces)


def turn_text(turn: ConversationTurn) -> str:
    return messages_text(turn.messages)


def match_terms(text: str, terms: Sequence[str]) -> bool:
    """Every term a substring of the folded text."""
    folded = text.lower()
    return all(term in folded for term in terms)


def newest_first(turns: Iterable[ConversationTurn]) -> tuple[ConversationTurn, ...]:
    """The total order: `created_at`, then `conversation_id`, then `turn`,
    each descending — codepoint order on the id, which the SQL substrates
    spell as `COLLATE "C"` / `COLLATE BINARY`."""
    return tuple(
        sorted(
            turns,
            key=lambda turn: (turn.created_at, turn.conversation_id, turn.turn),
            reverse=True,
        )
    )
