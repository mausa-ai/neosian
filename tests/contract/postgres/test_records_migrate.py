"""Lossless PostgreSQL record migration through the documented local command."""

import hashlib
import json
import os
from subprocess import CompletedProcess
from typing import Any, LiteralString

import pytest
from psycopg import sql

from tests.contract.file.test_records import MESSAGES, RECORDS, STAMP, WHOLE, record
from tests.contract.file.test_records_migrate import answer, owner
from tests.contract.postgres.history_fixture import native, postgres
from tests.contract.postgres.test_records_extension import Database, install
from tests.contract.postgres.test_search_bounds import search
from tests.contract.profile_support import run
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres


def rows(
    database: Database, statement: LiteralString, *args: object
) -> list[tuple[object, ...]]:
    connection, name, _ = database
    query = sql.SQL(statement).format(s=sql.Identifier(name))
    result = connection.execute(query, args)
    return result.fetchall() if result.description else []


def migrate(
    binary: Binary, database: Database, *names: str, **settings: str
) -> CompletedProcess[str]:
    flags = [arg for name in names for arg in ("--conversation", name)]
    return run(
        binary, "records", "migrate", "--schema", database[1], "--json", *flags,
        env={"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"], **settings},
    )  # fmt: skip


def parent(database: Database, session: str) -> None:
    rows(database, "INSERT INTO {s}.conversations VALUES (%s,%s)", session, STAMP)


def legacy(
    database: Database,
    session: str,
    number: int,
    messages: str,
    index: str | None,
    *,
    redacted: bool = False,
) -> None:
    rows(
        database,
        "INSERT INTO {s}.turns "
        "(conversation_id,turn,messages,search_text,created_at,actor,redacted) "
        "VALUES (%s,%s,%s::jsonb,%s,%s,'cli:planted',%s)",
        session,
        number,
        messages,
        index,
        STAMP,
        redacted,
    )


def messages(text: str) -> str:
    return json.dumps([{**WHOLE["messages"][0], "content": text}])


def test_selection_validates_all_operands_then_uses_stored_or_visible_names(
    binary: Binary, database: Database
) -> None:
    bad = {"NEOSIAN_MAX_RECORD_TURN_BYTES": "7"}
    plain = migrate(binary, database, **bad)
    assert plain.returncode == 1
    assert "records are not enabled on this store" in plain.stderr
    assert "NEOSIAN_MAX_RECORD_TURN_BYTES" not in plain.stderr
    install(database, binary)
    invalid_setting = migrate(binary, database, **bad)
    assert (
        invalid_setting.returncode == 1
        and "NEOSIAN_MAX_RECORD_TURN_BYTES" in invalid_setting.stderr
    )
    for name in ("a-lower", "Bare", "B-visible"):
        parent(database, name)
    legacy(database, "a-lower", 8, messages("named"), "named")
    rows(
        database,
        "INSERT INTO {s}.projections (conversation_id,turn,kind,text) "
        "VALUES ('B-visible',9,'log','unchanged')",
    )
    for name, kind in [("Write", "write"), ("Restore", "restore")]:
        rows(
            database,
            "INSERT INTO {s}.record_drafts "
            "(conversation_id,kind,name,state,position,bytes,opened_at,touched_at) "
            "VALUES (%s,%s,'pending','open','{{}}',200,%s,%s)",
            name,
            kind,
            STAMP,
            STAMP,
        )
    pending = rows(
        database,
        "SELECT owner,position,bytes,ctid::text,xmin::text FROM "
        "{s}.record_drafts ORDER BY owner",
    )
    for name in ("A-lower", "Restore", "missing", "../x"):
        refused = migrate(binary, database, "a-lower", name)
        assert refused.returncode == 1
        why = (
            "agent_conversation_id_invalid"
            if name == "../x"
            else f"no conversation {name} in this store; nothing was migrated"
        )
        assert why in json.loads(refused.stdout)["error"]
        assert rows(database, "SELECT neosian_format FROM {s}.turns") == [(1,)]
    selected = migrate(binary, database, "Write", "a-lower", "Bare", "a-lower")
    assert selected.returncode == 0, selected.stderr
    assert selected.stdout == answer("Write", 0, 0) + answer("a-lower", 1, 0) + answer(
        "Bare", 0, 0
    )
    visible = migrate(binary, database)
    assert visible.returncode == 0, visible.stderr
    assert visible.stdout == answer("B-visible", 0, 0) + answer("a-lower", 0, 1)
    assert (
        rows(
            database,
            "SELECT owner,position,bytes,ctid::text,xmin::text FROM "
            "{s}.record_drafts ORDER BY owner",
        )
        == pending
    )


