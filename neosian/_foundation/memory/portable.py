"""Store mobility — the archive types and the privileged path (DESIGN §26).

The two ABCs assign version numbers, turn numbers and timestamps
themselves, and neither enumerates its units; moving a store *whole*
needs both. `Portable` is that surface, deliberately beside the ABCs
(the `apply_schema()` shape, ledger #163): the three reference stores
implement it, a host store pays nothing, and a store without it is
refused by name. Export needs no privilege — `transfer.py` composes it
from the reads every store already has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from neosian._foundation.conversation.types import (
        ConversationProjection,
        ConversationTurn,
    )
    from neosian._foundation.memory.types import (
        MemoryDocument,
        MemoryRedaction,
        MemoryVersion,
    )

UnitKind = Literal["scope", "conversation"]


@dataclass(frozen=True, slots=True)
class ScopeArchive:
    """One scope, whole. The live documents ride explicitly — `extra`,
    the `created_at` a re-create reset, and the live `redacted` flag are
    not derivable from the rows. Orders are the substrate's write orders:
    documents by path, versions by (path, version), the trail oldest
    first."""

    scope: str
    documents: tuple[MemoryDocument, ...]
    versions: tuple[MemoryVersion, ...]
    redactions: tuple[MemoryRedaction, ...]


@dataclass(frozen=True, slots=True)
class ConversationArchive:
    """One conversation, whole: turns ascending and gapless from 1,
    projections in read order (turn, span, insertion)."""

    conversation_id: str
    turns: tuple[ConversationTurn, ...]
    projections: tuple[ConversationProjection, ...]


@dataclass(frozen=True, slots=True)
class UnitReport:
    """One unit moved — one row type, the fields its kind fills."""

    kind: UnitKind
    name: str
    documents: int = 0
    versions: int = 0
    redactions: int = 0
    turns: int = 0
    projections: int = 0


@dataclass(frozen=True, slots=True)
class TransferReport:
    """Every unit moved, scopes first then conversations, each sorted."""

    units: tuple[UnitReport, ...]


@runtime_checkable
class Portable(Protocol):
    """The privileged write path beside the ABCs (§26.1).

    Restores are verbatim: numbers, timestamps and actors are the
    archive's, never the store's. The unit must be empty in the target —
    `memory_conflict` / `agent_conversation_conflict` with reason
    `target_occupied` otherwise — and lands whole where the substrate
    can (one statement on Postgres; sidecars before documents on files).
    The archive is trusted as a conforming store's output: a substrate
    re-validates only what its parsers already check. An empty archive
    restores nothing and creates nothing.
    """

    async def scopes(self) -> tuple[str, ...]:
        """Every scope holding documents, history or redactions, sorted."""
        ...

    async def conversations(self) -> tuple[str, ...]:
        """Every conversation holding turns or projections, sorted."""
        ...

    async def restore_scope(self, archive: ScopeArchive) -> None: ...

    async def restore_conversation(self, archive: ConversationArchive) -> None: ...
