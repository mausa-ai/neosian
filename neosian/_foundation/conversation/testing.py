"""ConversationStoreContract — the shipped conformance kit (DESIGN §9.7).

Subclass it in your test suite, provide a `store` fixture, and inherit
the cross-implementation tests that keep host stores honest. Requires
pytest and pytest-asyncio; neosian itself never imports this module at
runtime.

    class TestMyStore(ConversationStoreContract):
        @pytest.fixture
        def store(self) -> MyStore: ...

The `store` fixture must be function-scoped, empty and isolated —
`test_store_starts_empty` fails loudly when it leaks state. Override
`plant_raw_turn` to enable the two substrate-planting tests (format
refusal, malformed-row raise); by default they skip. Two ids differing
only in case are never used (case-insensitive filesystems are a legal
substrate).
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

import pytest

from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.llm.base import (
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolCall,
)
from neosian._foundation.shared.exceptions import (
    ConversationFormatUnsupportedError,
    ConversationIdInvalidError,
)
from neosian._foundation.shared.types import ToolCallId, ToolName

if TYPE_CHECKING:
    from neosian._foundation.conversation.base import ConversationStore

_asyncio = pytest.mark.asyncio

_BAD_IDS = ("", ".", "..", "a/b", "user:1", "a b", "x" * 129, "a\n")

_ROUND_TRIP: tuple[tuple[Message, ...], ...] = (
    (Message(role=Role.USER, content="plain text"),),
    (Message(role=Role.USER, content=""),),
    (
        Message(role=Role.USER, content="think"),
        Message(role=Role.ASSISTANT, content=None, reasoning="chain of thought"),
    ),
    (
        Message(role=Role.USER, content="call tools"),
        Message(
            role=Role.ASSISTANT,
            content=None,
            tool_calls=[
                ToolCall(
                    id=ToolCallId("call-1"),
                    name=ToolName("lookup"),
                    arguments={"query": "x", "options": {"depth": 2}},
                ),
                ToolCall(
                    id=ToolCallId("call-2"),
                    name=ToolName("fetch"),
                    arguments={},
                ),
            ],
        ),
        Message(role=Role.TOOL, content="result", tool_call_id=ToolCallId("call-1")),
        Message(role=Role.ASSISTANT, content="done"),
    ),
    (
        Message(
            role=Role.USER,
            content=[
                TextBlock(text="what is this?"),
                ImageBlock(url="https://example.test/x.png"),
            ],
        ),
        Message(role=Role.ASSISTANT, content="a picture"),
    ),
    (Message(role=Role.USER, content="---\nfrontmatter? café ✓\r\nline"),),
)


class ConversationStoreContract:
    """Inherit ~26 conformance tests; provide a `store` fixture."""

    @pytest.fixture
    def conversation_id(self) -> str:
        """The conversation under test; override to exercise another."""
        return "contract-kit"

    def stamped(self, store: ConversationStore, actor: str) -> str:
        """What the store records for a turn appended as `actor` — identity
        everywhere but the daemon (§20); the Remote subclasses override."""
        del store
        return actor

    async def plant_raw_turn(
        self, store: ConversationStore, conversation_id: str, *, line: str
    ) -> None:
        """Write one raw row directly into the substrate's turn log,
        bypassing the store. Override per substrate; the default skips."""
        del store, conversation_id, line
        pytest.skip("plant_raw_turn not implemented for this substrate")

    async def plant_raw_projection(
        self, store: ConversationStore, conversation_id: str, *, line: str
    ) -> None:
        """Write one raw row directly into the substrate's projection log,
        bypassing the store. Override per substrate; the default skips."""
        del store, conversation_id, line
        pytest.skip("plant_raw_projection not implemented for this substrate")

    @staticmethod
    def _exchange(text: str) -> tuple[Message, ...]:
        return (
            Message(role=Role.USER, content=text),
            Message(role=Role.ASSISTANT, content=f"re: {text}"),
        )

    # Turns ----------------------------------------------------------------

    @_asyncio
    async def test_actor_is_recorded_verbatim_and_defaults_to_none(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        """NL (DESIGN §20): who appended a turn rides the row, opaque."""
        bare = await store.append_turn(conversation_id, self._exchange("a"))
        named = await store.append_turn(
            conversation_id, self._exchange("b"), actor="claude-code:s1"
        )
        # A bodyless append records the daemon's client, else nothing.
        assert bare.actor in (None, self.stamped(store, "").rstrip("/") or None)
        stamped = self.stamped(store, "claude-code:s1")
        assert named.actor == stamped
        turns = await store.read_turns(conversation_id)
        assert [turn.actor for turn in turns] == [bare.actor, stamped]

    @_asyncio
    async def test_store_starts_empty(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        assert await store.read_turns(conversation_id) == ()
        assert await store.last_turn_number(conversation_id) == 0

    @_asyncio
    async def test_an_append_is_visible_to_the_next_read(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        """CS2, read-your-writes within a task — named rather than leaned
        on silently by every other test (NQ2, MC-14)."""
        first = await store.append_turn(conversation_id, self._exchange("a"))
        assert await store.last_turn_number(conversation_id) == first.turn
        assert [t.turn for t in await store.read_turns(conversation_id)] == [first.turn]
        second = await store.append_turn(conversation_id, self._exchange("b"))
        assert await store.last_turn_number(conversation_id) == second.turn
        assert [t.turn for t in await store.read_turns(conversation_id)] == [
            first.turn,
            second.turn,
        ]
        assert [
            t.turn for t in await store.read_turns(conversation_id, after=first.turn)
        ] == [second.turn]

    @_asyncio
    async def test_append_assigns_turn_one_and_reads_back(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        appended = await store.append_turn(conversation_id, self._exchange("hi"))
        assert appended.turn == 1
        assert appended.conversation_id == conversation_id
        turns = await store.read_turns(conversation_id)
        assert len(turns) == 1
        assert turns[0].turn == 1
        assert turns[0].messages == appended.messages

    @_asyncio
    async def test_turn_numbers_are_monotonic_and_gapless(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        for expected in range(1, 5):
            appended = await store.append_turn(
                conversation_id, self._exchange(str(expected))
            )
            assert appended.turn == expected
        turns = await store.read_turns(conversation_id)
        assert [t.turn for t in turns] == [1, 2, 3, 4]

    @pytest.mark.parametrize("messages", _ROUND_TRIP)
    @_asyncio
    async def test_messages_round_trip_verbatim(
        self,
        store: ConversationStore,
        conversation_id: str,
        messages: tuple[Message, ...],
    ) -> None:
        await store.append_turn(conversation_id, messages)
        (turn,) = await store.read_turns(conversation_id)
        assert turn.messages == messages

    @_asyncio
    async def test_every_returned_datetime_is_utc_aware(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        appended = await store.append_turn(conversation_id, self._exchange("t"))
        (read,) = await store.read_turns(conversation_id)
        for stamp in (appended.created_at, read.created_at):
            assert stamp.tzinfo is not None
            assert stamp.utcoffset() is not None
            assert stamp.astimezone(UTC) == stamp

    @_asyncio
    async def test_created_at_is_non_decreasing_across_turns(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        first = await store.append_turn(conversation_id, self._exchange("a"))
        second = await store.append_turn(conversation_id, self._exchange("b"))
        assert second.created_at >= first.created_at

    @_asyncio
    async def test_read_turns_after_is_exclusive(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        for text in ("a", "b", "c"):
            await store.append_turn(conversation_id, self._exchange(text))
        turns = await store.read_turns(conversation_id, after=2)
        assert [t.turn for t in turns] == [3]
        assert await store.read_turns(conversation_id, after=3) == ()

    @_asyncio
    async def test_read_turns_limit_takes_the_oldest_after_the_cursor(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        for text in ("a", "b", "c", "d"):
            await store.append_turn(conversation_id, self._exchange(text))
        assert await store.read_turns(conversation_id, limit=0) == ()
        turns = await store.read_turns(conversation_id, after=1, limit=2)
        assert [t.turn for t in turns] == [2, 3]
        everything = await store.read_turns(conversation_id, limit=99)
        assert [t.turn for t in everything] == [1, 2, 3, 4]

    @_asyncio
    async def test_read_turns_unknown_conversation_is_empty(
        self, store: ConversationStore
    ) -> None:
        assert await store.read_turns("never-written") == ()

    @_asyncio
    async def test_last_turn_number_tracks_appends(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(conversation_id, self._exchange("a"))
        assert await store.last_turn_number(conversation_id) == 1
        await store.append_turn(conversation_id, self._exchange("b"))
        assert await store.last_turn_number(conversation_id) == 2

    @_asyncio
    async def test_conversations_are_isolated(self, store: ConversationStore) -> None:
        await store.append_turn("thread-a", self._exchange("a"))
        appended = await store.append_turn("thread-b", self._exchange("b"))
        assert appended.turn == 1
        assert [t.turn for t in await store.read_turns("thread-a")] == [1]
        (turn_b,) = await store.read_turns("thread-b")
        assert turn_b.messages[0].content == "b"

    @_asyncio
    async def test_append_empty_messages_raises(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        with pytest.raises(ValueError):
            await store.append_turn(conversation_id, ())

    @_asyncio
    async def test_negative_after_or_limit_raises(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        with pytest.raises(ValueError):
            await store.read_turns(conversation_id, after=-1)
        with pytest.raises(ValueError):
            await store.read_turns(conversation_id, limit=-1)
        with pytest.raises(ValueError):
            await store.read_projections(conversation_id, after=-1)
        with pytest.raises(ValueError):
            await store.read_projections(conversation_id, limit=-1)

    @pytest.mark.parametrize("bad_id", _BAD_IDS)
    @_asyncio
    async def test_invalid_conversation_ids_are_rejected_on_every_method(
        self, store: ConversationStore, bad_id: str
    ) -> None:
        with pytest.raises(ConversationIdInvalidError):
            await store.append_turn(bad_id, self._exchange("x"))
        with pytest.raises(ConversationIdInvalidError):
            await store.read_turns(bad_id)
        with pytest.raises(ConversationIdInvalidError):
            await store.last_turn_number(bad_id)
        with pytest.raises(ConversationIdInvalidError):
            await store.append_projections(
                bad_id, (ConversationProjection(turn=1, kind="log", text="x"),)
            )
        with pytest.raises(ConversationIdInvalidError):
            await store.read_projections(bad_id)

    # Projections ----------------------------------------------------------

    @_asyncio
    async def test_projections_start_empty(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        assert await store.read_projections(conversation_id) == ()

    @_asyncio
    async def test_append_projections_reads_back_in_turn_order(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        entries = (
            ConversationProjection(turn=2, kind="log", text="second"),
            ConversationProjection(turn=1, kind="log", text="first"),
        )
        await store.append_projections(conversation_id, entries)
        read = await store.read_projections(conversation_id)
        assert [e.turn for e in read] == [1, 2]
        assert [e.text for e in read] == ["first", "second"]

    @_asyncio
    async def test_append_projections_accumulates_across_batches(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_projections(
            conversation_id, (ConversationProjection(turn=1, kind="log", text="a"),)
        )
        await store.append_projections(
            conversation_id, (ConversationProjection(turn=2, kind="log", text="b"),)
        )
        assert len(await store.read_projections(conversation_id)) == 2

    @_asyncio
    async def test_append_projections_empty_batch_is_a_no_op(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_projections(conversation_id, ())
        assert await store.read_projections(conversation_id) == ()

    @_asyncio
    async def test_projections_preserve_kind_and_span(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        entry = ConversationProjection(turn=6, kind="epoch", text="fold", span=4)
        await store.append_projections(conversation_id, (entry,))
        (read,) = await store.read_projections(conversation_id)
        assert read == entry

    @_asyncio
    async def test_projections_after_and_limit(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        entries = tuple(
            ConversationProjection(turn=n, kind="log", text=str(n)) for n in range(1, 5)
        )
        await store.append_projections(conversation_id, entries)
        read = await store.read_projections(conversation_id, after=1, limit=2)
        assert [e.turn for e in read] == [2, 3]
        assert await store.read_projections(conversation_id, limit=0) == ()

    @_asyncio
    async def test_equal_turn_entries_sort_by_span_then_insertion(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_projections(
            conversation_id,
            (
                ConversationProjection(turn=3, kind="epoch", text="wide", span=3),
                ConversationProjection(turn=3, kind="log", text="narrow-1"),
                ConversationProjection(turn=3, kind="log", text="narrow-2"),
            ),
        )
        read = await store.read_projections(conversation_id)
        assert [e.text for e in read] == ["narrow-1", "narrow-2", "wide"]

    @_asyncio
    async def test_projections_are_isolated_per_conversation(
        self, store: ConversationStore
    ) -> None:
        await store.append_projections(
            "thread-a", (ConversationProjection(turn=1, kind="log", text="a"),)
        )
        assert await store.read_projections("thread-b") == ()

    @_asyncio
    async def test_projection_of_an_unwritten_turn_is_accepted(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        """CS7: the store never checks that a projected turn exists."""
        await store.append_projections(
            conversation_id, (ConversationProjection(turn=9, kind="log", text="x"),)
        )
        assert len(await store.read_projections(conversation_id)) == 1

    # Substrate planting ---------------------------------------------------

    @_asyncio
    async def test_newer_format_version_is_refused(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await self.plant_raw_turn(
            store,
            conversation_id,
            line='{"neosian_format":99,"turn":1,"created_at":'
            '"2026-01-01T00:00:00Z","messages":[{"role":"user","content":"x"}]}',
        )
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_turns(conversation_id)

    @_asyncio
    async def test_malformed_row_raises_and_never_skips(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await self.plant_raw_turn(store, conversation_id, line="not json")
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_turns(conversation_id)

    @_asyncio
    async def test_newer_projection_format_is_refused(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await self.plant_raw_projection(
            store,
            conversation_id,
            line='{"neosian_format":99,"turn":1,"span":1,"kind":"log","text":"x"}',
        )
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_projections(conversation_id)

    @_asyncio
    async def test_malformed_projection_row_raises_and_never_skips(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await self.plant_raw_projection(store, conversation_id, line="not json")
        with pytest.raises(ConversationFormatUnsupportedError):
            await store.read_projections(conversation_id)
