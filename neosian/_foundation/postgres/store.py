"""PostgresStore — the relational reference implementation (N3).

The standalone counterpart of FileStore, implementing both storage seams
(§8 documents, §9 turns) over one lazily-opened autocommit pool
(ledger #34): every mutation is a single atomic statement, the store
never issues BEGIN/COMMIT/ROLLBACK, and `expected_version` is race-safe
across workers (`supports_optimistic_concurrency = True`).

Construction is pure validation — no I/O, no driver import; the pool
opens on first use and `aclose()` releases it (idempotent; a later call
reopens). Timestamps come from the injected `Clock`, never SQL `now()`
(ledger #35). Substrate limitation (ledger #38): Postgres `text`/`jsonb`
reject U+0000, so content containing a NUL raises a driver error where
FileStore round-trips it.

The schema is applied explicitly, never as a side effect:
`await store.apply_schema()` (idempotent), or hand the rendered DDL from
`store.schema_sql()` / `python -m neosian.schemas postgres` to your own
migration tooling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.postgres.memory_store import PostgresMemoryStore
from neosian._foundation.postgres.pool import PostgresPool
from neosian._foundation.postgres.schema import schema_sql, validate_schema_name
from neosian._foundation.postgres.statements import build_statements
from neosian._foundation.postgres.turn_store import PostgresTurnStore
from neosian._foundation.shared.clock import Clock, SystemClock

if TYPE_CHECKING:
    from types import TracebackType


class PostgresStore(PostgresMemoryStore, PostgresTurnStore):
    """Both storage seams over one Postgres schema (default ``neosian``).

    Usage::

        store = PostgresStore(dsn)
        await store.apply_schema()   # once, idempotent
        ...
        await store.aclose()         # or: async with PostgresStore(dsn) as store
    """

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "neosian",
        clock: Clock | None = None,
    ) -> None:
        self._schema = validate_schema_name(schema)
        self._pool = PostgresPool(dsn)
        self._sql = build_statements(self._schema)
        self._clock = clock if clock is not None else SystemClock()

    def schema_sql(self) -> str:
        """The shipped DDL rendered for this store's schema (pure)."""
        return schema_sql(self._schema)

    async def apply_schema(self) -> None:
        """Apply the idempotent DDL — the caller's explicit act (C1)."""
        await self._pool.execute_script(self.schema_sql())

    async def aclose(self) -> None:
        """Release the connection pool; idempotent."""
        await self._pool.close()

    async def __aenter__(self) -> PostgresStore:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
