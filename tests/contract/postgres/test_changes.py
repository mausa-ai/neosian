"""Operator extension installation and physical rollback, without product internals."""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from tests.contract.support import Binary, Server, serve

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


@pytest.fixture
def changes_database(database: Database, binary: Binary) -> Iterator[Database]:
    connection, name, _legacy = database
    ddl = binary.run("schema", "changes-postgres", "--schema", name)
    assert ddl.returncode == 0, ddl.stderr
    connection.execute(ddl.stdout)
    with serve(
        binary, postgres_dsn=os.environ["NEOSIAN_TEST_POSTGRES_DSN"], schema=name
    ) as server:
        assert server.request("/v1/capabilities", method="GET").body["change_feed"] == {
            "version": 1
        }
        yield connection, name, server


def test_base_schema_four_never_installs_changes_automatically(
    database: Database,
) -> None:
    connection, name, server = database
    assert "change_feed" not in server.request("/v1/capabilities", method="GET").body
    server.post(
        "memory/write", scope="test:legacy", path="doc", content="still supported"
    )
    refused = server.request("/v1/memory/changes", {"scope": "test:legacy"})
    assert refused.status == 400
    assert connection.execute(
        "SELECT to_regclass(%s)", (name + ".memory_changes",)
    ).fetchone() == (None,)


@pytest.mark.parametrize("operation", ["write", "delete", "rename", "redact"])
def test_event_failure_rolls_back_the_entire_memory_mutation(
    changes_database: Database, operation: str
) -> None:
    connection, name, server = changes_database
    scope = "test:rollback"
    doc = server.post("memory/write", scope=scope, path="doc", content="unchanged")[
        "document"
    ]
    before = server.post("memory/changes", scope=scope)
    connection.execute(
        sql.SQL(
            "ALTER TABLE {}.memory_changes ADD CONSTRAINT reject_new CHECK (sequence <= 2)"
        ).format(sql.Identifier(name))
    )
    payload = {"scope": scope, "path": "doc"}
    if operation == "write":
        payload["content"] = "must roll back"
    if operation == "rename":
        payload = {"scope": scope, "src": "doc", "dst": "moved"}
    failed = server.request("/v1/memory/" + operation, payload)
    assert failed.status >= 400
    assert server.post("memory/read", scope=scope, path="doc")["document"] == doc
    assert len(server.post("memory/versions", scope=scope, path="doc")["versions"]) == 1
    assert server.post("memory/read", scope=scope, path="moved")["document"] is None
    assert server.post("memory/changes", scope=scope) == before
    assert connection.execute(
        sql.SQL(
            "SELECT sequence, floor FROM {}.memory_change_streams WHERE scope = %s"
        ).format(sql.Identifier(name)),
        (scope,),
    ).fetchone() == (2, 0)
    connection.execute(
        sql.SQL("ALTER TABLE {}.memory_changes DROP CONSTRAINT reject_new").format(
            sql.Identifier(name)
        )
    )
    server.post("memory/write", scope=scope, path="doc", content="accepted")
    assert (
        server.post("memory/changes", scope=scope, cursor=before["cursor"])["events"][
            0
        ]["sequence"]
        == 3
    )


def test_retention_failure_restores_events_and_floor(
    changes_database: Database,
) -> None:
    connection, name, server = changes_database
    scope = "test:retention"
    server.post("memory/write", scope=scope, path="doc", content="one")
    page = server.post("memory/changes", scope=scope)
    connection.execute(
        sql.SQL(
            "ALTER TABLE {}.memory_change_streams ADD CONSTRAINT keep_floor CHECK (floor = 0)"
        ).format(sql.Identifier(name))
    )
    failed = server.request(
        "/v1/memory/prune_changes",
        {"scope": scope, "through": page["cursor"], "write": True},
    )
    assert failed.status >= 400
    assert server.post("memory/changes", scope=scope) == page


def test_restore_failure_does_not_leave_documents_without_events(
    changes_database: Database,
) -> None:
    connection, name, server = changes_database
    doc = server.post("memory/write", scope="test:source", path="doc", content="one")[
        "document"
    ]
    rows = server.post("memory/versions", scope="test:source", path="doc")["versions"]
    connection.execute(
        sql.SQL(
            "ALTER TABLE {}.memory_changes ADD CONSTRAINT source_only CHECK (scope = 'test:source')"
        ).format(sql.Identifier(name))
    )
    failed = server.request(
        "/v1/store/restore_scope",
        {
            "scope": "test:target",
            "documents": [doc | {"scope": "test:target"}],
            "versions": [row | {"scope": "test:target"} for row in rows],
            "redactions": [],
        },
    )
    assert failed.status >= 400
    assert (
        server.post("memory/read", scope="test:target", path="doc")["document"] is None
    )
    assert server.post("memory/changes", scope="test:target")["gap"] == "untracked"


@pytest.mark.parametrize("damage", ["missing", "future"])
def test_missing_or_future_events_refuse(
    changes_database: Database, damage: str
) -> None:
    connection, name, server = changes_database
    server.post("memory/write", scope="test:corrupt", path="doc", content="one")
    query = (
        "DELETE FROM {}.memory_changes WHERE sequence = 2"
        if damage == "missing"
        else "UPDATE {}.memory_changes SET event = jsonb_set(event, '{{format}}', '2') WHERE sequence = 2"
    )
    connection.execute(sql.SQL(query).format(sql.Identifier(name)))
    assert server.request("/v1/memory/changes", {"scope": "test:corrupt"}).status >= 400


@pytest.mark.parametrize("damage", ["partial", "future"])
def test_unsupported_extension_refuses_before_mutating(
    database: Database, binary: Binary, damage: str
) -> None:
    connection, name, _legacy = database
    connection.execute(
        binary.run("schema", "changes-postgres", "--schema", name).stdout
    )
    if damage == "partial":
        connection.execute(
            sql.SQL("DROP TABLE {}.memory_changes").format(sql.Identifier(name))
        )
    else:
        connection.execute(
            sql.SQL("UPDATE {}.neosian_changes SET version = 2").format(
                sql.Identifier(name)
            )
        )
    with serve(
        binary, postgres_dsn=os.environ["NEOSIAN_TEST_POSTGRES_DSN"], schema=name
    ) as server:
        assert server.request("/v1/capabilities", method="GET").status >= 400
        assert (
            server.request(
                "/v1/memory/write",
                {"scope": "test:invalid", "path": "doc", "content": "must not exist"},
            ).status
            >= 400
        )
    assert connection.execute(
        sql.SQL("SELECT count(*) FROM {}.memories").format(sql.Identifier(name))
    ).fetchone() == (0,)