def views(server: Server, session: str) -> dict[str, Any]:
    result = {"turns": server.post("conversation/read_turns", conversation_id=session)}
    expected = {
        "héllo": [2],
        "needle": [9, 5],
        "oldindex": [8],
        "missingindex": [],
        "actualbody": [],
    }
    for query, hits in expected.items():
        for order in ("newest", "relevance"):
            found = server.post(
                "conversation/search_turns",
                query=query,
                conversations=[session],
                order=order,
            )
            assert sorted(t["turn"] for t in found["turns"]) == sorted(hits)
            result[f"{query}-{order}"] = found
    return result


def test_public_history_and_kept_tuple_bytes_survive_migration_and_replay(
    binary: Binary, database: Database
) -> None:
    install(database, binary)
    session = "c-lossless"
    parent(database, session)
    legacy(
        database,
        session,
        2,
        json.dumps(MESSAGES),
        'héllo\nBash {"command":"ls"}\na.txt\nb.txt\nTwo files.',
    )
    legacy(database, session, 3, "[]", None, redacted=True)
    legacy(database, session, 5, messages("needle alpha"), "needle alpha")
    legacy(database, session, 6, '[{"role":"user","content":"bare"}]', "bare")
    # Only a key inside a nested tool call is unknown to the message codec.
    nested = {
        **MESSAGES[1],
        "tool_calls": [{**MESSAGES[1]["tool_calls"][0], "unknown": {"preserve": [1]}}],
    }
    legacy(database, session, 7, json.dumps([nested]), 'Bash {"command":"ls"}')
    legacy(database, session, 8, messages("actualbody"), "oldindex")
    native(
        database, session, 9, STAMP, [record(0, 0, "user", "prompt", "needle native")]
    )
    legacy(database, session, 10, messages("missingindex"), None)
    for number, numeric, index in [
        (11, "1.00", "1.0"),
        (12, "1234567890.1234567890123456", "1234567890.1234567"),
    ]:
        # Pass original numeric lexemes directly to PostgreSQL's JSONB parser.
        encoded = json.dumps([MESSAGES[1]]).replace(
            '"command": "ls"', f'"n": {numeric}'
        )
        legacy(database, session, number, encoded, f'Bash {{"n":{index}}}')
    # A missing default must not hide a simultaneous loss of numeric precision.
    omitted = {k: v for k, v in MESSAGES[1].items() if k != "reasoning"}
    encoded = json.dumps([omitted]).replace(
        '"command": "ls"', '"n": 1234567890.1234567890123456'
    )
    legacy(database, session, 13, encoded, 'Bash {"n":1234567890.1234567}')
    retained = (
        "SELECT to_jsonb(t)::text,ctid::text,xmin::text FROM {s}.turns t "
        "WHERE turn NOT IN (2,5) ORDER BY turn"
    )
    initial = rows(database, retained)
    before_cli = search(binary, database[1], "needle", "--conversation", session)
    assert before_cli.returncode == 0, before_cli.stderr
    with postgres(binary, database) as server:
        before = views(server, session)
        first = migrate(binary, database, session)
        assert first.returncode == 0, first.stderr
        kept = {"skeletons": 1, "spelling": 2, "content": 5}
        assert first.stdout == answer(session, 2, 1, **kept)
        assert views(server, session) == before
    after_cli = search(binary, database[1], "needle", "--conversation", session)
    assert (after_cli.returncode, after_cli.stdout) == (0, before_cli.stdout)
    assert rows(database, retained) == initial
    mapping = rows(
        database,
        "SELECT turn,part,draft,records,bytes FROM {s}.record_turns "
        "WHERE turn IN (2,5) ORDER BY turn",
    )
    assert mapping == [
        (2, 2, owner(session), 4, sum(len(r["text"].encode()) for r in RECORDS)),
        (5, 5, owner(session), 1, len("needle alpha")),
    ]
    expected_records = [*RECORDS, record(0, 0, "user", "prompt", "needle alpha")]
    digests = [
        hashlib.sha256(
            json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode()
        ).digest()
        for r in expected_records
    ]
    stored = rows(
        database,
        "SELECT digest,last FROM {s}.turn_records WHERE part IN (2,5) ORDER BY part,seq",
    )
    assert stored == [(digest, True) for digest in digests]
    physical = rows(
        database,
        "SELECT part,seq,ctid::text,xmin::text FROM {s}.turn_records ORDER BY part,seq",
    )
    second = migrate(binary, database, session)
    assert second.returncode == 0 and second.stdout == answer(session, 0, 3, **kept)
    assert (
        rows(
            database,
            "SELECT part,seq,ctid::text,xmin::text FROM {s}.turn_records ORDER BY part,seq",
        )
        == physical
    )
    with postgres(binary, database) as fresh:
        assert views(fresh, session) == before
        assert fresh.post(
            "conversation/redact_turns", conversation_id=session, turns=[5]
        ) == {"count": 1}
        assert [
            t["turn"]
            for t in fresh.post("conversation/search_turns", query="needle")["turns"]
        ] == [9]
        survivor = fresh.post(
            "conversation/read_turns", conversation_id=session, after=1, limit=1
        )
        assert survivor["turns"][0]["messages"] == MESSAGES
    assert rows(
        database,
        "SELECT part,seq,ctid::text,xmin::text FROM {s}.turn_records ORDER BY part,seq",
    ) == [r for r in physical if r[0] != 5]


