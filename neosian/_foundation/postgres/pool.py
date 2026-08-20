"""The store-owned connection pool (ledger #34).

Lazily opened on first use, always `autocommit=True`: every statement is
its own implicit transaction, so the store never issues BEGIN/COMMIT/
ROLLBACK — each mutation in `statements.py` is one atomic data-modifying-
CTE statement. Contended statements (version numbers, turn numbers) retry
on unique-violation/deadlock with a fresh snapshot; on exhaustion the
driver error propagates (ledger #39).
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

from neosian._foundation.postgres.driver import load_driver

if TYPE_CHECKING:
    from collections.abc import Mapping

    from psycopg_pool import AsyncConnectionPool

# Each lost race means another writer committed, so the system always
# progresses; a single caller's losses are bounded by the writers in
# flight (pool-sized). 25 attempts with growing jitter outlasts any
# realistic pool before the driver error propagates (ledger #39).
_MAX_ATTEMPTS = 25
_BACKOFF_SECONDS = 0.01

Row = tuple[Any, ...]


class PostgresPool:
    """One lazily-opened autocommit pool, owned by a PostgresStore."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: AsyncConnectionPool | None = None
        self._open_lock = asyncio.Lock()

    async def _ensure_pool(self) -> AsyncConnectionPool:
        async with self._open_lock:
            if self._pool is None:
                pool = load_driver().pool_class(
                    self._dsn, open=False, kwargs={"autocommit": True}
                )
                await pool.open()
                self._pool = pool
            return self._pool

    async def fetch(self, query: str, params: Mapping[str, Any]) -> list[Row]:
        pool = await self._ensure_pool()
        async with pool.connection() as connection:
            cursor = await connection.execute(query, params)
            return await cursor.fetchall()

    async def execute(self, query: str, params: Mapping[str, Any]) -> None:
        """Run a statement that returns no rows."""
        pool = await self._ensure_pool()
        async with pool.connection() as connection:
            await connection.execute(query, params)

    async def fetch_one(self, query: str, params: Mapping[str, Any]) -> Row:
        rows = await self.fetch(query, params)
        if len(rows) != 1:
            raise RuntimeError(f"expected exactly one diagnostics row, got {len(rows)}")
        return rows[0]

    async def fetch_one_retry(self, query: str, params: Mapping[str, Any]) -> Row:
        """`fetch_one` retried on version/turn-number races (CS3)."""
        retryable = load_driver().retryable
        for attempt in range(_MAX_ATTEMPTS - 1):
            try:
                return await self.fetch_one(query, params)
            except retryable:
                await asyncio.sleep(random.uniform(0, _BACKOFF_SECONDS * (attempt + 1)))
        return await self.fetch_one(query, params)

    async def execute_script(self, script: str) -> None:
        """Run a multi-statement, parameter-free script (the DDL asset)."""
        pool = await self._ensure_pool()
        async with pool.connection() as connection:
            await connection.execute(script)

    async def close(self) -> None:
        """Release the pool; idempotent. A later call opens a fresh one."""
        async with self._open_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()
