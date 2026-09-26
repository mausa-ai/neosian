"""The search slice of the conversation conformance kit (DESIGN §32).

Mixed into `ConversationStoreContract`; runnable on its own so a suite
can drive it a second time under a frozen clock, where the total order's
tiebreak is a real check rather than a predicate. Every whole-store test
uses a word no other test uses, because the container leg's store is
long-lived and freshens only its ids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.shared.exceptions import ConversationIdInvalidError
from neosian._foundation.shared.types import ToolCallId, ToolName

if TYPE_CHECKING:
    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.types import ConversationTurn

_asyncio = pytest.mark.asyncio


def _order(turns: tuple[ConversationTurn, ...]) -> list[tuple[str, int]]:
    return [(turn.conversation_id, turn.turn) for turn in turns]


def _expected(*appended: ConversationTurn) -> list[tuple[str, int]]:
    """The total order, computed from the records' own stamps."""
    ranked = sorted(
        appended,
        key=lambda turn: (turn.created_at, turn.conversation_id, turn.turn),
        reverse=True,
    )
    return _order(tuple(ranked))


class SearchContract:
    """`search_turns` on every substrate: fifteen tests."""

    @pytest.fixture
    def conversation_id(self) -> str:
        """The conversation under test; override to exercise another."""
        return "contract-kit"

    @staticmethod
    def _exchange(text: str) -> tuple[Message, ...]:
        return (
            Message(role=Role.USER, content=text),
            Message(role=Role.ASSISTANT, content=f"re: {text}"),
        )

    @_asyncio
    async def test_search_hits_message_text(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        appended = await store.append_turn(conversation_id, self._exchange("pelican"))
        (hit,) = await store.search_turns("pelican")
        assert (hit.conversation_id, hit.turn) == (conversation_id, appended.turn)
        assert hit.messages == appended.messages

    @_asyncio
    async def test_search_hits_a_tool_call_name_and_argument(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(
            conversation_id,
            (
                Message(role=Role.USER, content="find it"),
                Message(
                    role=Role.ASSISTANT,
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=ToolCallId("call-1"),
                            name=ToolName("lookup_sextant"),
                            arguments={"query": "astrolabe"},
                        )
                    ],
                ),
            ),
        )
        assert len(await store.search_turns("astrolabe")) == 1
        assert len(await store.search_turns("lookup_sextant")) == 1

    @_asyncio
    async def test_search_hits_a_tool_result(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(
            conversation_id,
            (
                Message(role=Role.USER, content="run"),
                Message(
                    role=Role.ASSISTANT,
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=ToolCallId("call-1"),
                            name=ToolName("fetch"),
                            arguments={},
                        )
                    ],
                ),
                Message(
                    role=Role.TOOL,
                    content="mackerel found",
                    tool_call_id=ToolCallId("call-1"),
                ),
            ),
        )
        assert len(await store.search_turns("mackerel")) == 1

    @_asyncio
    async def test_search_requires_every_term(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(conversation_id, self._exchange("quince"))
        both = await store.append_turn(conversation_id, self._exchange("quince yarrow"))
        assert _order(await store.search_turns("quince yarrow")) == [
            (conversation_id, both.turn)
        ]
        assert _order(await store.search_turns("yarrow quince")) == [
            (conversation_id, both.turn)
        ]
        assert len(await store.search_turns("quince")) == 2

    @_asyncio
    async def test_search_is_ascii_case_insensitive(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(conversation_id, self._exchange("Kestrel"))
        for spelling in ("kestrel", "KESTREL", "kEsTrEl"):
            assert len(await store.search_turns(spelling)) == 1, spelling

    @_asyncio
    async def test_search_matches_inside_a_word_and_has_no_wildcards(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(conversation_id, self._exchange("fjords 100% done_now"))
        for term in ("jord", "100%", "done_now"):
            assert len(await store.search_turns(term)) == 1, term
        assert await store.search_turns("100_") == ()

    @_asyncio
    async def test_search_never_matches_a_role_label(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(conversation_id, self._exchange("hello"))
        for label in ("user", "assistant", "tool"):
            found = await store.search_turns(label, conversations=[conversation_id])
            assert found == (), label

    @_asyncio
    async def test_search_across_the_store_is_newest_first(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        other = f"{conversation_id}-b"
        first = await store.append_turn(conversation_id, self._exchange("wombat one"))
        second = await store.append_turn(other, self._exchange("wombat two"))
        third = await store.append_turn(conversation_id, self._exchange("wombat three"))
        found = await store.search_turns("wombat")
        assert _order(found) == _expected(first, second, third)
        stamps = [turn.created_at for turn in found]
        assert stamps == sorted(stamps, reverse=True)

    @_asyncio
    async def test_search_narrows_to_the_named_conversations(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        other = f"{conversation_id}-b"
        await store.append_turn(conversation_id, self._exchange("gannet"))
        await store.append_turn(other, self._exchange("gannet"))
        mine = await store.search_turns("gannet", conversations=[conversation_id])
        assert {turn.conversation_id for turn in mine} == {conversation_id}
        theirs = await store.search_turns("gannet", conversations=[other])
        assert {turn.conversation_id for turn in theirs} == {other}
        absent = await store.search_turns(
            "gannet", conversations=[f"{conversation_id}-absent"]
        )
        assert absent == ()

    @_asyncio
    async def test_search_with_an_empty_sequence_is_empty(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        await store.append_turn(conversation_id, self._exchange("heron"))
        assert await store.search_turns("heron", conversations=()) == ()
        assert await store.search_turns("heron", conversations=[]) == ()

    @_asyncio
    async def test_search_limit_takes_the_newest(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        for n in range(4):
            await store.append_turn(conversation_id, self._exchange(f"ibis {n}"))
        assert _order(await store.search_turns("ibis", limit=2)) == [
            (conversation_id, 4),
            (conversation_id, 3),
        ]
        assert _order(await store.search_turns("ibis", limit=1)) == [
            (conversation_id, 4)
        ]
        assert len(await store.search_turns("ibis", limit=99)) == 4

    @_asyncio
    async def test_search_refuses_a_bad_limit_or_a_blank_query(
        self, store: ConversationStore
    ) -> None:
        for limit in (0, -1):
            with pytest.raises(ValueError):
                await store.search_turns("x", limit=limit)
        for query in ("", "   \t"):
            with pytest.raises(ValueError):
                await store.search_turns(query)

    @_asyncio
    async def test_search_with_an_absent_term_is_empty(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        assert await store.search_turns("zzz-never") == ()
        await store.append_turn(conversation_id, self._exchange("jacana"))
        assert await store.search_turns("jacana zebra") == ()

    @_asyncio
    async def test_search_rejects_an_invalid_conversation_id(
        self, store: ConversationStore
    ) -> None:
        with pytest.raises(ConversationIdInvalidError):
            await store.search_turns("x", conversations=["a/b"])

    @_asyncio
    async def test_search_follows_the_total_order(
        self, store: ConversationStore, conversation_id: str
    ) -> None:
        other = f"{conversation_id}-b"
        appended = [
            await store.append_turn(conversation_id, self._exchange("kittiwake a1")),
            await store.append_turn(other, self._exchange("kittiwake b1")),
            await store.append_turn(conversation_id, self._exchange("kittiwake a2")),
            await store.append_turn(other, self._exchange("kittiwake b2")),
        ]
        # Under a frozen clock every stamp ties and this pins the id and
        # turn tiebreaks; under an advancing one it pins recency.
        assert _order(await store.search_turns("kittiwake")) == _expected(*appended)
