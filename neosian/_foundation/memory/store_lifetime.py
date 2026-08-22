"""Store construction and lifetime for the argv entry points.

The runtime counterpart to `settings.py`'s pure grammar: one async
context manager builds the store the resolved settings name and closes
it on exit, so every CLI verb — dispatch, maintain, the operator
verbs — shares one DSN-vs-root branch instead of inlining it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from neosian._foundation.memory.file import FileStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.settings import StoreSettings


@asynccontextmanager
async def open_store(settings: StoreSettings) -> AsyncIterator[MemoryStore]:
    """Build the store `settings` names; close it on exit.

    Callers finish their grammar tier before entering — `FileStore`'s
    constructor mkdirs, and exit 2 constructs nothing (§14.1).
    """
    if settings.dsn is not None:
        # Function-local: a module-level edge memory -> postgres would
        # close a package cycle (postgres implements this package's ABC).
        from neosian._foundation.postgres.store import PostgresStore

        store = PostgresStore(settings.dsn, schema=settings.schema)
        try:
            yield store
        finally:
            await store.aclose()
        return
    assert settings.root is not None  # resolve_store_settings guarantees one
    yield FileStore(settings.root)
