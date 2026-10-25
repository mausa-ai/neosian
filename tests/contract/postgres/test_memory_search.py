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


def test_search_streams_to_lookahead_and_bounds_expanded_row_bytes(
    binary: Binary, database: Database
) -> None:
    connection, schema, server = database
    for path in ["a", "b", "c"]:
        server.post("memory/write", scope="user:search", path=path, content="needle")
    # Large highly compressible values must count their expanded bytes, including extra.
    connection.execute(
        sql.SQL("UPDATE {}.memories SET extra = %s::jsonb WHERE path = 'c'").format(
            sql.Identifier(schema)
        ),
        (json.dumps({"large": "x" * 12000}),),
    )
    env = {
        "NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"],
        "NEOSIAN_MAX_FILE_BYTES": "1024",
    }
    args = [
        "memory",
        "search",
        "needle",
        "--scope",
        "user:search",
        "--schema",
        schema,
        "--json",
    ]
    first = run(binary, *args, "--limit", "1", env=env)
    assert first.returncode == 0, first.stderr
    page = json.loads(first.stdout)
    assert [hit["path"] for hit in page["hits"]] == ["a"] and page["next_cursor"]
    refused = run(binary, *args, "--limit", "3", env=env)
    assert refused.returncode != 0 and "NEOSIAN_MAX_FILE_BYTES" in refused.stderr
    env["NEOSIAN_MAX_FILE_BYTES"] = "20000"
    raised = run(binary, *args, "--limit", "3", env=env)
    assert raised.returncode == 0, raised.stderr
    assert [hit["path"] for hit in json.loads(raised.stdout)["hits"]] == ["a", "b", "c"]
    assert connection.execute(
        sql.SQL(
            "SELECT version, content, length(extra->>'large') FROM {}.memories WHERE path = 'c'"
        ).format(sql.Identifier(schema))
    ).fetchone() == (1, "needle", 12000)
