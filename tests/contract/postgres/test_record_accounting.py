"""The operator's accounting example separates open work from published records."""

import pytest
from psycopg import sql

from tests.contract.file.test_records import RECORDS, record
from tests.contract.postgres.fold_fixture import draft, restore_unit
from tests.contract.postgres.test_records_extension import Database, install
from tests.contract.postgres.test_records_migrate import rows
from tests.contract.support import Binary

pytestmark = pytest.mark.postgres


def test_documented_meter_counts_each_owner_and_published_part_once(
    binary: Binary, database: Database
) -> None:
    conn, schema, _ = database
    install(database, binary)
    session = "selected"
    parts = {2: RECORDS, 8: [record(0, 0, "user", "prompt", "mémoire")]}
    restore_unit(database, session, parts)
    active = draft(database, session, "write", "active")
    c = len(session.encode())
    # Opening's bookkeeping and its seven-byte private record.
    held = len("active") + 2 * c + 1024 + len("pending") + 2 * c + 256
    rows(database, "UPDATE {s}.record_drafts SET bytes=%s WHERE owner=%s", held, active)
    draft(database, "other", "write", "active")
    published = sum(
        256
        + 2 * c
        + sum(len((row[field] or "").encode()) for field in ("text", "call_id", "name"))
        for part in parts.values()
        for row in part
    )
    marker = len("backup") + 2 * c + 256

    page = binary.run("docs", "stores")
    assert page.returncode == 0, page.stderr
    section = page.stdout.split("### Reconciling private and published records\n", 1)[1]
    query = section.split("```sql\n", 1)[1].split("\n```", 1)[0]
    statement = sql.SQL(
        query.replace("neosian.", "{s}.").replace(":'conversation_id'", "%s")
    ).format(s=sql.Identifier(schema))
    for version in (1, 2):
        if version == 2:
            upgrade = binary.run(
                "schema", "records-postgres", "--version", "2", "--schema", schema
            )
            assert upgrade.returncode == 0, upgrade.stderr
            conn.execute(upgrade.stdout)
        assert conn.execute(statement, (session,)).fetchall() == [
            (held, marker, published)
        ]
        assert conn.execute(statement, ("absent",)).fetchall() == [(0, 0, 0)]
