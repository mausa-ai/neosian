"""The `Pageable` suite every paging store runs (NQ2 slice C, DESIGN §8).

Subclass `PagingSuite` and provide a `store` fixture. The seeded scope has
edits, a delete and re-create, a rename (two history rows at one instant)
and three redactions; subclasses run it again under `FrozenClock`, where
every row ties on `created_at` and only the tiebreaks order the pages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from neosian._foundation.memory.pageable import Page, Pageable
from neosian._foundation.shared.exceptions import MemoryScopeInvalidError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from neosian._foundation.memory.base import MemoryStore

    PageMethod = Callable[..., Awaitable[Page[Any]]]

SCOPE = "user:pages"
_LIMITS = (1, 2, 3, 1000)


class FrozenClock:
    """Every read answers the same instant: the worst case for a keyset."""

    def now(self) -> datetime:
        return datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


async def seed(store: MemoryStore) -> None:
    for n in range(6):
        await store.write(SCOPE, f"notes/{n}", f"note {n}", actor=f"w{n}")
    for edit in ("second", "third"):
        await store.write(SCOPE, "notes/1", edit)
    await store.write(SCOPE, "a", "top")
    await store.write(SCOPE, "z", "first life")
    await store.delete(SCOPE, "z")
    await store.write(SCOPE, "z", "second life")
    await store.rename(SCOPE, "notes/5", "moved")
    await store.redact(SCOPE, path="notes/2", actor="ops")
    await store.redact(SCOPE, actor="ops")
    await store.write(SCOPE, "notes/3", "after the erasure")
    await store.redact(SCOPE, path="a")


async def walk(
    method: PageMethod,
    limit: int,
    *args: str,
    cursor: str | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Every page from `cursor` on, asserting the shape of each: only the
    last page may be short, and only the last carries no cursor."""
    items: list[Any] = []
    while True:
        page = await method(*args, cursor=cursor, limit=limit, **kwargs)
        assert len(page.items) <= limit
        items.extend(page.items)
        if page.next_cursor is None:
            return items
        assert len(page.items) == limit
        cursor = page.next_cursor


async def continued(first: Page[Any], method: PageMethod, *args: str) -> list[Any]:
    rest = await walk(method, len(first.items), *args, cursor=first.next_cursor)
    return [*first.items, *rest]


class PagingSuite:
    @pytest.fixture
    async def seeded(self, store: MemoryStore) -> Any:
        assert isinstance(store, Pageable)
        await seed(store)
        return store

    @pytest.mark.parametrize("limit", _LIMITS)
    async def test_pages_concatenate_to_every_listing(
        self, seeded: Any, limit: int
    ) -> None:
        for prefix in ("", "notes/", "nothing/"):
            assert await walk(
                seeded.list_documents_page, limit, SCOPE, prefix=prefix
            ) == list(await seeded.list_documents(SCOPE, prefix=prefix))
        history = await seeded.history(SCOPE)
        assert len(history) == 15
        for since in (None, history[len(history) // 2].created_at):
            assert await walk(seeded.history_page, limit, SCOPE, since=since) == list(
                await seeded.history(SCOPE, since=since)
            )
            assert await walk(
                seeded.redactions_page, limit, SCOPE, since=since
            ) == list(await seeded.redactions(SCOPE, since=since))
        for path in {row.path for row in history} | {"never"}:
            assert await walk(seeded.versions_page, limit, SCOPE, path) == list(
                await seeded.versions(SCOPE, path, limit=1000)
            )

    async def test_a_page_as_long_as_the_listing_is_the_last(self, seeded: Any) -> None:
        history = await seeded.history(SCOPE)
        whole = await seeded.history_page(SCOPE, limit=len(history))
        assert whole == Page(history, None)
        short = await seeded.history_page(SCOPE, limit=len(history) - 1)
        assert short.next_cursor is not None
        rest = await seeded.history_page(SCOPE, cursor=short.next_cursor, limit=5)
        assert rest == Page(history[-1:], None)
        assert await seeded.redactions_page("user:empty", limit=3) == Page((), None)

    async def test_a_write_between_pages_repeats_and_skips_nothing(
        self, seeded: Any
    ) -> None:
        listing = [entry.path for entry in await seeded.list_documents(SCOPE)]
        history = list(await seeded.history(SCOPE))
        trail = list(await seeded.redactions(SCOPE))
        versions = list(await seeded.versions(SCOPE, "notes/1"))
        documents = await seeded.list_documents_page(SCOPE, limit=2)
        rows = await seeded.history_page(SCOPE, limit=4)
        acts = await seeded.redactions_page(SCOPE, limit=1)
        numbers = await seeded.versions_page(SCOPE, "notes/1", limit=1)
        # Every write lands before its listing's cursor — a path that sorts
        # first, newer rows, a newer erasure, a newer version — and touches
        # no row already paged (an edit changes an entry's fields, so the
        # listing is compared by path).
        await seeded.write(SCOPE, "0-first", "late")
        await seeded.write(SCOPE, "notes/1", "fourth")
        await seeded.redact(SCOPE, path="0-first")
        paged = await continued(documents, seeded.list_documents_page, SCOPE)
        assert [entry.path for entry in paged] == listing
        assert await continued(rows, seeded.history_page, SCOPE) == history
        assert await continued(acts, seeded.redactions_page, SCOPE) == trail
        assert (
            await continued(numbers, seeded.versions_page, SCOPE, "notes/1") == versions
        )

    async def test_bad_cursors_and_limits_are_programmer_errors(
        self, seeded: Any
    ) -> None:
        minted = (await seeded.history_page(SCOPE, limit=1)).next_cursor
        calls: tuple[tuple[PageMethod, tuple[str, ...]], ...] = (
            (seeded.list_documents_page, (SCOPE,)),
            (seeded.versions_page, (SCOPE, "notes/1")),
            (seeded.history_page, (SCOPE,)),
            (seeded.redactions_page, (SCOPE,)),
        )
        for method, args in calls:
            for cursor in ("not a cursor", "W10=", "WyJ4Il0="):
                with pytest.raises(ValueError):
                    await method(*args, cursor=cursor, limit=2)
            with pytest.raises(ValueError):
                await method(*args, limit=0)
            if method != seeded.history_page:
                with pytest.raises(ValueError):  # minted by another method
                    await method(*args, cursor=minted, limit=2)
        with pytest.raises(MemoryScopeInvalidError):
            await seeded.history_page("not a scope", limit=1)
        with pytest.raises(ValueError):
            await seeded.history_page(SCOPE, since=datetime(2026, 1, 1), limit=1)
