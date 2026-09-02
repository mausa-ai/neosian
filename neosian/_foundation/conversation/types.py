"""Value types for the conversation storage seam (DESIGN §9.3).

Frozen records crossing the `ConversationStore` boundary. Field lists are
part of the host-facing contract: every store implementation must populate
them, and the conformance kit asserts against them. Turn rows deliberately
carry no usage/model/cost — hooks are the metering seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, get_args

if TYPE_CHECKING:
    from datetime import datetime

    from neosian._foundation.llm.base import Message

# The storage format marker (`neosian_format`); stores refuse to read
# anything newer (CS6) — the escape hatch for evolving the schema without
# breaking existing stores.
CONVERSATION_FORMAT_VERSION: Final = 1

# What a projection entry is: a per-turn log line, a model-distilled
# digest, or an epoch fold (DESIGN §9.6; written in slice B).
ProjectionKind = Literal["log", "digest", "epoch"]

_PROJECTION_KINDS: Final = frozenset(get_args(ProjectionKind))


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """One append-only turn: the USER message that opened it plus every
    message the run produced, in provider order (§9.5 rulings 1–2)."""

    conversation_id: str
    turn: int
    messages: tuple[Message, ...]
    created_at: datetime
    # Who appended it (NL, DESIGN §20): opaque to the store, `None` for
    # rows written before the field existed. Additive — last, defaulted.
    actor: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationProjection:
    """One checkpointed compaction entry (§9.6).

    `turn` is the last turn the entry covers; `span` the count of
    consecutive turns ending there. No timestamp: a projection is derived
    data whose provenance is the turn range it names.
    """

    turn: int
    kind: ProjectionKind
    text: str
    span: int = 1

    def __post_init__(self) -> None:
        if self.turn < 1:
            raise ValueError(f"turn must be >= 1, got {self.turn}")
        if not 1 <= self.span <= self.turn:
            raise ValueError(
                f"span must satisfy 1 <= span <= turn, got span={self.span} "
                f"for turn={self.turn}"
            )
        if self.kind not in _PROJECTION_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(_PROJECTION_KINDS)}, "
                f"got {self.kind!r}"
            )
