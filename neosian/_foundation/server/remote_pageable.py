"""`RemoteStore`'s side of paged listings (NQ2 slice C, §18.2).

Every listing route answers at most a page and names its continuation —
`next_cursor` for the memory listings, `next_after` for the two
conversation reads. The ABC methods follow it invisibly (`_follow`), so a
caller still gets the one tuple FileStore returns while neither end holds
more than a page per request. The four `Pageable` methods are one POST
each, honest only over a backend that pages: the handshake transmits
whether it does, and a page asked of one that does not is refused by
name (#109's rule — capability is transmitted, never claimed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neosian._foundation.memory.journal import since_window
from neosian._foundation.memory.pageable import Page, page_limit
from neosian._foundation.server.wire import (
    decode_entry,
    decode_redaction,
    decode_version,
    encode_timestamp,
    optional_str,
)
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from neosian._foundation.memory.types import (
        MemoryEntry,
        MemoryRedaction,
        MemoryVersion,
    )


class RemotePageable:
    """Mixin; the host class owns `_call` and `_pageable` (`remote.py`)."""

    _pageable: bool

    if TYPE_CHECKING:

        async def _call(
            self, endpoint: str, payload: dict[str, Any]
        ) -> dict[str, Any]: ...

    async def _follow(
        self, endpoint: str, payload: dict[str, Any], items: str, *, after: bool
    ) -> list[Any]:
        """Every row the request asks for, a page per request. `after`
        names the conversation reads' continuation, else the cursor's."""
        send, answer = ("after", "next_after") if after else ("cursor", "next_cursor")
        limit = payload.get("limit")
        rows: list[Any] = []
        while True:
            data = await self._call(endpoint, payload)
            rows.extend(data[items])
            following = data[answer]
            if following is None or not data[items]:
                return rows
            remaining = None if limit is None else limit - len(rows)
            if remaining == 0:
                return rows
            payload = {**payload, send: following, "limit": remaining}

    async def _page(
        self,
        endpoint: str,
        payload: dict[str, Any],
        items: str,
        decode: Callable[[Any], Any],
    ) -> Page[Any]:
        page_limit(payload["limit"])
        if not self._pageable:
            raise ConfigurationError(
                "the server's backend does not implement Pageable — its "
                "listings answer whole (DESIGN §18.2)"
            )
        data = await self._call(endpoint, payload)
        return Page(
            tuple(decode(item) for item in data[items]),
            optional_str(data, "next_cursor"),
        )

    async def list_documents_page(
        self, scope: str, *, prefix: str = "", cursor: str | None = None, limit: int
    ) -> Page[MemoryEntry]:
        return await self._page(
            "memory/list_documents",
            {"scope": scope, "prefix": prefix, "cursor": cursor, "limit": limit},
            "entries",
            decode_entry,
        )

    async def versions_page(
        self, scope: str, path: str, *, cursor: str | None = None, limit: int
    ) -> Page[MemoryVersion]:
        return await self._page(
            "memory/versions",
            {"scope": scope, "path": path, "cursor": cursor, "limit": limit},
            "versions",
            decode_version,
        )

    async def history_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryVersion]:
        return await self._page(
            "memory/history",
            ledger_payload(scope, since, cursor, limit),
            "versions",
            decode_version,
        )

    async def redactions_page(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> Page[MemoryRedaction]:
        return await self._page(
            "memory/redactions",
            ledger_payload(scope, since, cursor, limit),
            "redactions",
            decode_redaction,
        )


def ledger_payload(
    scope: str, since: datetime | None, cursor: str | None, limit: int | None
) -> dict[str, Any]:
    since_window(since, None)  # a naive `since` never crosses (C4)
    return {
        "scope": scope,
        "since": None if since is None else encode_timestamp(since),
        "cursor": cursor,
        "limit": limit,
    }
