"""Store-wide operator search joins messages where scopes are enumerable."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from neosian._foundation.conversation.hits import hit_json, snippet, stamp
from neosian._foundation.conversation.types import ConversationTurn
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.portable import Portable
from neosian._foundation.messaging.core import Mailbox
from neosian._foundation.messaging.history import annotation


async def search_hits(
    store: MemoryStore,
    turns: Sequence[ConversationTurn],
    terms: Sequence[str],
    ids: Sequence[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Preserve turn-hit fields and order; add explicitly typed message hits."""
    rows: list[tuple[datetime, str, dict[str, Any]]] = [
        (
            turn.created_at,
            f"turn:{turn.conversation_id}:{turn.turn:020}",
            hit_json(turn, terms),
        )
        for turn in turns
    ]
    if isinstance(store, Portable) and (scopes := await store.scopes()):
        mailbox = Mailbox(
            MemoryConfig(
                store,
                tuple(Mount(scope, f"scope-{n}") for n, scope in enumerate(scopes)),
            )
        )
        for receipt in await mailbox.all():
            item = receipt.message
            if ids is not None and item.about not in ids:
                continue
            note = annotation(item)
            for _, _, hit in rows:
                if (
                    hit.get("type") != "message"
                    and item.about == hit["conversation_id"]
                    and item.about_turn in (None, hit["turn"])
                ):
                    hit.setdefault("annotations", []).append(note)
            versions = await mailbox.history(item.scope, item.id)
            text = "\n".join([item.body, *(v.outcome or "" for v in versions)])
            if all(term in text.lower() for term in terms):
                rows.append(
                    (
                        item.created_at,
                        f"message:{item.id}",
                        {
                            "type": "message",
                            "message_id": item.id,
                            "scope": item.scope,
                            "conversation_id": item.about,
                            "turn": item.about_turn,
                            "created_at": stamp(item.created_at),
                            "actor": item.actor,
                            "status": item.status,
                            "snippet": snippet(text, terms),
                            "annotation": note,
                        },
                    )
                )
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in rows[:limit]]
