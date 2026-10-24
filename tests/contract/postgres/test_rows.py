"""Future and malformed rows refuse reads and mutations without losing data."""

from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.contract.storage.support import refused
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


def table(name: str, leaf: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(name), sql.Identifier(leaf))


@pytest.mark.parametrize("declared", [0, 2])
@pytest.mark.parametrize(
    "route,fields",
    [
        ("read", {}),
        ("write", {"content": "new"}),
        ("delete", {}),
        ("rename", {"src": "note", "dst": "moved"}),
        ("redact", {}),
    ],
)
def test_memory_format_refusal_precedes_mutation(
    database: Database, declared: int, route: str, fields: dict[str, Any]
) -> None:
    connection, name, server = database
    server.post("memory/write", scope="user:pg", path="note", content="kept")
    connection.execute(
        sql.SQL("UPDATE {} SET neosian_format=%s").format(table(name, "memories")),
        (declared,),
    )
    before = connection.execute(
        sql.SQL("SELECT * FROM {}").format(table(name, "memories"))
    ).fetchall()
    versions = connection.execute(
        sql.SQL("SELECT * FROM {}").format(table(name, "memory_versions"))
    ).fetchall()
    body = {"scope": "user:pg", **fields}
    if route != "rename":
        body["path"] = "note"
    refused(server.request("/v1/memory/" + route, body), "memory_format_unsupported")
    assert (
        connection.execute(
            sql.SQL("SELECT * FROM {}").format(table(name, "memories"))
        ).fetchall()
        == before
    )
    assert (
        connection.execute(
            sql.SQL("SELECT * FROM {}").format(table(name, "memory_versions"))
        ).fetchall()
        == versions
    )


@pytest.mark.parametrize(
    "change",
    [
        "neosian_format=2",
        "messages='{}'::jsonb",
        "messages='[]'::jsonb",
        "redacted=true",
    ],
)
def test_bad_turns_refuse_without_rewriting(database: Database, change: str) -> None:
    connection, name, server = database
    server.post(
        "conversation/append_turn",
        conversation_id="pg-session",
        messages=[{"role": "user", "content": "kept"}],
    )
    # `change` is a literal test table, never caller SQL.
    connection.execute(
        sql.SQL("UPDATE {} SET ").format(table(name, "turns")) + sql.SQL(change)
    )
    before = connection.execute(
        sql.SQL("SELECT * FROM {}").format(table(name, "turns"))
    ).fetchall()
    refused(
        server.request(
            "/v1/conversation/read_turns", {"conversation_id": "pg-session"}
        ),
        "agent_conversation_format_unsupported",
    )
    assert (
        connection.execute(
            sql.SQL("SELECT * FROM {}").format(table(name, "turns"))
        ).fetchall()
        == before
    )


def test_schema_is_idempotent_and_generation_four(
    database: Database, binary: Binary
) -> None:
    connection, name, server = database
    server.post("memory/write", scope="user:pg", path="note", content="kept")
    ddl = binary.run("schema", "postgres", "--schema", name)
    connection.execute(ddl.stdout)
    assert connection.execute(
        sql.SQL("SELECT version FROM {}").format(table(name, "neosian_schema"))
    ).fetchall() == [(4,)]
    assert (
        server.post("memory/read", scope="user:pg", path="note")["document"]["content"]
        == "kept"
    )


def test_erasure_does_not_rewrite_unknown_history(database: Database) -> None:
    connection, name, server = database
    server.post("memory/write", scope="user:pg", path="note", content="kept")
    connection.execute(
        sql.SQL("UPDATE {} SET neosian_format=2").format(table(name, "memory_versions"))
    )
    refused(
        server.request("/v1/memory/redact", {"scope": "user:pg"}),
        "memory_format_unsupported",
    )
    assert connection.execute(
        sql.SQL("SELECT content FROM {}").format(table(name, "memories"))
    ).fetchall() == [("kept",)]
    assert connection.execute(
        sql.SQL("SELECT content FROM {}").format(table(name, "memory_versions"))
    ).fetchall() == [("kept",)]
    assert connection.execute(
        sql.SQL("SELECT count(*) FROM {}").format(table(name, "memory_redactions"))
    ).fetchone() == (0,)


@pytest.mark.parametrize("leaf", ["turns", "projections"])
@pytest.mark.parametrize("declared", [0, 2])
def test_turn_erasure_preflights_all_affected_formats(
    database: Database, leaf: str, declared: int
) -> None:
    connection, name, server = database
    server.post(
        "conversation/append_turn",
        conversation_id="pg-session",
        messages=[{"role": "user", "content": "kept"}],
    )
    server.post(
        "conversation/append_projections",
        conversation_id="pg-session",
        entries=[{"turn": 1, "kind": "digest", "text": "kept", "span": 1}],
    )
    connection.execute(
        sql.SQL("UPDATE {} SET neosian_format=%s").format(table(name, leaf)),
        (declared,),
    )

    def snapshot() -> list[list[tuple[object, ...]]]:
        return [
            connection.execute(
                sql.SQL("SELECT * FROM {}").format(table(name, item))
            ).fetchall()
            for item in ("turns", "projections", "turn_redactions")
        ]

    before = snapshot()
    refused(
        server.request(
            "/v1/conversation/redact_turns", {"conversation_id": "pg-session"}
        ),
        "agent_conversation_format_unsupported",
    )
    assert snapshot() == before
