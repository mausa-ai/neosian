"""A Postgres schema with records extension 1 keeps every legacy route's
bytes (DESIGN §40.54 item 15.1).

The script of `tests/contract/records/test_legacy_bytes.py` is sent to two
servers, one on a reference schema 4 and one on a schema that also has the
records extension and holds only whole turns. Both schemas have the change
feed, so the change routes answer. Every answer is compared as bytes with
the same three server-drawn values replaced, and the handshakes differ by
the one capability key, last in the bytes.
"""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests.contract.records.test_legacy_bytes import compared
from tests.contract.support import Binary, serve

pytestmark = pytest.mark.postgres
RECORD_TABLES = ["turn_records", "record_turns", "record_drafts", "record_staging"]


def test_every_legacy_route_answers_the_bytes_of_a_plain_schema(
    binary: Binary,
) -> None:
    dsn = os.environ.get("NEOSIAN_TEST_POSTGRES_DSN", "")
    assert dsn, "select this tier only with NEOSIAN_TEST_POSTGRES_DSN"
    plain = "neosian_legacy_plain_" + uuid4().hex
    kept = "neosian_legacy_kept_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        try:
            for name in (plain, kept):
                layouts = ["postgres", "changes-postgres"]
                if name == kept:
                    layouts.append("records-postgres")
                for layout in layouts:
                    ddl = binary.run("schema", layout, "--schema", name)
                    assert ddl.returncode == 0, ddl.stderr
                    connection.execute(ddl.stdout)
            with (
                serve(binary, postgres_dsn=dsn, schema=plain) as without,
                serve(binary, postgres_dsn=dsn, schema=kept) as with_records,
            ):
                handshake = compared(without, with_records)
                assert b'"backend":"PostgresStore"' in handshake[1]
            # The schema that keeps records still holds whole turns only.
            for table in RECORD_TABLES:
                count = connection.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier(kept), sql.Identifier(table)
                    )
                ).fetchone()
                assert count == (0,), table
        finally:
            for name in (plain, kept):
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(name)
                    )
                )
