"""The conversation-id grammar (DESIGN §9.4)."""

import pytest

from neosian._foundation.conversation.ids import (
    CONVERSATION_ID_MAX_LENGTH,
    CONVERSATION_ID_PATTERN,
    parse_conversation_id,
)
from neosian._foundation.shared.exceptions import ConversationIdInvalidError

_GOOD = (
    "a",
    "thread-829",
    "user_1.session.2",
    "UPPER-and-lower",
    "...",
    "x" * 128,
)

_BAD = (
    "",
    ".",
    "..",
    "a/b",
    "user:1",
    "a b",
    "a\n",
    "thread-829\n",
    "é",
    "x" * 129,
)


@pytest.mark.unit
class TestParseConversationId:
    @pytest.mark.parametrize("value", _GOOD)
    def test_accepts_grammatical_ids(self, value: str) -> None:
        assert parse_conversation_id(value) == value

    @pytest.mark.parametrize("value", _BAD)
    def test_rejects_ungrammatical_ids(self, value: str) -> None:
        with pytest.raises(ConversationIdInvalidError) as excinfo:
            parse_conversation_id(value)
        assert excinfo.value.code == "agent_conversation_id_invalid"
        details = excinfo.value.details
        assert details is not None
        assert details["conversation_id"] == value
        assert details["reason"]

    def test_never_normalizes(self) -> None:
        assert parse_conversation_id("MiXeD.Case") == "MiXeD.Case"

    def test_max_length_is_exported(self) -> None:
        assert CONVERSATION_ID_MAX_LENGTH == 128

    def test_pattern_is_anchored_against_trailing_newline(self) -> None:
        assert CONVERSATION_ID_PATTERN.match("ok")
        assert CONVERSATION_ID_PATTERN.match("ok\n") is None
