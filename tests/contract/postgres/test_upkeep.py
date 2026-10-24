"""Reviewed upkeep preserves Postgres data and uses its existing version fence."""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.knowledge_support import success, unpack
from tests.contract.support import Binary, Server
from tests.contract.upkeep_support import edit, invoke, prepare

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


@pytest.mark.parametrize("mode", ["direct", "http"])
def test_reviewed_updates_recovery_and_erasure_use_existing_rows(
    binary: Binary, database: Database, mode: str
) -> None:
    connection, schema, server = database
    tables = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
        (schema,),
    ).fetchall()
    server.post(
        "memory/write", scope="user:upkeep", path="notes/a", content="Original fact"
    )
    connection.execute(
        sql.SQL(
            "UPDATE {}.memories SET extra = %s::jsonb WHERE path = 'notes/a'"
        ).format(sql.Identifier(schema)),
        ('{"opaque":[null,17,"retained"]}',),
    )
    flags = ["--scope", "user:upkeep"]
    env = {"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    if mode == "direct":
        flags += ["--schema", schema]
    else:
        flags += ["--url", server.url]
        env = {"NEOSIAN_CLIENT_TOKEN": server.token}
    path, proposal = prepare(binary, [edit()], flags, env)
    success(
        invoke(
            binary,
            "apply",
            str(path),
            "--write",
            "--approve",
            proposal["id"],
            flags=flags,
            env=env,
        )
    )
    doc = server.post("memory/read", scope="user:upkeep", path="notes/a")["document"]
    assert doc["version"] == 2 and doc["extra"] == {"opaque": [None, 17, "retained"]}
    source = unpack(doc["content"])[0]["sources"][0]
    assert source["origin"]["extra"] == doc["extra"]
    path, proposal = prepare(
        binary,
        [
            {
                "path": "/memories/notes/a",
                "operation": "restore",
                "expected_version": 2,
                "restore_version": 1,
                "reason": "Restore original evidence",
            }
        ],
        flags,
        env,
    )
    success(
        invoke(
            binary,
            "apply",
            str(path),
            "--write",
            "--approve",
            proposal["id"],
            flags=flags,
            env=env,
        )
    )
    assert (
        unpack(
            server.post("memory/read", scope="user:upkeep", path="notes/a")["document"][
                "content"
            ]
        )[1]
        == "Original fact"
    )
    path, proposal = prepare(binary, [edit(expected_version=3)], flags, env)
    server.post("memory/redact", scope="user:upkeep", path="notes/a")
    assert (
        invoke(
            binary,
            "apply",
            str(path),
            "--write",
            "--approve",
            proposal["id"],
            flags=flags,
            env=env,
        ).returncode
        == 1
    )
    rows = connection.execute(
        sql.SQL(
            "SELECT content, redacted FROM {}.memory_versions WHERE path = 'notes/a'"
        ).format(sql.Identifier(schema))
    ).fetchall()
    assert rows and all(row == ("", True) for row in rows)
    assert (
        connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (schema,),
        ).fetchall()
        == tables
    )
