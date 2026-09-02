"""The audit engine (NL, DESIGN §20) over a FileStore."""

from datetime import UTC, datetime

import pytest

from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.audit import audit
from neosian._foundation.memory.file import FileStore

_SCOPE = "user:demo"


async def _seed(store: FileStore) -> None:
    await store.write(_SCOPE, "a", "1", actor="claude-code:s1")
    await store.write(_SCOPE, "b", "2", actor="cli:local")
    await store.delete(_SCOPE, "a", actor="claude-code:s1#2")
    await store.redact(_SCOPE, path="b", actor="cli:local")
    await store.append_turn(
        "s1", [Message(role=Role.USER, content="hi")], actor="claude-code:s1"
    )


class TestAudit:
    async def test_merges_history_redactions_and_turns_newest_first(
        self, store: FileStore
    ) -> None:
        await _seed(store)
        entries = await audit(store, _SCOPE, conversation_id="s1")
        assert [e.event for e in entries] == [
            "turn",
            "redacted",
            "deleted",
            "created",
            "created",
        ]
        stamps = [e.created_at for e in entries]
        assert stamps == sorted(stamps, reverse=True)
        turn, redacted, deleted = entries[:3]
        assert (turn.conversation_id, turn.turn, turn.actor) == (
            "s1",
            1,
            "claude-code:s1",
        )
        assert (redacted.path, redacted.count) == ("b", 1)
        assert (deleted.path, deleted.version, deleted.actor) == (
            "a",
            2,
            "claude-code:s1#2",
        )
        assert entries[-1].redacted is False

    async def test_without_a_conversation_no_turns_are_read(
        self, store: FileStore
    ) -> None:
        await _seed(store)
        assert all(e.event != "turn" for e in await audit(store, _SCOPE))

    async def test_the_actor_filter_is_a_prefix(self, store: FileStore) -> None:
        await _seed(store)
        mine = await audit(store, _SCOPE, conversation_id="s1", actor="claude-code:s1")
        assert [e.event for e in mine] == ["turn", "deleted", "created"]
        assert await audit(store, _SCOPE, actor="claude-code:s") == ()

    async def test_since_and_limit(self, store: FileStore) -> None:
        await _seed(store)
        entries = await audit(store, _SCOPE, conversation_id="s1")
        newest = entries[0]
        assert await audit(store, _SCOPE, conversation_id="s1", limit=1) == (newest,)
        since = await audit(
            store, _SCOPE, conversation_id="s1", since=newest.created_at
        )
        assert since and all(e.created_at >= newest.created_at for e in since)
        naive = datetime(2026, 9, 2)  # noqa: DTZ001 - the point
        with pytest.raises(ValueError, match="timezone-aware"):
            await audit(store, _SCOPE, since=naive)
        assert await audit(store, "user:nobody") == ()

    async def test_a_memory_only_store_cannot_answer_for_a_conversation(
        self, store: FileStore
    ) -> None:
        class MemoryOnly:  # duck-typed: history/redactions only
            history = store.history
            redactions = store.redactions

        with pytest.raises(TypeError, match="ConversationStore"):
            await audit(MemoryOnly(), _SCOPE, conversation_id="s1")  # type: ignore[arg-type]

    def test_the_entry_is_frozen(self) -> None:
        from neosian._foundation.memory.audit import AuditEntry

        entry = AuditEntry(
            created_at=datetime(2026, 9, 2, tzinfo=UTC), actor=None, event="turn"
        )
        with pytest.raises(AttributeError):
            entry.actor = "x"  # type: ignore[misc]
