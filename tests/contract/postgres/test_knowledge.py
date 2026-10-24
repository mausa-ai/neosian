"""Knowledge uses existing Postgres rows, versions and erasure without DDL."""

from __future__ import annotations

import json
import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.knowledge_support import invoke, success, unpack
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


@pytest.mark.parametrize("mode", ["direct", "http"])
def test_import_replace_export_and_erasure_preserve_backend_contract(
    binary: Binary, database: Database, mode: str
) -> None:
    connection, schema, server = database
    tables = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
        (schema,),
    ).fetchall()
    file = binary.directory / "a.md"
    file.write_bytes("# Café\r\nSource body\r\n".encode())
    flags = ["--scope", "user:knowledge"]
    env = {"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    if mode == "direct":
        flags += ["--schema", schema]
    else:
        flags += ["--url", server.url]
        env = {"NEOSIAN_CLIENT_TOKEN": server.token}
    args = ["import", str(file), "--to", "/memories/notes", "--write"]
    success(invoke(binary, *args, flags=flags, env=env))
    connection.execute(
        sql.SQL(
            "UPDATE {}.memories SET extra = %s::jsonb WHERE path = 'notes/a'"
        ).format(sql.Identifier(schema)),
        ('{"opaque":["retained",null,17]}',),
    )
    original = server.post("memory/read", scope="user:knowledge", path="notes/a")[
        "document"
    ]
    file.write_text("Replacement", encoding="utf-8")
    success(invoke(binary, *args, "--conflict", "replace", flags=flags, env=env))
    current = server.post("memory/read", scope="user:knowledge", path="notes/a")[
        "document"
    ]
    assert current["created_at"] == original["created_at"]
    assert current["extra"] == original["extra"] and current["version"] == 2
    output = binary.directory / "export"
    success(
        invoke(
            binary,
            "export",
            "/memories/notes",
            "--output",
            str(output),
            flags=flags,
            env=env,
        )
    )
    manifest = json.loads((output / "knowledge.json").read_text())
    assert manifest["documents"][0]["origin"]["extra"] == original["extra"]
    success(
        invoke(
            binary,
            "import",
            str(output),
            "--format",
            "bundle",
            "--to",
            "/memories/copied",
            "--write",
            flags=flags,
            env=env,
        )
    )
    copied = server.post("memory/read", scope="user:knowledge", path="copied/a")[
        "document"
    ]
    metadata, body = unpack(copied["content"])
    assert body == "Replacement"
    assert metadata["sources"][-1]["origin"]["extra"] == original["extra"]
    server.post("memory/redact", scope="user:knowledge", path="notes/a")
    refused = invoke(binary, *args, "--conflict", "replace", flags=flags, env=env)
    assert refused.returncode == 1
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
