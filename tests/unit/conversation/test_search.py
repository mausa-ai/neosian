"""The one search rule (DESIGN §32): the query, the searchable text, the
match, the total order and the file scan's prefilter."""

from datetime import UTC, datetime

import pytest

from neosian._foundation.conversation.file_search import prefilterable
from neosian._foundation.conversation.search import (
    check_limit,
    match_terms,
    messages_text,
    newest_first,
    parse_conversations,
    parse_query,
    turn_text,
)
from neosian._foundation.conversation.types import ConversationTurn
from neosian._foundation.llm.base import Message, Role, TextBlock, ToolCall
from neosian._foundation.shared.exceptions import ConversationIdInvalidError
from neosian._foundation.shared.types import ToolCallId, ToolName

pytestmark = pytest.mark.unit


def _turn(
    conversation_id: str, number: int, text: str, at: datetime
) -> ConversationTurn:
    return ConversationTurn(
        conversation_id=conversation_id,
        turn=number,
        messages=(Message(role=Role.USER, content=text),),
        created_at=at,
    )


class TestTheQuery:
    def test_terms_are_lowercased_and_split_on_whitespace(self) -> None:
        assert parse_query("  Deploy\tTarget  eu-west-1\n") == (
            "deploy",
            "target",
            "eu-west-1",
        )

    @pytest.mark.parametrize("query", ["", "   ", "\t\n"])
    def test_a_blank_query_is_a_programmer_error(self, query: str) -> None:
        with pytest.raises(ValueError, match="at least one term"):
            parse_query(query)

    def test_conversations_are_validated_or_none(self) -> None:
        assert parse_conversations(None) is None
        assert parse_conversations(()) == ()
        assert parse_conversations(["a", "b-2"]) == ("a", "b-2")
        with pytest.raises(ConversationIdInvalidError):
            parse_conversations(["a", "x/y"])

    def test_limit_starts_at_one(self) -> None:
        check_limit(1)
        for limit in (0, -3):
            with pytest.raises(ValueError, match="limit must be >= 1"):
                check_limit(limit)


class TestTheSearchableText:
    def test_every_message_contributes_its_text_in_order(self) -> None:
        messages = (
            Message(role=Role.USER, content="first"),
            Message(role=Role.ASSISTANT, content=[TextBlock(text="second")]),
            Message(role=Role.TOOL, content="third", tool_call_id=ToolCallId("c")),
        )
        assert messages_text(messages) == "first\nsecond\nthird"

    def test_a_tool_call_renders_as_name_and_compact_arguments(self) -> None:
        call = ToolCall(
            id=ToolCallId("c1"),
            name=ToolName("lookup"),
            arguments={"query": "café", "depth": 2},
        )
        message = Message(role=Role.ASSISTANT, content=None, tool_calls=[call])
        # Compact and unescaped: a non-ASCII query matches an argument as
        # it matches message text.
        assert messages_text((message,)) == 'lookup {"query":"café","depth":2}'

    def test_no_role_labels_and_no_reasoning(self) -> None:
        message = Message(role=Role.ASSISTANT, content="answer", reasoning="private")
        text = messages_text((Message(role=Role.USER, content="ask"), message))
        assert text == "ask\nanswer"
        assert "USER" not in text and "private" not in text

    def test_empty_content_contributes_nothing(self) -> None:
        assert messages_text((Message(role=Role.ASSISTANT, content=None),)) == ""

    def test_turn_text_is_the_messages_rule(self) -> None:
        turn = _turn("c", 1, "hello", datetime(2026, 1, 1, tzinfo=UTC))
        assert turn_text(turn) == messages_text(turn.messages)


class TestTheMatch:
    def test_every_term_must_be_a_substring_case_folded(self) -> None:
        assert match_terms("The Deploy Target is eu-west-1", ("deploy", "eu-west"))
        assert not match_terms("The Deploy Target", ("deploy", "eu-west"))
        assert match_terms("anything", ())

    def test_the_total_order_is_newest_then_id_then_turn_descending(self) -> None:
        early = datetime(2026, 1, 1, tzinfo=UTC)
        late = datetime(2026, 1, 2, tzinfo=UTC)
        turns = (
            _turn("a", 1, "x", early),
            _turn("b", 1, "x", early),
            _turn("a", 2, "x", early),
            _turn("a", 1, "x", late),
        )
        ordered = newest_first(turns)
        assert [(t.conversation_id, t.turn, t.created_at) for t in ordered] == [
            ("a", 1, late),
            ("b", 1, early),
            ("a", 2, early),
            ("a", 1, early),
        ]


class TestThePrefilter:
    @pytest.mark.parametrize("term", ["deploy", "eu-west-1", "100%", "a/b", "it's"])
    def test_printable_ascii_without_quotes_or_backslashes_is_prefilterable(
        self, term: str
    ) -> None:
        assert prefilterable(term)

    @pytest.mark.parametrize("term", ['say"hi', "back\\slash", "café", "tab\x01"])
    def test_what_the_file_escapes_is_not(self, term: str) -> None:
        assert not prefilterable(term)
