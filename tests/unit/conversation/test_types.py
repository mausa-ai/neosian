"""Value types for the conversation seam (DESIGN §9.3)."""

import dataclasses
from datetime import UTC, datetime

import pytest

from neosian._foundation.conversation.types import (
    CONVERSATION_FORMAT_VERSION,
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.llm.base import Message, Role


@pytest.mark.unit
class TestConversationTurn:
    def test_field_list_is_the_contract(self) -> None:
        names = [f.name for f in dataclasses.fields(ConversationTurn)]
        assert names == ["conversation_id", "turn", "messages", "created_at", "actor"]

    def test_frozen_and_slotted(self) -> None:
        turn = ConversationTurn(
            conversation_id="t",
            turn=1,
            messages=(Message(role=Role.USER, content="x"),),
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            turn.turn = 2  # type: ignore[misc]
        assert not hasattr(turn, "__dict__")


@pytest.mark.unit
class TestConversationProjection:
    def test_field_list_is_the_contract(self) -> None:
        names = [f.name for f in dataclasses.fields(ConversationProjection)]
        assert names == ["turn", "kind", "text", "span"]

    def test_span_defaults_to_one(self) -> None:
        entry = ConversationProjection(turn=3, kind="log", text="x")
        assert entry.span == 1

    def test_epoch_fold_spans_a_range(self) -> None:
        entry = ConversationProjection(turn=10, kind="epoch", text="x", span=10)
        assert (entry.turn, entry.span) == (10, 10)

    @pytest.mark.parametrize(
        ("turn", "span"),
        [(0, 1), (-1, 1), (3, 0), (3, -1), (3, 4)],
    )
    def test_out_of_range_turn_or_span_raises(self, turn: int, span: int) -> None:
        with pytest.raises(ValueError):
            ConversationProjection(turn=turn, kind="log", text="x", span=span)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError):
            ConversationProjection(turn=1, kind="summary", text="x")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        entry = ConversationProjection(turn=1, kind="log", text="x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.text = "y"  # type: ignore[misc]


@pytest.mark.unit
def test_format_version_is_one() -> None:
    assert CONVERSATION_FORMAT_VERSION == 1
