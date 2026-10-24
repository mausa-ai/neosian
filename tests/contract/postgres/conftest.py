"""Every case owns and drops one unique database schema, never existing data."""

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests.contract.support import Binary, Server, serve


@pytest.fixture
def database(
    binary: Binary,
) -> Iterator[tuple[psycopg.Connection[tuple[object, ...]], str, Server]]:
    dsn = os.environ.get("NEOSIAN_TEST_POSTGRES_DSN", "")
    assert dsn, "select this tier only with NEOSIAN_TEST_POSTGRES_DSN"
    name = "neosian_corruption_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        ddl = binary.run("schema", "postgres", "--schema", name)
        assert ddl.returncode == 0, ddl.stderr
        connection.execute(ddl.stdout)
        try:
            with serve(binary, postgres_dsn=dsn, schema=name) as server:
                yield connection, name, server
        finally:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
            )
