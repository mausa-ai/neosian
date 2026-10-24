"""Direct Postgres bindings retain the operator-owned schema and stored identity."""

import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.profile_support import PROFILE, bind, install, rpc, run
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


def test_profile_routes_to_an_existing_postgres_schema(
    binary: Binary, database: Database
) -> None:
    connection, schema, server = database
    server.post(
        "memory/write", scope="workspace:portable", path="decision", content="preserved"
    )
    install(
        binary, PROFILE.replace('skills = ["/project/skills/review"]', "skills = []")
    )
    bind(binary, "--dsn-env", "PROFILE_TEST_DSN", "--schema", schema)
    env = {"PROFILE_TEST_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    answers = rpc(
        binary, [("memory", {"command": "view", "path": "/project/decision"})], env=env
    )
    assert "preserved" in answers[2]["result"]["content"][0]["text"]
    updated = run(
        binary,
        "memory",
        "str_replace",
        "/project/decision",
        "--old-str",
        "preserved",
        "--new-str",
        "updated",
        "--profile",
        "portable",
        "--actor",
        "cli:portable",
        "--json",
        env=env,
    )
    assert updated.returncode == 0, updated.stderr
    row = connection.execute(
        sql.SQL("SELECT scope, version, actor FROM {}.memories").format(
            sql.Identifier(schema)
        )
    ).fetchone()
    assert row == ("workspace:portable", 2, "cli:portable")
