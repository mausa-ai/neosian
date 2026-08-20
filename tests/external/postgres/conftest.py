"""Fixtures for the PostgresStore suite (a real server, not an API key).

Every test self-skips per test unless NEOSIAN_TEST_POSTGRES_DSN is set —
falsiness, never module-level (DESIGN §10). Locally:

    docker run --rm -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:17
    export NEOSIAN_TEST_POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/postgres
    make test-postgres

Isolation: each test gets its own uniquely-named schema, applied from the
shipped DDL and dropped with CASCADE at teardown — nothing leaks between
tests or runs. The store fixture takes a ManualClock: the conformance
kits' timestamp assertions prove the Clock is genuinely injected.
"""

import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from neosian import PostgresStore
from tests.unit.memory.conftest import ManualClock

__all__ = ["ManualClock"]


@pytest.fixture
def manual_clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
async def store(
    postgres_dsn: str, manual_clock: ManualClock
) -> AsyncIterator[PostgresStore]:
    schema = f"neosian_test_{uuid.uuid4().hex[:12]}"
    store = PostgresStore(postgres_dsn, schema=schema, clock=manual_clock)
    await store.apply_schema()
    try:
        yield store
    finally:
        await plant_sql(store, f'DROP SCHEMA "{schema}" CASCADE', {})
        await store.aclose()


# Substrate hooks reach the tables directly through the store's own pool
# and schema — deliberately inside, the FileStore contract-test idiom.


def store_schema(store: PostgresStore) -> str:
    return store._schema  # noqa: SLF001 — substrate hook, deliberately inside


async def plant_sql(
    store: PostgresStore, query: str, params: Mapping[str, Any]
) -> None:
    await store._pool.execute(query, params)  # noqa: SLF001 — substrate hook


async def fetch_sql(
    store: PostgresStore, query: str, params: Mapping[str, Any]
) -> list[tuple[Any, ...]]:
    return await store._pool.fetch(query, params)  # noqa: SLF001 — substrate hook
