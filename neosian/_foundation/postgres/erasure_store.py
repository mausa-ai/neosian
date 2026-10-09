"""The `Erasable` protocol over Postgres (N8, DESIGN §38; mixin, the
`PostgresTurnStore` shape: the host owns the pool, statements and clock).

One statement per act (`statements_erasure.py`): the selected turns lose
their message array and search text and gain the flag, every projection
entry covering one loses its text, and the trail row lands in the same
round trip when anything matched. No primary key races here, so no
retry; the clock stamps the act, never SQL `now()` (ledger #35).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.conversation.erasable import (
    check_trail_window,
    parse_selection,
)
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.search import parse_conversations
from neosian._foundation.postgres.rows import aware_now, turn_redaction

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from neosian._foundation.conversation.types import ConversationRedaction
    from neosian._foundation.postgres.pool import PostgresPool
    from neosian._foundation.postgres.statements_erasure import ErasureStatements
    from neosian._foundation.shared.clock import Clock


class PostgresErasureStore:
    _pool: PostgresPool
    _erasure_sql: ErasureStatements
    _clock: Clock

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
        row = await self._pool.fetch_one(
            self._erasure_sql.redact_turns,
            {
                "conversation_id": conversation_id,
                "through": through,
                "all": named is None,
                "turns": list(named or ()),
                "actor": actor,
                "now": aware_now(self._clock),
            },
        )
        return int(row[0])

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
        rows = await self._pool.fetch(
            self._erasure_sql.read_turn_redactions,
            {
                "all": ids is None,
                "ids": list(ids or ()),
                "since": since,
                "limit": limit,
            },
        )
        return tuple(turn_redaction(row) for row in rows)
