"""The eraser beside the ABC (N8, DESIGN §38).

`ConversationStore` keeps every turn verbatim (CS5) and compaction pages
rather than deletes (§9.6); `Erasable` is the one way content leaves a
conversation, deliberately *beside* the ABC like `Portable` and
`Pageable` (ledger #342): the three reference stores implement it, a
host store pays nothing, and the state process transmits `erasable` in
its handshake rather than claiming it (#109). It is an operator's act,
never a tool command (#104): the model never erases its own past.

The rule every substrate applies lives here and nowhere else. A
selection is every turn, the turns through `through`, or the `turns`
named, never both; a redacted turn keeps its number, `created_at` and
`actor`, carries no messages and `redacted=True`; every projection entry
covering a redacted turn loses its text; the count answered is the
turns matched, already-redacted ones included, so the act is idempotent;
the trail is bounded and newest first under one total order:
`created_at`, then `conversation_id` in codepoint order, then the act's
position in its conversation's trail, each descending.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from neosian._foundation.conversation.types import (
        ConversationProjection,
        ConversationRedaction,
    )


@runtime_checkable
class Erasable(Protocol):
    """Redact recorded turns, skeleton kept, and read the trail."""

    async def redact_turns(
        self,
        conversation_id: str,
        *,
        through: int | None = None,
        turns: Sequence[int] | None = None,
        actor: str | None = None,
    ) -> int:
        """Blank the selected turns of one conversation and every
        projection entry covering them; answer the count matched. Both
        selectors `None` is every turn; `through=N` is turns 1 to N;
        `turns` names numbers; giving both, or a number below 1, is a
        `ValueError`. An unknown conversation or number matches nothing."""
        ...

    async def turn_redactions(
        self,
        *,
        conversations: Sequence[str] | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> tuple[ConversationRedaction, ...]:
        """The erasure trail, newest first, at most `limit`;
        `conversations=None` is the whole store, a sequence narrows, `()`
        answers `()`; `since` is inclusive and tz-aware."""
        ...


def parse_selection(
    through: int | None, turns: Sequence[int] | None
) -> tuple[int | None, tuple[int, ...] | None]:
    """The selectors validated: at most one, every number from 1; the
    named turns deduplicated and ascending."""
    if through is not None and turns is not None:
        raise ValueError("give through= or turns=, not both")
    if through is not None and through < 1:
        raise ValueError(f"through must be >= 1, got {through}")
    if turns is None:
        return through, None
    named = tuple(sorted(set(turns)))
    if named and named[0] < 1:
        raise ValueError(f"turn numbers start at 1, got {named[0]}")
    return None, named


def selected(number: int, through: int | None, turns: tuple[int, ...] | None) -> bool:
    if turns is not None:
        return number in turns
    return through is None or number <= through


def covers(entry: ConversationProjection, numbers: Iterable[int]) -> bool:
    """Whether the entry's range holds any of the numbers."""
    first = entry.turn - entry.span + 1
    return any(first <= number <= entry.turn for number in numbers)


def check_trail_window(since: datetime | None, limit: int) -> None:
    if since is not None and since.tzinfo is None:
        raise ValueError("since must be timezone-aware (UTC)")
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")


def newest_first(
    acts: Iterable[tuple[ConversationRedaction, int]], since: datetime | None
) -> tuple[ConversationRedaction, ...]:
    """The total order over `(act, position)` pairs, `since` applied."""
    kept = [(act, at) for act, at in acts if since is None or act.created_at >= since]
    kept.sort(
        key=lambda item: (item[0].created_at, item[0].conversation_id, item[1]),
        reverse=True,
    )
    return tuple(act for act, _ in kept)
