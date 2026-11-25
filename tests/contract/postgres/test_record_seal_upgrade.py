"""Operator SQL upgrades an existing schema 4 + records 1 store in place."""

import hashlib
import json
import os

import psycopg
import pytest
from psycopg import sql

from tests.contract.file.test_records import STAMP, record
from tests.contract.file.test_records_fold import answer
from tests.contract.postgres.fold_fixture import snapshot
from tests.contract.postgres.history_fixture import native, postgres
from tests.contract.postgres.test_records_extension import Database, install
from tests.contract.postgres.test_records_fold import fold
from tests.contract.postgres.test_records_migrate import rows
from tests.contract.support import Binary, serve

pytestmark = pytest.mark.postgres

# Captured from signed 28ecd1d with schema neosian_v1_control.
V1_SQL = "1587abb5f1ceaa40741e99e4680358b30ac0cc717dfc9088b5b0f5b8272bc037"
SEALS = ("record_seals", "record_seal_pages")


def declared(binary: Binary, name: str, *version: str) -> str:
    result = binary.run("schema", "records-postgres", "--schema", name, *version)
    assert result.returncode == 0, result.stderr
    return result.stdout


def tables(database: Database) -> list[tuple[object, ...]]:
    return (
        database[0]
        .execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
            (database[1],),
        )
        .fetchall()
    )


def marker(database: Database) -> list[tuple[object, ...]]:
    return rows(database, "SELECT singleton,version FROM {s}.neosian_records")


def refuse_upgrade(database: Database, ddl: str) -> None:
    # The printed SQL contains its own transaction. End its failed transaction
    # before inspecting the catalog and stored data on the same connection.
    with pytest.raises(psycopg.Error):
        database[0].execute(ddl)
    database[0].execute("ROLLBACK")


def test_explicit_upgrade_prints_sql_and_preserves_existing_native_turns(
    binary: Binary, database: Database
) -> None:
    c, name, _ = database
    old = declared(binary, "neosian_v1_control")
    assert hashlib.sha256(old.encode()).hexdigest() == V1_SQL
    assert declared(binary, "neosian_v1_control", "--version", "1") == old
    present = tables(database)
    ddl = declared(binary, name, "--version", "2")
    assert tables(database) == present
    assert all((t,) not in present for t in SEALS)
    assert rows(database, "SELECT version FROM {s}.neosian_schema") == [(4,)]
    refuse_upgrade(database, ddl)
    assert tables(database) == present
    install(database, binary)
    assert marker(database) == [(True, 1)]
    originals = [
        record(0, 0, "user", "prompt", "hi", size=5),
        record(1, 0, "user", "prompt", "you", size=5, chunk=1, content=None),
    ]
    native(database, "existing", 12, STAMP, originals)
    physical = snapshot(database)
    with postgres(binary, database) as server:
        before = server.post("conversation/read_turns", conversation_id="existing")
        assert before["turns"][0]["messages"][0]["content"] == "hiyou"
    old_refusal = fold(binary, database, "existing")
    assert old_refusal.returncode == 1 and snapshot(database) == physical
    c.execute(ddl)
    assert marker(database) == [(True, 2)]
    assert set(tables(database)) == set(present) | {
        (n,)
        for n in [
            "neosian_records",
            "record_drafts",
            "turn_records",
            "record_turns",
            "record_staging",
            *SEALS,
        ]
    }
    assert snapshot(database) == physical
    with postgres(binary, database) as restarted:
        assert (
            restarted.post("conversation/read_turns", conversation_id="existing")
            == before
        )
    folded = fold(binary, database, "existing")
    assert (folded.returncode, folded.stdout) == (0, answer("existing", 1, 0))
    query = "SELECT turn,records,pages_seen,actor,draft,ctid::text,xmin::text FROM {s}.record_seals"
    pages = "SELECT page,digests,ctid::text,xmin::text FROM {s}.record_seal_pages"
    expected = b"".join(
        hashlib.sha256(
            json.dumps(r, separators=(",", ":"), ensure_ascii=False).encode()
        ).digest()
        for r in originals
    )
    headers, found = rows(database, query), rows(database, pages)
    assert [r[:5] for r in headers] == [(12, 2, 1, None, "d12")]
    assert [r[:2] for r in found] == [(0, expected)]
    c.execute(ddl)
    # Operators can still render and reapply the old idempotent install SQL.
    c.execute(declared(binary, name, "--version", "1"))
    assert marker(database) == [(True, 2)]
    assert rows(database, query) == headers and rows(database, pages) == found
    with postgres(binary, database) as restarted:
        assert (
            restarted.post("conversation/read_turns", conversation_id="existing")
            == before
        )


