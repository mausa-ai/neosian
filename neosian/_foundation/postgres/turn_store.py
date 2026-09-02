"""The ConversationStore seam over Postgres (mixin; PostgresStore owns
the pool, statements and clock).

CS3 rides `COALESCE(MAX(turn),0)+1` under `UNIQUE(conversation_id,
turn)`: two workers computing the same number collide on the primary key
and the loser retries with a fresh snapshot — numbers stay per-
conversation, monotonic, gapless, never reused. Messages are stored as
the public codec's JSON (CS5), verbatim in and out.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.types import (
    CONVERSATION_FORMAT_VERSION,
    ConversationTurn,
)
from neosian._foundation.llm.codec import message_to_json
from neosian._foundation.postgres.rows import (
    aware_now,
    conversation_projection,
    conversation_turn,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.conversation.types import ConversationProjection
    from neosian._foundation.llm.base import Message
    from neosian._foundation.postgres.pool import PostgresPool
    from neosian._foundation.postgres.statements import Statements
    from neosian._foundation.shared.clock import Clock


def _check_cursor(after: int, limit: int | None) -> None:
    if after < 0:
        raise ValueError(f"after must be >= 0, got {after}")
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")


class PostgresTurnStore(ConversationStore):
    """Turn persistence over the conversations/turns/projections tables."""

    _pool: PostgresPool
    _sql: Statements
    _clock: Clock

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
        encoded = json.dumps(
            [message_to_json(message) for message in messages],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        created_at = aware_now(self._clock)
        row = await self._pool.fetch_one_retry(
            self._sql.append_turn,
            {
                "conversation_id": conversation_id,
                "messages": encoded,
                "now": created_at,
                "format": CONVERSATION_FORMAT_VERSION,
                "actor": actor,
            },
        )
        return ConversationTurn(
            conversation_id=conversation_id,
            turn=int(row[0]),
            messages=tuple(messages),
            created_at=created_at,
            actor=actor,
        )

    async def read_turns(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationTurn, ...]:
        conversation_id = parse_conversation_id(conversation_id)
        _check_cursor(after, limit)
        rows = await self._pool.fetch(
            self._sql.read_turns,
            {"conversation_id": conversation_id, "after": after, "limit": limit},
        )
        return tuple(conversation_turn(conversation_id, row) for row in rows)

    async def last_turn_number(self, conversation_id: str) -> int:
        conversation_id = parse_conversation_id(conversation_id)
        row = await self._pool.fetch_one(
            self._sql.last_turn_number, {"conversation_id": conversation_id}
        )
        return int(row[0])

    async def append_projections(
        self, conversation_id: str, entries: Sequence[ConversationProjection]
    ) -> None:
        conversation_id = parse_conversation_id(conversation_id)
        if not entries:
            return
        encoded = json.dumps(
            [
                {
                    "turn": entry.turn,
                    "kind": entry.kind,
                    "text": entry.text,
                    "span": entry.span,
                }
                for entry in entries
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        await self._pool.execute(
            self._sql.append_projections,
            {
                "conversation_id": conversation_id,
                "entries": encoded,
                "now": aware_now(self._clock),
                "format": CONVERSATION_FORMAT_VERSION,
            },
        )

    async def read_projections(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationProjection, ...]:
        conversation_id = parse_conversation_id(conversation_id)
        _check_cursor(after, limit)
        rows = await self._pool.fetch(
            self._sql.read_projections,
            {"conversation_id": conversation_id, "after": after, "limit": limit},
        )
        return tuple(conversation_projection(conversation_id, row) for row in rows)
