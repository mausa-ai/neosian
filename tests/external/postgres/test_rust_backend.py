"""Substrate parity beyond the kits: keysets, nested metadata and failure paths."""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from neosian import PostgresStore, RemoteStore
from neosian.memory import ScopeArchive
from tests.support.postgres import fetch_sql, plant_sql, store_schema
from tests.support.postgres_server import remote_postgres
from tests.unit.memory.paging import SCOPE, seed


@pytest.fixture
async def remote(
    store: PostgresStore, postgres_dsn: str, tmp_path: Path
) -> AsyncIterator[RemoteStore]:
    async with remote_postgres(
        store, postgres_dsn, tmp_path, "rust", "rust-check"
    ) as client:
        yield client


async def test_keysets_are_identical_on_ties_and_resume_across_servers(
    store: PostgresStore, remote: RemoteStore
) -> None:
    await seed(store)
    schema = store_schema(store)
    for table in ("memory_versions", "memory_redactions"):
        await plant_sql(
            store,
            f'UPDATE "{schema}".{table} SET created_at = %(at)s::timestamptz',
            {"at": "2026-01-01T12:00:00+02:00"},
        )
    for name, args in (
        ("list_documents_page", (SCOPE,)),
        ("versions_page", (SCOPE, "notes/1")),
        ("history_page", (SCOPE,)),
        ("redactions_page", (SCOPE,)),
    ):
        cursor = None
        while True:
            python = await getattr(store, name)(*args, cursor=cursor, limit=2)
            rust = await getattr(remote, name)(*args, cursor=cursor, limit=2)
            assert rust == python
            cursor = rust.next_cursor
            if cursor is None:
                break


async def test_nested_metadata_survives_reads_mutations_and_restore(
    store: PostgresStore, remote: RemoteStore
) -> None:
    document = await store.write("user:source", "doc", "original")
    extra = {"a: b": {"items": [True, None, 3, "a\nb"], "quoted": "'\""}}
    document = replace(document, scope="user:target", extra=extra)
    await remote.restore_scope(ScopeArchive("user:target", (document,), (), ()))
    assert await remote.read("user:target", "doc") == document
    written = await remote.write("user:target", "doc", "edited", expected_version=1)
    assert dict(written.extra) == extra
    renamed = await remote.rename("user:target", "doc", "moved")
    assert dict(renamed.extra) == extra
    assert await store.read("user:target", "moved") == renamed


async def test_bad_restore_leaves_the_whole_scope_empty(
    store: PostgresStore, remote: RemoteStore
) -> None:
    document = await store.write("user:seed", "clean", "fine")
    clean = replace(document, scope="user:nul")
    dirty = replace(clean, path="dirty", content="a\x00b")
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await remote.restore_scope(ScopeArchive("user:nul", (clean, dirty), (), ()))
    assert caught.value.response.status_code == 500
    assert caught.value.response.text == "Internal Server Error"
    assert await store.list_documents("user:nul") == ()
    assert await store.history("user:nul") == ()


async def test_a_missing_table_is_not_created_and_sql_details_stay_private(
    store: PostgresStore, remote: RemoteStore
) -> None:
    schema = store_schema(store)
    await plant_sql(store, f'DROP TABLE "{schema}".memories', {})
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await remote.read("user:test", "doc")
    assert caught.value.response.text == "Internal Server Error"
    assert await fetch_sql(
        store, "SELECT to_regclass(%(table)s)", {"table": f"{schema}.memories"}
    ) == [(None,)]
