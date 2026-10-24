"""Old turns, erasure trails and scalar metadata for operator parity."""

from __future__ import annotations

from datetime import UTC, datetime

from neosian import FileStore, Message, Role
from neosian.conversation import (
    ConversationProjection,
    ConversationRedaction,
    ConversationTurn,
)
from neosian.memory import (
    ConversationArchive,
    MemoryDocument,
    MemoryRedaction,
    MemoryVersion,
    ScopeArchive,
)
from tests.differential.harness import Workspace

STAMP = datetime(2020, 1, 2, tzinfo=UTC)
SCOPE = "user:operator"
OLD = "old-session"


async def augment(space: Workspace) -> FileStore:
    store = FileStore(space.home)
    await store.restore_scope(
        ScopeArchive(
            scope=SCOPE,
            documents=(
                MemoryDocument(
                    scope=SCOPE,
                    path="note",
                    content="café ✓\n",
                    version=2,
                    created_at=STAMP,
                    updated_at=STAMP,
                    actor="opaque actor",
                    extra={"reviewed": True, "rank": 7, "comment": "one: two"},
                ),
            ),
            versions=(
                MemoryVersion("note", 1, "created", "first", "opaque actor", STAMP),
                MemoryVersion("note", 2, "modified", "café ✓\n", "opaque actor", STAMP),
                MemoryVersion("gone", 1, "deleted", "", None, STAMP, redacted=True),
            ),
            redactions=(MemoryRedaction("gone", "cli:erase", STAMP, 1),),
        )
    )
    turns = tuple(
        ConversationTurn(
            OLD,
            n,
            (Message(role=Role.USER, content=f"old turn {n}"),),
            STAMP,
            actor=f"cli:writer/conv:{OLD}#{n}",
        )
        for n in range(1, 5)
    )
    await store.restore_conversation(
        ConversationArchive(
            OLD,
            turns,
            (
                ConversationProjection(2, "digest", "first pair", 2),
                ConversationProjection(4, "log", "second pair", 2),
            ),
        )
    )
    await store.restore_conversation(
        ConversationArchive(
            "already-erased",
            (ConversationTurn("already-erased", 1, (), STAMP, redacted=True),),
            (),
            (ConversationRedaction("already-erased", (1,), "opaque actor", STAMP),),
        )
    )
    await store.restore_conversation(
        ConversationArchive(
            "projection-only", (), (ConversationProjection(1, "log", "held"),)
        )
    )
    for scope in (SCOPE, "user:other"):
        await store.write(
            scope,
            f"sessions/{OLD}",
            "# Session\n\n- continues: earlier-session\n\nprivate summary\n",
            actor=f"cli:writer/conv:{OLD}#4",
        )
    return store
