"""Paged listings — the protocol beside the ABCs (NQ2 slice C, DESIGN §8).

The four memory listings return whole tuples, and three of them have no
start key: `since` is a lower bound over a newest-first order, not a
cursor. `Pageable` is the paged form of each, deliberately *beside* the
ABC (the `Portable` shape, ledger #163): the three reference stores
implement it, a host store pays nothing, and the state process pages its
wire through it (§18.2).

A cursor is an opaque string the store minted (ledger #244). Each store
encodes a keyset — the last row's position in the listing's own order —
so a write between two pages never repeats or skips a row already paged.
Cursors are not portable across substrates and are never parsed by a
caller; a malformed one, or one minted by another method, is a
`ValueError`.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from neosian._foundation.memory.types import (
        MemoryEntry,
        MemoryRedaction,
        MemoryVersion,
    )


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of a listing. `next_cursor` is None exactly when nothing
    follows; otherwise pass it back with the same arguments."""

    items: tuple[T, ...]
    next_cursor: str | None


@runtime_checkable
class Pageable(Protocol):
    """The four `MemoryStore` listings, a page at a time.

    Pages concatenated in order equal the ABC method's answer for the same
    arguments; a page holds at most `limit` items (`limit < 1` is a
    `ValueError`). A cursor is valid only with the arguments that produced
    it. Validation is the ABC method's: scope, then path.
    """

    async def list_documents_page(
        self, scope: str, *, prefix: str = "", cursor: str | None = None, limit: int
    ) -> Page[MemoryEntry]: ...

    async def versions_page(
        self, scope: str, path: str, *, cursor: str | None = None, limit: int
    ) -> Page[MemoryVersion]: ...

    async def history_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryVersion]: ...

    async def redactions_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryRedaction]: ...


def page_limit(limit: int) -> int:
    if limit < 1:
        raise ValueError(f"a page limit must be >= 1, got {limit}")
    return limit


def encode_cursor(method: str, *parts: str | int) -> str:
    """The store's keyset, tagged with the method that minted it."""
    raw = json.dumps([method, *parts], ensure_ascii=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii")


def decode_cursor(method: str, cursor: str, *kinds: type) -> tuple[Any, ...]:
    """The parts `encode_cursor` wrote, each checked against `kinds`."""
    try:
        data: Any = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (UnicodeError, binascii.Error, ValueError):
        data = None
    if (
        not isinstance(data, list)
        or data[:1] != [method]
        or len(data) != len(kinds) + 1
        or not all(
            isinstance(part, kind) and not isinstance(part, bool)
            for part, kind in zip(data[1:], kinds, strict=True)
        )
    ):
        raise ValueError(f"malformed {method} cursor: {cursor!r}")
    return tuple(data[1:])


def paged[T](items: list[T], limit: int, cursor_of: Callable[[T], str]) -> Page[T]:
    """Cut a page from `limit + 1` fetched rows: the extra row only says
    that something follows, and the cursor is the last row kept."""
    if len(items) <= limit:
        return Page(tuple(items), None)
    kept = items[:limit]
    return Page(tuple(kept), cursor_of(kept[-1]))


def instant(value: datetime) -> str:
    """A timestamp as a cursor part: ISO-8601 in UTC, microseconds kept."""
    return value.astimezone(UTC).isoformat()


def instant_part(method: str, cursor: str, stamp: str) -> datetime:
    """A cursor part back to the tz-aware timestamp it was written from."""
    try:
        value = datetime.fromisoformat(stamp)
    except ValueError:
        value = None
    if value is None or value.tzinfo is None:
        raise ValueError(f"malformed {method} cursor: {cursor!r}")
    return value


def history_cursor(row: MemoryVersion) -> str:
    return encode_cursor("history", instant(row.created_at), row.path, row.version)


def history_key(cursor: str) -> tuple[datetime, str, int]:
    """The ledger order's keyset (DESIGN §20), which FileStore and
    PostgresStore both sort by."""
    stamp, path, version = decode_cursor("history", cursor, str, str, int)
    return instant_part("history", cursor, stamp), path, version