@pytest.mark.parametrize(
    "damage",
    [
        "bare_orphan",
        "v1_orphan",
        "missing_v1",
        "pretend_v2",
        "future",
        "missing_pages",
        "broken_column",
        "missing_trigger",
        "missing_draft_key",
        "missing_page_key",
    ],
)
def test_damaged_receipt_installations_are_refused_by_upgrade_and_reader(
    binary: Binary, database: Database, damage: str
) -> None:
    c, name, _ = database
    s = sql.Identifier(name)
    ddl = declared(binary, name, "--version", "2")
    if damage != "bare_orphan":
        install(database, binary)
    if damage in (
        "missing_pages",
        "broken_column",
        "missing_trigger",
        "missing_draft_key",
        "missing_page_key",
    ):
        c.execute(ddl)
    if damage in ("bare_orphan", "v1_orphan"):
        c.execute(sql.SQL("CREATE TABLE {}.record_seals (bad integer)").format(s))
    elif damage == "missing_v1":
        c.execute(sql.SQL("DROP TABLE {}.turn_records").format(s))
    elif damage == "pretend_v2":
        c.execute(sql.SQL("UPDATE {}.neosian_records SET version=2").format(s))
    elif damage == "future":
        c.execute(sql.SQL("UPDATE {}.neosian_records SET version=3").format(s))
    elif damage == "missing_pages":
        c.execute(sql.SQL("DROP TABLE {}.record_seal_pages").format(s))
    elif damage == "broken_column":
        c.execute(
            sql.SQL("ALTER TABLE {}.record_seal_pages RENAME digests TO unused").format(
                s
            )
        )
    elif damage == "missing_trigger":
        c.execute(
            sql.SQL(
                "DROP TRIGGER record_seal_pages_insert ON {}.record_seal_pages"
            ).format(s)
        )
    else:
        relation, key = (
            ("record_seals", "UNIQUE (conversation_id, draft)")
            if damage == "missing_draft_key"
            else ("record_seal_pages", "PRIMARY KEY (conversation_id, turn, page)")
        )
        lookup = c.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid=to_regclass(%s) AND pg_get_constraintdef(oid)=%s",
            (f"{name}.{relation}", key),
        ).fetchall()
        assert len(lookup) == 1 and isinstance(lookup[0][0], str)
        c.execute(
            sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                sql.Identifier(name, relation), sql.Identifier(lookup[0][0])
            )
        )
    original = tables(database)
    mark = marker(database) if damage != "bare_orphan" else []
    refuse_upgrade(database, ddl)
    assert tables(database) == original
    if mark:
        assert marker(database) == mark
    why = "unsupported" if damage == "future" else "incomplete"
    with (
        pytest.raises(AssertionError, match=f"{why} Postgres records extension"),
        serve(
            binary, postgres_dsn=os.environ["NEOSIAN_TEST_POSTGRES_DSN"], schema=name
        ),
    ):
        pytest.fail("a fresh reader started on an incompatible records schema")


def test_receipt_upgrade_remains_scoped_to_reference_schema_four(
    binary: Binary, database: Database
) -> None:
    c, name, _ = database
    install(database, binary)
    ddl = declared(binary, name, "--version", "2")
    rows(database, "UPDATE {s}.neosian_schema SET version=3")
    refuse_upgrade(database, ddl)
    assert marker(database) == [(True, 1)]
    assert rows(database, "SELECT version FROM {s}.neosian_schema") == [(3,)]
    assert all((t,) not in tables(database) for t in SEALS)
