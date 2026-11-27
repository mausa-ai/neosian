"""The synthetic home imports fully through `neosian import DIR --url` into a
PostgreSQL store with records extension 1 and reads back identically (the
T1gb slice of the T1f plan, DESIGN §40.54 item 16.5).

The checks are those of `records/test_synthetic_home.py`, on two fresh
schemas of the test database: one with the records extension, served to
the import through the meter, and its twin without it, which receives the
same archive through the legacy import. The whole erasure trail is read in
stored order from each schema's `turn_redactions` table. The one-tenth
profile runs here by default; `NEOSIAN_SYNTHETIC_PROFILE=full` runs the
full one, and `NEOSIAN_SYNTHETIC_REPORT=FILE` appends its costs to FILE.

`read_turns` from the store that keeps records is the archive's bytes, as
on files. The twin is compared as parsed JSON (ruling K6 of the T1f plan):
a whole turn stored without records is kept as `jsonb`, which reorders the
keys of nested objects, such as a tool call's arguments, shorter key first.
One case pins that difference itself with the smallest input, so a change
in either direction is seen.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests.contract.records.differential import (
    Home,
    Starter,
    Trail,
    check_entries_and_trails,
    check_full_view,
    check_memory,
    check_read_turns,
    check_units,
    imported_home,
    post_raw,
    run_import,
)
from tests.contract.records.synthetic import message
from tests.contract.support import Binary, Server, executable, serve

pytestmark = [pytest.mark.postgres, pytest.mark.timeout(3_600)]
Link = psycopg.Connection[tuple[object, ...]]
ARGUMENTS = {"file_path": "a", "content": "b"}
AS_WRITTEN = b'"arguments":{"file_path":"a","content":"b"}'
REORDERED = b'"arguments":{"content":"b","file_path":"a"}'


@contextmanager
def schemas(link: Link, binary: Binary) -> Iterator[tuple[str, str]]:
    """A fresh schema with records extension 1 and its twin without it,
    dropped afterwards."""
    name = "neosian_synthetic_" + uuid4().hex
    pair = (name, name + "_twin")
    try:
        for schema in pair:
            ddl = binary.run("schema", "postgres", "--schema", schema)
            assert ddl.returncode == 0, ddl.stderr
            link.execute(ddl.stdout)
        ddl = binary.run("schema", "records-postgres", "--schema", name)
        assert ddl.returncode == 0, ddl.stderr
        link.execute(ddl.stdout)
        yield pair
    finally:
        for schema in pair:
            drop = sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE")
            link.execute(drop.format(sql.Identifier(schema)))


def dsn() -> str:
    found = os.environ.get("NEOSIAN_TEST_POSTGRES_DSN", "")
    assert found, "select this tier only with NEOSIAN_TEST_POSTGRES_DSN"
    return found


def binaries(base: Path) -> tuple[Binary, Binary]:
    made = Binary(executable(), base / "kept"), Binary(executable(), base / "twin")
    for binary in made:
        binary.directory.mkdir()
    return made


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Home]:
    base = tmp_path_factory.mktemp("synthetic")
    kept, legacy = binaries(base)
    with psycopg.connect(dsn(), autocommit=True) as link:

        def starter(schema: str) -> Starter:
            def start(binary: Binary) -> AbstractContextManager[Server]:
                return serve(binary, postgres_dsn=dsn(), schema=schema)

            return start

        def trail(schema: str) -> Trail:
            query = sql.SQL(
                "SELECT created_at, actor, turns FROM {}.turn_redactions"
                " WHERE conversation_id = %s ORDER BY id"
            ).format(sql.Identifier(schema))

            def read(conversation: str) -> list[tuple[datetime, str | None, list[int]]]:
                found = link.execute(query, (conversation,)).fetchall()
                return [(when, actor, list(turns)) for when, actor, turns in found]

            return read

        with (
            schemas(link, kept) as (name, twin),
            imported_home(
                base,
                "postgres",
                (kept, starter(name), trail(name)),
                (legacy, starter(twin), trail(twin)),
                twin_bytes=False,
            ) as imported,
        ):
            yield imported


def test_the_import_reports_every_unit_and_leaves_the_archive_intact(
    home: Home,
) -> None:
    check_units(home)


def test_every_conversation_reads_back_in_the_full_view_and_its_digest(
    home: Home,
) -> None:
    check_full_view(home)


def test_read_turns_sends_the_archive_bytes_and_the_twins_json(home: Home) -> None:
    check_read_turns(home)


def test_projections_and_the_whole_trail_match_on_both_stores(home: Home) -> None:
    check_entries_and_trails(home)


def test_memory_scopes_read_back_on_both_stores(home: Home) -> None:
    check_memory(home)


def test_only_the_twin_reorders_the_keys_of_a_nested_object(tmp_path: Path) -> None:
    """The smallest input of the finding: one turn whose call arguments are
    `{"file_path":"a","content":"b"}`, imported into both schemas. The store
    that keeps records answers the arguments as written; the twin answers
    them in `jsonb` order; both answer the same JSON."""
    archive = tmp_path / "archive" / "conversations" / "key-order"
    archive.mkdir(parents=True)
    call = {"id": "t1", "name": "Write", "arguments": ARGUMENTS}
    messages = [
        message("user", "go"),
        message("assistant", None, [call]),
        message("tool", "ok", call_id="t1"),
    ]
    row = {"neosian_format": 1, "turn": 1, "messages": messages}
    row |= {"created_at": "2026-09-01T10:00:00Z", "actor": None, "redacted": False}
    (archive / "turns.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    kept, legacy = binaries(tmp_path)
    answers = []
    with psycopg.connect(dsn(), autocommit=True) as link, schemas(link, kept) as pair:
        for binary, schema in zip((kept, legacy), pair, strict=True):
            with serve(binary, postgres_dsn=dsn(), schema=schema) as server:
                run_import(binary, archive.parent.parent, server.url)
                body = {"conversation_id": "key-order"}
                answers.append(post_raw(server.url, "conversation/read_turns", body))
    assert AS_WRITTEN in answers[0] and REORDERED not in answers[0]
    assert REORDERED in answers[1] and AS_WRITTEN not in answers[1]
    assert json.loads(answers[0]) == json.loads(answers[1])
