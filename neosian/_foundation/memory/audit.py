"""The audit view (NL, DESIGN §20): what was done, by whom, when.

One read composed over the ABCs' own reads — `history`, `redactions`,
and `read_turns` when a conversation is named — so it answers identically
on FileStore, PostgresStore and through the daemon (`RemoteStore`), and
a host store that passes the kits answers it too. The engine holds no
state and interprets no actor: the filter is `actor_matches`'s
segment-prefix rule, and an unparseable actor matches only itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.memory.actor import actor_matches

if TYPE_CHECKING:
    from datetime import datetime

    from neosian._foundation.memory.base import MemoryStore

AuditEvent = Literal["created", "modified", "deleted", "redacted", "turn"]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One ledger line. Memory rows fill `path`/`version`; an erasure
    fills `path` (None scope-wide) and `count`; a turn fills
    `conversation_id`/`turn`."""

    created_at: datetime
    actor: str | None
    event: AuditEvent
    path: str | None = None
    version: int | None = None
    redacted: bool = False
    count: int | None = None
    conversation_id: str | None = None
    turn: int | None = None


async def audit(
    store: MemoryStore,
    scope: str,
    *,
    conversation_id: str | None = None,
    actor: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> tuple[AuditEntry, ...]:
    """The scope's ledger, newest first, optionally one conversation's
    turns merged in and the whole filtered to one actor's prefix."""
    entries: list[AuditEntry] = [
        AuditEntry(
            created_at=row.created_at,
            actor=row.actor,
            event=row.action,
            path=row.path,
            version=row.version,
            redacted=row.redacted,
        )
        for row in await store.history(scope, since=since)
    ]
    entries.extend(
        AuditEntry(
            created_at=act.created_at,
            actor=act.actor,
            event="redacted",
            path=act.path,
            count=act.count,
        )
        for act in await store.redactions(scope, since=since)
    )
    if conversation_id is not None:
        if not isinstance(store, ConversationStore):
            raise TypeError(
                f"{type(store).__name__} does not implement ConversationStore — "
                "a conversation's turns need both seams"
            )
        entries.extend(
            AuditEntry(
                created_at=turn.created_at,
                actor=turn.actor,
                event="turn",
                conversation_id=conversation_id,
                turn=turn.turn,
            )
            for turn in await store.read_turns(conversation_id)
            if since is None or turn.created_at >= since
        )
    if actor is not None:
        entries = [entry for entry in entries if actor_matches(entry.actor, actor)]
    entries.sort(key=lambda entry: entry.created_at, reverse=True)  # stable
    return tuple(entries)[:limit]
