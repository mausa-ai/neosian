"""The ConversationStore ABC — the turn-storage contract (DESIGN §9.2).

Seven constraints keep this host-implementable (CS1–CS7): async signatures
with zero I/O ownership (no __init__, no connect/commit/DDL; methods must
be safe inside a caller-owned transaction and must not assume durability
on return); read-your-writes within a task; store-assigned turn numbers —
per-conversation, monotonic, gapless, from 1, never reused; tz-aware UTC
`created_at` assigned by the store; verbatim messages (never summarized,
reordered, dropped, or re-keyed — encode with the public message codec);
a refused-when-newer format marker with unknown keys ignored; and no
policy — no message-shape validation, no trimming, no compaction.

Cross-implementation invariants (pinned by
`testing.ConversationStoreContract`):

- `read_turns` returns turns with number > `after`, ascending; `limit`
  takes the oldest N after the cursor; `limit=0` and unknown
  conversations yield `()`.
- `read_turns(after=n-1, limit=1)` is the recall lookup — there is no
  sixth method.
- `last_turn_number` returns the highest number ever assigned, 0 for an
  unknown conversation.
- Projections are append-only, returned ordered by (turn, span,
  insertion); the store never checks that a projected turn exists (CS7).
- Every method validates the conversation id
  (ConversationIdInvalidError); negative `after`/`limit` and an empty
  `messages` sequence are programmer errors (ValueError).

Deliberately absent: `list_conversations` (hosts list from their own
tables), delete/redact, per-turn usage/model/cost, capability ClassVars.
This seam is not yet in ECOSYSTEM — until the §12 amendment session-pair,
DESIGN §9 is the contract of record and the seam is not frozen for hosts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.conversation.types import (
        ConversationProjection,
        ConversationTurn,
    )
    from neosian._foundation.llm.base import Message


class ConversationStore(ABC):
    """Abstract async turn store over (conversation_id, turn) → messages."""

    @abstractmethod
    async def append_turn(
        self, conversation_id: str, messages: Sequence[Message]
    ) -> ConversationTurn:
        """Append one turn; the store assigns its number and created_at."""

    @abstractmethod
    async def read_turns(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationTurn, ...]:
        """Turns with number > `after`, ascending; `limit` takes the
        oldest N after the cursor."""

    @abstractmethod
    async def last_turn_number(self, conversation_id: str) -> int:
        """The highest turn number assigned, 0 for an unknown conversation."""

    @abstractmethod
    async def append_projections(
        self, conversation_id: str, entries: Sequence[ConversationProjection]
    ) -> None:
        """Checkpoint compaction log entries (§9.6); batch, append-only."""

    @abstractmethod
    async def read_projections(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationProjection, ...]:
        """Entries with turn > `after`, ordered by (turn, span, insertion)."""
