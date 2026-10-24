"""Governed records use existing Postgres rows, indexes and erasure semantics."""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.governance_support import SCOPE, proposal
from tests.contract.knowledge_support import invoke, success
from tests.contract.support import Binary, Server
from tests.contract.upkeep_support import invoke as upkeep, save

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


@pytest.mark.parametrize("mode", ["direct", "http"])
def test_review_and_redaction_clear_current_historical_and_generated_search_content(
    binary: Binary, database: Database, mode: str
) -> None:
    connection, schema, server = database
    server.post(
        "memory/write", scope=SCOPE, path="notes/a", content="Reviewable needle"
    )
    tables = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
        (schema,),
    ).fetchall()
    flags = ["--scope", SCOPE]
    if mode == "direct":
        flags += ["--schema", schema]
        env = {"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    else:
        flags += ["--url", server.url]
        env = {"NEOSIAN_CLIENT_TOKEN": server.token}
    plan = proposal(binary, flags=flags, env=env)
    file = save(binary, "proposal.json", plan)
    success(
        upkeep(
            binary,
            "apply",
            str(file),
            "--write",
            "--approve",
            plan["id"],
            flags=flags,
            env=env,
        )
    )
    assert success(
        invoke(
            binary,
            "read",
            "/memories/notes/a",
            "--policy",
            "approved",
            flags=flags,
            env=env,
        )
    )["included"]
    assert (
        len(
            server.post(
                "memory/search",
                scope=SCOPE,
                query="needle",
                knowledge_policy="approved",
            )["hits"]
        )
        == 1
    )
    before = server.post("memory/read", scope=SCOPE, path="notes/a")["document"]
    server.post("memory/redact", scope=SCOPE, path="notes/a")
    after = server.post("memory/read", scope=SCOPE, path="notes/a")["document"]
    assert (
        after["version"] == before["version"]
        and after["updated_at"] == before["updated_at"]
    )
    assert connection.execute(
        sql.SQL(
            "SELECT content, redacted, search::text FROM {}.memories WHERE scope = %s AND path = 'notes/a'"
        ).format(sql.Identifier(schema)),
        (SCOPE,),
    ).fetchall() == [("", True, "")]
    versions = connection.execute(
        sql.SQL(
            "SELECT content, redacted FROM {}.memory_versions WHERE scope = %s AND path = 'notes/a'"
        ).format(sql.Identifier(schema)),
        (SCOPE,),
    ).fetchall()
    assert len(versions) == 2 and all(row == ("", True) for row in versions)
    assert (
        server.post(
            "memory/search",
            scope=SCOPE,
            query="PRIVATE_REVIEW_LABEL",
            knowledge_policy="all",
        )["hits"]
        == []
    )
    assert (
        invoke(binary, "read", "/memories/notes/a", flags=flags, env=env).returncode
        == 1
    )
    assert (
        connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (schema,),
        ).fetchall()
        == tables
    )
