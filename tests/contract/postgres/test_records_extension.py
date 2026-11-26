"""Records extension 1 is separately installed and detected on Postgres schema 4."""

import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.support import Binary, Server, serve

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]

# All physical columns, in published order. Column types use pg_catalog names.
TABLES = {
    "neosian_records": "singleton:bool version:int4",
    "record_drafts": (
        "owner:int8 conversation_id:text kind:text name:text state:text actor:text "
        "turn_bytes:int8 opened_at:timestamptz touched_at:timestamptz position:jsonb bytes:int8"
    ),
    "turn_records": (
        "conversation_id:text owner:int8 part:int4 seq:int4 message:int4 role:text "
        "kind:text content:text block:int4 call:int4 call_id:text name:text "
        "chunk:int4 size:int8 last:bool text:text digest:bytea neosian_format:int4"
    ),
    "record_turns": (
        "conversation_id:text turn:int4 owner:int8 part:int4 draft:text records:int4 "
        "bytes:int8 created_at:timestamptz"
    ),
    "record_staging": "conversation_id:text owner:int8 ord:int8 item:text body:jsonb digest:bytea",
}


def install(database: Database, binary: Binary) -> None:
    connection, name, _ = database
    ddl = binary.run("schema", "records-postgres", "--schema", name)
    assert ddl.returncode == 0, ddl.stderr
    connection.execute(ddl.stdout)


def test_records_install_is_explicit_idempotent_and_keeps_the_published_keys(
    database: Database, binary: Binary
) -> None:
    connection, name, legacy = database
    capabilities = legacy.request("/v1/capabilities", method="GET")
    assert capabilities.status == 200
    assert "turn_records" not in capabilities.body
    assert (
        legacy.post("conversation/read_turns", conversation_id="empty")["turns"] == []
    )
    columns = """
        SELECT table_name, string_agg(column_name || ':' || udt_name, ' ' ORDER BY ordinal_position)
        FROM information_schema.columns WHERE table_schema = %s AND table_name = ANY(%s)
        GROUP BY table_name ORDER BY table_name
    """
    assert connection.execute(columns, (name, list(TABLES))).fetchall() == []
    install(database, binary)
    install(database, binary)
    found = connection.execute(columns, (name, list(TABLES))).fetchall()
    assert found == sorted(TABLES.items())
    assert connection.execute(
        sql.SQL("SELECT singleton, version FROM {}.neosian_records").format(
            sql.Identifier(name)
        )
    ).fetchall() == [(True, 1)]
    constraints = connection.execute(
        """
        SELECT relname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND relname = ANY(%s) AND contype IN ('u', 'p', 'f')
        """,
        (name, list(TABLES)),
    ).fetchall()
    assert set(constraints) == {
        ("neosian_records", "PRIMARY KEY (singleton)"),
        ("record_drafts", "PRIMARY KEY (owner)"),
        ("record_drafts", "UNIQUE (conversation_id, kind, name)"),
        ("turn_records", "PRIMARY KEY (conversation_id, owner, part, seq)"),
        ("record_turns", "PRIMARY KEY (conversation_id, turn)"),
        ("record_turns", "UNIQUE (conversation_id, owner, part)"),
        ("record_staging", "PRIMARY KEY (conversation_id, owner, ord)"),
    }
    indexes = connection.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = 'record_turns_draft'",
        (name,),
    ).fetchall()
    assert len(indexes) == 1 and str(indexes[0][0]).endswith("(conversation_id, draft)")
    assert connection.execute(
        sql.SQL("SELECT version FROM {}.neosian_schema").format(sql.Identifier(name))
    ).fetchone() == (4,)
    # The handshake (item 8.1): a server answers the layout it detected, so
    # the one started before the install keeps its document, and a server
    # started after it adds the one key, last. Extension 2 is the same
    # capability version.
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    assert legacy.request("/v1/capabilities", method="GET").body == capabilities.body
    declared = {**capabilities.body, "turn_records": {"version": 1}}
    with serve(binary, postgres_dsn=dsn, schema=name) as fresh:
        first = fresh.request("/v1/capabilities", method="GET")
        assert (first.status, first.body) == (200, declared)
        assert list(first.body) == [*capabilities.body, "turn_records"]
    upgrade = binary.run(
        "schema", "records-postgres", "--version", "2", "--schema", name
    )
    assert upgrade.returncode == 0, upgrade.stderr
    connection.execute(upgrade.stdout)
    with serve(binary, postgres_dsn=dsn, schema=name) as second:
        assert second.request("/v1/capabilities", method="GET").body == declared


@pytest.mark.parametrize(
    "damage", [*TABLES, "empty_marker", "damaged_marker", "future_version"]
)
def test_record_store_refuses_incomplete_or_future_extension_by_name(
    database: Database, binary: Binary, damage: str
) -> None:
    connection, name, legacy = database
    assert (
        legacy.post("conversation/read_turns", conversation_id="empty")["turns"] == []
    )
    install(database, binary)
    schema = sql.Identifier(name)
    if damage in TABLES:
        connection.execute(
            sql.SQL("DROP TABLE {}.{}").format(schema, sql.Identifier(damage))
        )
    elif damage == "empty_marker":
        connection.execute(sql.SQL("DELETE FROM {}.neosian_records").format(schema))
    elif damage == "damaged_marker":
        connection.execute(
            sql.SQL(
                "ALTER TABLE {}.neosian_records RENAME version TO original_version"
            ).format(schema)
        )
    else:
        connection.execute(
            sql.SQL("UPDATE {}.neosian_records SET version = 3").format(schema)
        )
        # Reapplying this release's DDL must leave the future version in place.
        install(database, binary)
    # The pre-extension process keeps its cached decision until restarted.
    assert (
        legacy.post("conversation/read_turns", conversation_id="empty")["turns"] == []
    )
    expected = "unsupported" if damage == "future_version" else "incomplete"
    with (
        pytest.raises(AssertionError, match=f"{expected} Postgres records extension"),
        serve(
            binary, postgres_dsn=os.environ["NEOSIAN_TEST_POSTGRES_DSN"], schema=name
        ),
    ):
        pytest.fail("server started with an unusable records extension")


@pytest.mark.parametrize(
    ("setting", "value", "lower", "upper"),
    [
        ("NEOSIAN_DRAFT_SECONDS", "59", 60, 2_592_000),
        ("NEOSIAN_MAX_TURN_BYTES", "65535", 65_536, 1 << 30),
        ("NEOSIAN_MAX_RECORD_TURN_BYTES", str((16 << 30) + 1), 65_536, 16 << 30),
    ],
)
def test_record_settings_apply_at_startup_only_on_enabled_postgres(
    database: Database, binary: Binary, setting: str, value: str, lower: int, upper: int
) -> None:
    _, name, _ = database
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    bad = {setting: value}
    with serve(binary, postgres_dsn=dsn, schema=name, extra_env=bad) as legacy:
        assert (
            legacy.post("conversation/read_turns", conversation_id="empty")["turns"]
            == []
        )
    install(database, binary)
    with (
        pytest.raises(
            AssertionError,
            match=rf"{setting} must be an integer in {lower}\.\.{upper}",
        ),
        serve(binary, postgres_dsn=dsn, schema=name, extra_env=bad),
    ):
        pytest.fail(f"server started with an invalid {setting}")
