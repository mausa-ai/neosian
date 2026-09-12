"""PostgresStore substrate hooks: they reach the tables directly through
the store's own pool and schema — deliberately inside, the FileStore
contract-test idiom."""

from collections.abc import Mapping
from typing import Any

from neosian import PostgresStore


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
