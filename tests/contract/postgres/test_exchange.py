"""Direct and HTTP Postgres sources feed the same local replica contract."""

from __future__ import annotations

import json
import os

import psycopg
import pytest

from tests.contract.exchange_support import SCOPE, apply, export, files
from tests.contract.profile_support import run
from tests.contract.support import ADMIN, Binary, Server, serve

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


@pytest.mark.parametrize("remote", [False, True])
def test_postgres_snapshot_delta_and_erasure(
    database: Database, binary: Binary, remote: bool
) -> None:
    connection, schema, _ = database
    ddl = binary.run("schema", "changes-postgres", "--schema", schema)
    assert ddl.returncode == 0, ddl.stderr
    connection.execute(ddl.stdout)
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    with serve(binary, postgres_dsn=dsn, schema=schema) as server:
        server.post(
            "memory/write", scope=SCOPE, path="a", content="postgres-private-marker"
        )
        flags = ["--scope", SCOPE] + (
            ["--url", server.url] if remote else ["--schema", schema]
        )
        env = (
            {"NEOSIAN_CLIENT_TOKEN": ADMIN} if remote else {"NEOSIAN_POSTGRES_DSN": dsn}
        )
        snap = export(binary, "snapshot", flags=flags, env=env)
        apply(binary, snap)
        before = json.loads(snap.read_text())["updates"][0]["document"]
        server.post("memory/redact", scope=SCOPE, path="a")
        delta = export(binary, "delta", after=snap, flags=flags, env=env)
        after = json.loads(delta.read_text())["updates"][0]["document"]
        assert (before["version"], before["updated_at"]) == (
            after["version"],
            after["updated_at"],
        )
        assert after["redacted"] and not after["content"]
        apply(binary, delta)
        assert b"postgres-private-marker" not in b"".join(files(binary).values())


def test_postgres_without_extension_refuses_exchange(
    database: Database, binary: Binary
) -> None:
    _, _, server = database
    server.post("memory/write", scope=SCOPE, path="a", content="legacy")
    output = binary.directory / "snapshot.json"
    result = run(
        binary,
        "exchange",
        "snapshot",
        "/memories",
        str(output),
        "--scope",
        SCOPE,
        "--url",
        server.url,
        "--paused",
        env={"NEOSIAN_CLIENT_TOKEN": ADMIN},
    )
    assert result.returncode == 1 and not output.exists()
