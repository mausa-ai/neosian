"""The eraser's slice of the conversation conformance kit (N8, DESIGN §38).

Opt-in, beside `ConversationStoreContract`: `Erasable` is a protocol a
host store may leave unimplemented, so this slice is never inherited by
the kit. A store that implements it subclasses this class too, with the
same `store` fixture; the whole-store tests use ids derived from the
`conversation_id` fixture, so a long-lived store (the container leg)
stays honest by freshening that one id.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.exceptions import ConversationIdInvalidError

if TYPE_CHECKING:
    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.erasable import Erasable
    from neosian._foundation.conversation.types import ConversationTurn

_asyncio = pytest.mark.asyncio


def _exchange(text: str) -> tuple[Message, ...]:
    return (
        Message(role=Role.USER, content=text),
        Message(role=Role.ASSISTANT, content=f"re: {text}"),
    )


def _skeleton(turn: ConversationTurn) -> tuple[int, tuple[Message, ...], bool]:
    return turn.turn, turn.messages, turn.redacted


class ErasureContract:
    """`redact_turns` and `turn_redactions` on every substrate: twelve tests."""

    @pytest.fixture
    def conversation_id(self) -> str:
        """The conversation under test; override to exercise another."""
        return "erasure-kit"

    def stamped(self, store: Erasable, actor: str) -> str:
        """What the store records for an act by `actor` — identity
        everywhere but the daemon (§20); the Remote subclasses override."""
        del store
        return actor

    @staticmethod
    async def _seed(
        store: ConversationStore, conversation_id: str, count: int
    ) -> tuple[ConversationTurn, ...]:
        appended = []
        for n in range(1, count + 1):
            appended.append(
                await store.append_turn(
                    conversation_id, _exchange(f"turn {n} body"), actor=f"w{n}"
                )
            )
        return tuple(appended)

    @_asyncio
    async def test_redacting_every_turn_keeps_the_skeleton(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        before = await self._seed(turns, conversation_id, 3)
        assert await store.redact_turns(conversation_id, actor="eraser") == 3
        after = await turns.read_turns(conversation_id)
        assert [_skeleton(t) for t in after] == [
            (1, (), True),
            (2, (), True),
            (3, (), True),
        ]
        assert [t.created_at for t in after] == [t.created_at for t in before]
        assert [t.actor for t in after] == [t.actor for t in before]
        assert await turns.last_turn_number(conversation_id) == 3

    @_asyncio
    async def test_through_blanks_a_prefix(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        before = await self._seed(turns, conversation_id, 4)
        assert await store.redact_turns(conversation_id, through=2) == 2
        after = await turns.read_turns(conversation_id)
        assert [t.redacted for t in after] == [True, True, False, False]
        assert after[2].messages == before[2].messages
        assert after[3].messages == before[3].messages

    @_asyncio
    async def test_named_turns_blank_exactly_those(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        await self._seed(turns, conversation_id, 4)
        # An unknown number matches nothing; a repeated one counts once.
        assert await store.redact_turns(conversation_id, turns=(3, 1, 9, 3)) == 2
        after = await turns.read_turns(conversation_id)
        assert [t.redacted for t in after] == [True, False, True, False]
        (act,) = await store.turn_redactions(conversations=[conversation_id])
        assert act.turns == (1, 3)

    @_asyncio
    async def test_bad_selectors_are_refused_before_any_write(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        before = await self._seed(turns, conversation_id, 2)
        with pytest.raises(ValueError):
            await store.redact_turns(conversation_id, through=1, turns=(1,))
        for through in (0, -1):
            with pytest.raises(ValueError):
                await store.redact_turns(conversation_id, through=through)
        for named in ((0,), (-1, 2)):
            with pytest.raises(ValueError):
                await store.redact_turns(conversation_id, turns=named)
        assert await turns.read_turns(conversation_id) == before
        assert await store.turn_redactions(conversations=[conversation_id]) == ()

    @_asyncio
    async def test_an_unknown_conversation_matches_nothing(
        self, store: Erasable, conversation_id: str
    ) -> None:
        absent = f"{conversation_id}-absent"
        assert await store.redact_turns(absent) == 0
        assert await store.redact_turns(absent, through=5) == 0
        assert await store.turn_redactions(conversations=[absent]) == ()

    @_asyncio
    async def test_redaction_is_idempotent_in_count_and_effect(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        await self._seed(turns, conversation_id, 2)
        assert await store.redact_turns(conversation_id) == 2
        once = await turns.read_turns(conversation_id)
        assert await store.redact_turns(conversation_id) == 2
        assert await turns.read_turns(conversation_id) == once
        assert len(await store.turn_redactions(conversations=[conversation_id])) == 2

    @_asyncio
    async def test_redacted_turns_leave_search_and_stay_readable(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        await turns.append_turn(conversation_id, _exchange("osprey nest location"))
        await turns.append_turn(conversation_id, _exchange("cormorant"))
        assert len(await turns.search_turns("osprey")) == 1
        assert await store.redact_turns(conversation_id, turns=(1,)) == 1
        assert await turns.search_turns("osprey") == ()
        assert len(await turns.search_turns("cormorant")) == 1
        (recalled,) = await turns.read_turns(conversation_id, limit=1)
        assert (recalled.turn, recalled.messages, recalled.redacted) == (1, (), True)

    @_asyncio
    async def test_covering_projections_lose_their_text_and_nothing_else(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        await self._seed(turns, conversation_id, 4)
        entries = (
            ConversationProjection(turn=1, kind="log", text="one"),
            ConversationProjection(turn=2, kind="log", text="two"),
            ConversationProjection(turn=3, kind="epoch", text="fold 1-3", span=3),
            ConversationProjection(turn=4, kind="digest", text="four"),
        )
        await turns.append_projections(conversation_id, entries)
        assert await store.redact_turns(conversation_id, turns=(2,)) == 1
        after = await turns.read_projections(conversation_id)
        assert [(e.turn, e.kind, e.span) for e in after] == [
            (e.turn, e.kind, e.span) for e in entries
        ]
        assert [e.text for e in after] == ["one", "", "", "four"]

    @_asyncio
    async def test_the_trail_records_each_act_newest_first(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        await self._seed(turns, conversation_id, 3)
        assert await store.redact_turns(conversation_id, through=1, actor="a") == 1
        assert await store.redact_turns(conversation_id, turns=(3,), actor="b") == 1
        acts = await store.turn_redactions(conversations=[conversation_id])
        assert [
            (act.conversation_id, act.turns, act.actor, act.count) for act in acts
        ] == [
            (conversation_id, (3,), self.stamped(store, "b"), 1),
            (conversation_id, (1,), self.stamped(store, "a"), 1),
        ]
        assert all(act.created_at.tzinfo is not None for act in acts)
        assert acts[0].created_at >= acts[1].created_at
        assert await store.turn_redactions(
            conversations=[conversation_id], limit=1
        ) == (acts[0],)
        recent = await store.turn_redactions(
            conversations=[conversation_id], since=acts[0].created_at
        )
        assert recent[0] == acts[0]
        assert await store.turn_redactions(conversations=()) == ()
        assert await store.turn_redactions(conversations=[]) == ()

    @_asyncio
    async def test_the_trail_refuses_a_bad_window(self, store: Erasable) -> None:
        naive = datetime(2026, 10, 10, 12, 0, 0)  # noqa: DTZ001 - the point
        with pytest.raises(ValueError, match="timezone-aware"):
            await store.turn_redactions(since=naive)
        for limit in (0, -1):
            with pytest.raises(ValueError):
                await store.turn_redactions(limit=limit)

    @_asyncio
    async def test_the_trail_spans_the_store_and_narrows(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        other = f"{conversation_id}-b"
        await self._seed(turns, conversation_id, 1)
        await self._seed(turns, other, 1)
        await store.redact_turns(conversation_id, actor="first")
        await store.redact_turns(other, actor="second")
        ours = [
            act
            for act in await store.turn_redactions(limit=500)
            if act.conversation_id in {conversation_id, other}
        ]
        assert [act.conversation_id for act in ours] == [other, conversation_id]
        stamps = [act.created_at for act in ours]
        assert stamps == sorted(stamps, reverse=True)
        narrowed = await store.turn_redactions(conversations=[other])
        assert [act.conversation_id for act in narrowed] == [other]

    @_asyncio
    async def test_appending_after_redaction_continues_the_numbering(
        self, store: Erasable, conversation_id: str
    ) -> None:
        turns: ConversationStore = store  # type: ignore[assignment]
        await self._seed(turns, conversation_id, 2)
        assert await store.redact_turns(conversation_id) == 2
        fresh = await turns.append_turn(conversation_id, _exchange("after"))
        assert (fresh.turn, fresh.redacted) == (3, False)
        after = await turns.read_turns(conversation_id)
        assert [_skeleton(t) for t in after] == [
            (1, (), True),
            (2, (), True),
            (3, _exchange("after"), False),
        ]

    @_asyncio
    async def test_an_invalid_conversation_id_is_refused(self, store: Erasable) -> None:
        with pytest.raises(ConversationIdInvalidError):
            await store.redact_turns("a/b")
        with pytest.raises(ConversationIdInvalidError):
            await store.turn_redactions(conversations=["a/b"])
