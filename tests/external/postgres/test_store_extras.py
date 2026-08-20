"""Substrate behaviors beyond the shared contracts: idempotent DDL, the
`extra` round-trip, the dormant FTS hatch, format refusal on planted
rows, pool lifecycle, and the documented NUL limitation (ledger #38)."""

from datetime import UTC, datetime

import psycopg
import pytest

from neosian import PostgresStore
from neosian._foundation.shared.exceptions import (
    ConversationFormatUnsupportedError,
    MemoryFormatUnsupportedError,
)
from tests.external.postgres.conftest import fetch_sql, plant_sql, store_schema

_SCOPE = "user:extras"
_TS = datetime(2026, 1, 1, tzinfo=UTC)


async def test_apply_schema_twice_is_a_no_op(store: PostgresStore) -> None:
    await store.write(_SCOPE, "doc", "kept")
    await store.apply_schema()
    document = await store.read(_SCOPE, "doc")
    assert document is not None
    assert document.content == "kept"


async def test_extra_survives_writes_and_rename(store: PostgresStore) -> None:
    schema = store_schema(store)
    await plant_sql(
        store,
        f'INSERT INTO "{schema}".memories '
        "(scope, path, content, version, created_at, updated_at, "
        "neosian_format, extra) "
        "VALUES (%(scope)s, 'doc', 'planted', 1, %(ts)s, %(ts)s, 1, "
        '\'{"custom": "kept"}\'::jsonb)',
        {"scope": _SCOPE, "ts": _TS},
    )
    await store.write(_SCOPE, "doc", "updated")
    moved = await store.rename(_SCOPE, "doc", "moved")
    assert moved.extra["custom"] == "kept"
    document = await store.read(_SCOPE, "moved")
    assert document is not None
    assert document.extra["custom"] == "kept"


async def test_the_fts_hatch_is_populated_but_has_no_api(
    store: PostgresStore,
) -> None:
    """Dormant by design: reachable through raw SQL only."""
    await store.write(_SCOPE, "notes/pelican", "the pelican ate a mackerel")
    await store.write(_SCOPE, "notes/other", "nothing relevant here")
    schema = store_schema(store)
    rows = await fetch_sql(
        store,
        f'SELECT path FROM "{schema}".memories '
        "WHERE scope = %(scope)s "
        "AND search @@ plainto_tsquery('simple', %(query)s)",
        {"scope": _SCOPE, "query": "mackerel"},
    )
    assert [row[0] for row in rows] == ["notes/pelican"]


async def test_newer_format_version_row_is_refused(store: PostgresStore) -> None:
    schema = store_schema(store)
    await plant_sql(
        store,
        f'INSERT INTO "{schema}".memory_versions '
        "(scope, path, version, action, content, created_at, neosian_format) "
        "VALUES (%(scope)s, 'doc', 1, 'created', 'future', %(ts)s, 99)",
        {"scope": _SCOPE, "ts": _TS},
    )
    with pytest.raises(MemoryFormatUnsupportedError):
        await store.versions(_SCOPE, "doc")


async def test_non_array_messages_row_is_refused(store: PostgresStore) -> None:
    schema = store_schema(store)
    await plant_sql(
        store,
        f'INSERT INTO "{schema}".conversations (conversation_id, created_at) '
        "VALUES ('planted', %(ts)s)",
        {"ts": _TS},
    )
    await plant_sql(
        store,
        f'INSERT INTO "{schema}".turns '
        "(conversation_id, turn, messages, created_at, neosian_format) "
        "VALUES ('planted', 1, '\"scalar\"'::jsonb, %(ts)s, 1)",
        {"ts": _TS},
    )
    with pytest.raises(ConversationFormatUnsupportedError):
        await store.read_turns("planted")


async def test_aclose_then_reuse_opens_a_fresh_pool(store: PostgresStore) -> None:
    await store.write(_SCOPE, "doc", "before close")
    await store.aclose()
    document = await store.read(_SCOPE, "doc")
    assert document is not None
    assert document.content == "before close"


async def test_nul_content_raises_the_driver_error(store: PostgresStore) -> None:
    """Ledger #38: Postgres text/jsonb reject U+0000 — a documented
    substrate exception to §8's byte-exact round-trip."""
    with pytest.raises(psycopg.Error):
        await store.write(_SCOPE, "doc", "a\x00b")
