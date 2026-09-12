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
from collections.abc import AsyncIterator

import pytest

from neosian import PostgresStore
from tests.support.clock import ManualClock
from tests.support.postgres import plant_sql


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
