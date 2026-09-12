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

Deliberately absent: `list_conversations`, delete/redact, per-turn
usage/model/cost, capability ClassVars. Frozen for hosts since the
2026-08-21 amendment (ECOSYSTEM §10); DESIGN §9 carries the rationale and
the CS1-CS7 rulings. NL added one thing, additively:
`append_turn(..., actor=)` and `ConversationTurn.actor` — who appended a
turn, opaque to the store (DESIGN §20).

**Reserved for a 1.x minor** (NQ2, ledger #229) — neosian will not claim
this name for anything else, so a host may implement it early:

    async def list_conversations(
        self, *, prefix: str | None = None, limit: int | None = None
    ) -> tuple[str, ...]: ...
        # Conversation ids ascending; `prefix` a plain string prefix.

It stays off the ABC for 1.0 because the need arose twice and was met
twice without it: NL's sessions listing is memory documents under the
scope (#123), and NC4's export enumerates through the `Portable`
privilege, which the three shipped stores pay for and hosts do not.
Arrival is an ECOSYSTEM §12 session-pair, never a quiet method on a
reference store.
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
        self,
        conversation_id: str,
        messages: Sequence[Message],
        *,
        actor: str | None = None,
    ) -> ConversationTurn:
        """Append one turn; the store assigns its number and created_at.

        `actor` is who appended it — recorded verbatim, never interpreted
        (DESIGN §20); an additive keyword since NL.
        """

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
