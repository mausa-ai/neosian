"""PostgresStore's side of store mobility (NC4, §26; mixin — the host
owns the pool, the statements and the clock).

Each unit is one statement (`statements_portable.py`), retried through
the pool like every mutation: a concurrent creator that wins the race
makes the retry's gate see an occupied unit and report the conflict,
never a raw primary-key error. Substrate limit (ledger #38): a
NUL-bearing row fails its unit's statement whole — the unit stays
empty. The `conversations.created_at` parent column, invisible to every
ABC read, takes the first turn's timestamp (else the clock's now).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.types import CONVERSATION_FORMAT_VERSION
from neosian._foundation.llm.codec import message_to_json
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.memory.types import MEMORY_FORMAT_VERSION
from neosian._foundation.postgres.rows import aware_now
from neosian._foundation.shared.exceptions import (
    ConversationConflictError,
    MemoryConflictError,
)

if TYPE_CHECKING:
    from datetime import datetime

    from neosian._foundation.memory.portable import ConversationArchive, ScopeArchive
    from neosian._foundation.postgres.pool import PostgresPool
    from neosian._foundation.postgres.statements_portable import PortableStatements
    from neosian._foundation.shared.clock import Clock


class PostgresPortableStore:
    _pool: PostgresPool
    _portable_sql: PortableStatements
    _clock: Clock

    async def scopes(self) -> tuple[str, ...]:
        rows = await self._pool.fetch(self._portable_sql.list_scopes, {})
        return tuple(str(row[0]) for row in rows)

    async def conversations(self) -> tuple[str, ...]:
        rows = await self._pool.fetch(self._portable_sql.list_conversations, {})
        return tuple(str(row[0]) for row in rows)

    async def restore_scope(self, archive: ScopeArchive) -> None:
        scope = parse_scope(archive.scope)
        row = await self._pool.fetch_one_retry(
            self._portable_sql.restore_scope,
            {
                "scope": scope,
                "documents": _dump(
                    {
                        "path": d.path,
                        "content": d.content,
                        "version": d.version,
                        "created_at": _stamp(d.created_at),
                        "updated_at": _stamp(d.updated_at),
                        "actor": d.actor,
                        "redacted": d.redacted,
                        "extra": dict(d.extra),
                    }
                    for d in archive.documents
                ),
                "versions": _dump(
                    {
                        "path": r.path,
                        "version": r.version,
                        "action": r.action,
                        "content": r.content,
                        "actor": r.actor,
                        "created_at": _stamp(r.created_at),
                        "redacted": r.redacted,
                    }
                    for r in archive.versions
                ),
                "redactions": _dump(
                    {
                        "path": a.path,
                        "actor": a.actor,
                        "count": a.count,
                        "created_at": _stamp(a.created_at),
                    }
                    for a in archive.redactions
                ),
                "format": MEMORY_FORMAT_VERSION,
            },
        )
        if not row[0]:
            raise MemoryConflictError(scope, None, "target_occupied")

    async def restore_conversation(self, archive: ConversationArchive) -> None:
        conversation_id = parse_conversation_id(archive.conversation_id)
        first = archive.turns[0].created_at if archive.turns else aware_now(self._clock)
        row = await self._pool.fetch_one_retry(
            self._portable_sql.restore_conversation,
            {
                "conversation_id": conversation_id,
                "now": first,
                "turns": _dump(
                    {
                        "turn": t.turn,
                        "messages": [message_to_json(m) for m in t.messages],
                        "created_at": _stamp(t.created_at),
                        "actor": t.actor,
                    }
                    for t in archive.turns
                ),
                "projections": _dump(
                    {"turn": p.turn, "kind": p.kind, "text": p.text, "span": p.span}
                    for p in archive.projections
                ),
                "format": CONVERSATION_FORMAT_VERSION,
            },
        )
        if not row[0]:
            raise ConversationConflictError(conversation_id, "target_occupied")


def _stamp(value: datetime) -> str:
    return value.isoformat()  # tz-aware by contract; the cast keeps the instant


def _dump(items: Any) -> str:
    return json.dumps(list(items), ensure_ascii=True, separators=(",", ":"))
