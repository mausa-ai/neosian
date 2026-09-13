"""The wire's pages (NQ2 slice C, DESIGN §18.2): no listing route answers
more than `PAGE` rows, and each names what to send back for the rest.

Memory listings page through `Pageable` and answer `next_cursor`; the
conversation reads already have a cursor in the ABC and answer
`next_after`. A caller asking for `limit ≤ PAGE` gets exactly the ABC's
answer and no continuation. A host store without `Pageable` answers
whole with `next_cursor: null` — the one unbounded response left, and
the host's own (ledger #246) — and a cursor sent to it is refused by name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.memory.pageable import Page, Pageable
from neosian._foundation.server.wire import optional_int, optional_str, require_str
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.types import (
        ConversationProjection,
        ConversationTurn,
    )

# The most rows one response carries. Not a limit on the answer: the
# caller follows the continuation, and `RemoteStore` does so invisibly.
PAGE: Final = 500


async def memory_page(
    store: object,
    payload: dict[str, Any],
    page: Callable[[Pageable, str | None, int], Awaitable[Page[Any]]],
    whole: Callable[[int | None], Awaitable[Iterable[Any]]],
    *,
    default_limit: int | None = None,
) -> Page[Any]:
    """One bounded page of a memory listing, or the whole of it from a
    store that cannot page. `whole` receives the caller's limit."""
    cursor = optional_str(payload, "cursor")
    limit = optional_int(payload, "limit")
    if limit is None:
        limit = default_limit
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    if not isinstance(store, Pageable):
        if cursor is not None:
            raise ConfigurationError(
                f"{type(store).__name__} does not implement Pageable — this "
                "backend answers its listings whole (DESIGN §18.2)"
            )
        return Page(tuple(await whole(limit))[:limit], None)
    if limit == 0:
        return Page(tuple(await whole(0))[:0], None)
    return await page(store, cursor, PAGE if limit is None else min(limit, PAGE))


async def turns_page(
    store: ConversationStore, payload: dict[str, Any]
) -> tuple[tuple[ConversationTurn, ...], int | None]:
    conversation_id = require_str(payload, "conversation_id")
    after = optional_int(payload, "after") or 0
    limit = optional_int(payload, "limit")
    size = PAGE if limit is None else min(limit, PAGE)
    turns = await store.read_turns(conversation_id, after=after, limit=size)
    if len(turns) < size or size == limit:
        return turns, None
    return turns, turns[-1].turn


async def projections_page(
    store: ConversationStore, payload: dict[str, Any]
) -> tuple[tuple[ConversationProjection, ...], int | None]:
    """As `turns_page`, but a cut page ends on a whole turn.

    `after` filters `turn > after` and several entries share one turn, so
    only entries below the page's last turn are answered — continuing from
    `boundary - 1` re-reads that turn complete. A full page holding a
    single turn cannot be split, so the page widens instead of advancing:
    exact, never nearly right.
    """
    conversation_id = require_str(payload, "conversation_id")
    after = optional_int(payload, "after") or 0
    limit = optional_int(payload, "limit")
    size = PAGE if limit is None else min(limit, PAGE)
    while True:
        entries = await store.read_projections(conversation_id, after=after, limit=size)
        if len(entries) < size or size == limit:
            return entries, None
        boundary = entries[-1].turn
        complete = tuple(entry for entry in entries if entry.turn < boundary)
        if complete:
            return complete, boundary - 1
        size = size * 2 if limit is None else min(size * 2, limit)
