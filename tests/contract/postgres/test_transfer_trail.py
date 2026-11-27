"""A transfer carries a conversation's whole erasure trail in storage order on
PostgreSQL (the T1e2d slice of the T1e2 plan, DESIGN §40.54 item 22): the
archive of `tests/contract/test_transfer_trail.py`, more than 500 acts with
times that go backwards and a tie across the old 500 boundary, is imported
after acts the schema already held and exported again through the CLI."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.contract.support import Binary, Server
from tests.contract.test_transfer_trail import (
    COUNT,
    ID,
    PRIOR,
    acts,
    archive,
    done,
    same_acts,
    trail_of,
    transfer,
)

pytestmark = pytest.mark.postgres

Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]


def stored(link: psycopg.Connection[tuple[object, ...]], name: str) -> list[Any]:
    query = sql.SQL(
        "SELECT actor, created_at, turns FROM {}.turn_redactions"
        " WHERE conversation_id = %s ORDER BY id"
    ).format(sql.Identifier(name))
    return list(link.execute(query, (ID,)).fetchall())


def test_postgres_import_and_export_keep_every_act_in_storage_order(
    database: Database, binary: Binary
) -> None:
    link, name, _ = database
    env = {"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    seeded = acts()
    prior = archive(binary.directory / "prior", PRIOR, turns=0)
    source = archive(binary.directory / "archive", seeded)
    schema = ("--schema", name)
    seed = ("import", str(prior), *schema, "--conversation", ID)
    assert [u["redactions"] for u in done(transfer(binary, *seed, **env))] == [2]
    units = done(transfer(binary, "import", str(source), *schema, **env))
    assert [(u["name"], u["turns"], u["redactions"]) for u in units] == [(ID, 4, COUNT)]
    expected = PRIOR + seeded
    assert stored(link, name) == [
        (
            row["actor"],
            datetime.fromisoformat(row["created_at"]),
            row["turns"],
        )
        for row in expected
    ]
    exported = binary.directory / "exported"
    units = done(transfer(binary, "export", str(exported), *schema, **env))
    assert [(u["name"], u["turns"], u["redactions"]) for u in units] == [
        (ID, 4, COUNT + 2)
    ]
    same_acts(trail_of(exported), expected)
