"""Direct SQL-backed search reads current rows without adding a derived index."""

import json
import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.profile_support import run
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


def test_direct_search_and_erasure_do_not_change_versions_or_metadata(
    binary: Binary, database: Database
) -> None:
    connection, schema, server = database
    original = server.post(
        "memory/write", scope="user:search", path="decision", content="Café decision"
    )["document"]
    connection.execute(
        sql.SQL("UPDATE {}.memories SET extra = %s::jsonb").format(
            sql.Identifier(schema)
        ),
        ('{"tags": ["Blue", "stable"]}',),
    )
    tables = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
        (schema,),
    ).fetchall()
    args = [
        "memory",
        "search",
        "blue",
        "--field",
        "metadata",
        "--metadata-key",
        "tags",
        "--scope",
        "user:search",
        "--schema",
        schema,
        "--json",
    ]
    env = {"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    result = run(binary, *args, env=env)
    assert result.returncode == 0, result.stderr
    hit = json.loads(result.stdout)["hits"][0]
    assert hit["reference"] == "/memories/decision"
    assert hit["version"] == original["version"]
    assert hit["excerpt"]["text"] == "Blue"
    before = connection.execute(
        sql.SQL("SELECT version, updated_at, extra FROM {}.memories").format(
            sql.Identifier(schema)
        )
    ).fetchone()
    server.post("memory/redact", scope="user:search", path="decision")
    assert json.loads(run(binary, *args, env=env).stdout)["hits"] == []
    after = connection.execute(
        sql.SQL("SELECT version, updated_at, extra FROM {}.memories").format(
            sql.Identifier(schema)
        )
    ).fetchone()
    assert (
        before == after
    )  # Erasure leaves these fields intact; none may keep a stale hit.
    assert (
        connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (schema,),
        ).fetchall()
        == tables
    )


def test_search_refuses_future_physical_rows(database: Database) -> None:
    connection, schema, server = database
    server.post("memory/write", scope="user:search", path="doc", content="needle")
    connection.execute(
        sql.SQL("UPDATE {}.memories SET neosian_format = 99").format(
            sql.Identifier(schema)
        )
    )
    reply = server.request(
        "/v1/memory/search", {"scope": "user:search", "query": "needle"}
    )
    assert (
        reply.status == 400
        and reply.body["error"]["code"] == "memory_format_unsupported"
    )
    assert connection.execute(
        sql.SQL("SELECT content, neosian_format FROM {}.memories").format(
            sql.Identifier(schema)
        )
    ).fetchone() == ("needle", 99)