def test_chunked_turn_over_whole_limit_migrates_and_over_record_limit_or_collision_stays(
    binary: Binary, database: Database
) -> None:
    install(database, binary)
    session = "c-bounds"
    parent(database, session)
    for turn, text in [(1, "x" * 70_000), (2, "é" * 35_001), (3, "occupied")]:
        legacy(database, session, turn, messages(text), text)
    rows(
        database,
        "INSERT INTO {s}.record_turns VALUES (%s,3,900,3,'occupied',1,8,%s)",
        session,
        STAMP,
    )
    preserved = "SELECT to_jsonb(t)::text,ctid::text,xmin::text FROM {s}.turns t WHERE turn>1 ORDER BY turn"
    before = rows(database, preserved)
    migrated = migrate(
        binary,
        database,
        NEOSIAN_MAX_TURN_BYTES="65536",
        NEOSIAN_MAX_RECORD_TURN_BYTES="70000",
    )
    assert migrated.returncode == 0, migrated.stderr
    assert migrated.stdout == answer(session, 1, 0, size=1, number=1)
    assert rows(database, preserved) == before
    assert rows(
        database,
        "SELECT seq,chunk,size,octet_length(text),last FROM {s}.turn_records ORDER BY seq",
    ) == [
        (0, 0, 70_000, 65_534, False),
        (1, 1, 70_000, 4_466, True),
    ]
    with postgres(binary, database) as server:
        page = server.post("conversation/read_turns", conversation_id=session, limit=1)
        assert page["turns"][0]["messages"][0]["content"] == "x" * 70_000


@pytest.mark.parametrize(
    ("version", "body", "detail"),
    [
        (3, "[]", "unsupported format 3"),
        (2, "[]", "record mapping"),
        (1, "{}", "message array"),
    ],
)
def test_unreadable_header_refuses_without_converting_later_turns(
    binary: Binary, database: Database, version: int, body: str, detail: str
) -> None:
    install(database, binary)
    session = "c-unreadable"
    parent(database, session)
    legacy(database, session, 4, body, None)
    rows(database, "UPDATE {s}.turns SET neosian_format=%s WHERE turn=4", version)
    legacy(database, session, 5, messages("later"), "later")
    snapshots = (
        "SELECT to_jsonb(t)::text,ctid::text,xmin::text FROM {s}.turns t ORDER BY turn"
    )
    before = rows(database, snapshots)
    refused = migrate(binary, database, session)
    assert refused.returncode == 1
    assert (
        "agent_conversation_format_unsupported" in json.loads(refused.stdout)["error"]
    )
    assert detail in refused.stderr
    assert rows(database, snapshots) == before
    assert rows(database, "SELECT owner FROM {s}.turn_records") == []
