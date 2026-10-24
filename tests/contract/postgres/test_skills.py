"""Managed skills retain physical Postgres versions and opaque metadata."""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.knowledge_support import success, unpack
from tests.contract.skill_support import SKILL, invoke, package
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


@pytest.mark.parametrize("mode", ["direct", "http"])
def test_skill_package_edit_resource_erasure_and_export(
    binary: Binary, database: Database, mode: str
) -> None:
    connection, schema, server = database
    flags = ["--scope", "user:skills"]
    if mode == "direct":
        flags += ["--schema", schema]
        env = {"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    else:
        flags += ["--url", server.url]
        env = {"NEOSIAN_CLIENT_TOKEN": server.token}
    source = package(binary.directory / "package")
    path = "/memories/skills/review"
    success(
        invoke(
            binary, "import", str(source), "--to", path, "--write", flags=flags, env=env
        )
    )
    connection.execute(
        sql.SQL(
            "UPDATE {}.memories SET extra = %s::jsonb WHERE path = 'skills/review'"
        ).format(sql.Identifier(schema)),
        ('{"opaque":{"keep":[1,null,"data"]}}',),
    )
    old = server.post("memory/read", scope="user:skills", path="skills/review")[
        "document"
    ]
    file = binary.directory / "edit.md"
    file.write_text(SKILL.replace("reasoning", "revised reasoning"))
    success(
        invoke(
            binary,
            "edit",
            path,
            "--file",
            str(file),
            "--expect-version",
            "1",
            "--write",
            flags=flags,
            env=env,
        )
    )
    current = server.post("memory/read", scope="user:skills", path="skills/review")[
        "document"
    ]
    assert current["version"] == 2 and current["created_at"] == old["created_at"]
    assert current["extra"] == old["extra"]
    output = binary.directory / "out"
    success(
        invoke(binary, "export", path, "--output", str(output), flags=flags, env=env)
    )
    metadata, _ = unpack((output / "SKILL.md").read_text())
    assert metadata["sources"][-1]["origin"]["extra"] == old["extra"]
    server.post(
        "memory/redact", scope="user:skills", path="skills/review/references/reason.md"
    )
    failed = invoke(binary, "load", path, flags=flags, env=env)
    assert failed.returncode == 1 and "Keep the" not in failed.stdout
    rows = connection.execute(
        sql.SQL(
            "SELECT content, redacted FROM {}.memory_versions WHERE path = 'skills/review/references/reason.md'"
        ).format(sql.Identifier(schema))
    ).fetchall()
    assert rows and all(row == ("", True) for row in rows)
    assert (
        invoke(
            binary,
            "import",
            str(source),
            "--to",
            path,
            "--replace",
            "--write",
            flags=flags,
            env=env,
        ).returncode
        != 0
    )
    assert (
        server.post("memory/read", scope="user:skills", path="skills/review")[
            "document"
        ]["version"]
        == 2
    )
