"""The fold operator restores legacy rows and their existing read/search surfaces."""

import json
import os
from subprocess import CompletedProcess
from typing import Any

import pytest

from tests.contract.file.test_records import MESSAGES, RECORDS, STAMP, record
from tests.contract.file.test_records_fold import answer
from tests.contract.postgres.fold_fixture import draft, restore_unit, snapshot
from tests.contract.postgres.history_fixture import postgres
from tests.contract.postgres.test_records_extension import Database, install
from tests.contract.postgres.test_records_migrate import (
    legacy,
    messages,
    migrate,
    parent,
    rows,
)
from tests.contract.postgres.test_search_bounds import search
from tests.contract.profile_support import run
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres


def fold(
    binary: Binary,
    database: Database,
    *ids: str,
    discard: bool = False,
    **settings: str,
) -> CompletedProcess[str]:
    flags = [arg for name in ids for arg in ("--conversation", name)]
    if discard:
        flags.append("--discard-pending")
    return run(
        binary, "records", "fold", "--schema", database[1], "--json", *flags,
        env={"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"], **settings},
    )  # fmt: skip


def views(server: Server, session: str) -> dict[str, Any]:
    args = {"conversation_id": session}
    found = {
        "read": server.post("conversation/read_turns", **args),
        "project": server.post("conversation/read_projections", **args),
        "acts": server.post("conversation/turn_redactions", conversations=[session]),
    }
    for term, expected in [
        ("Bash", [8]),
        ("needle", [4, 5]),
        ("oldindex", [13]),
        ("hidden", []),
    ]:
        for kind in ["newest", "relevance"]:
            hits = server.post(
                "conversation/search_turns",
                conversations=[session],
                query=term,
                order=kind,
            )
            assert sorted(t["turn"] for t in hits["turns"]) == expected
            found[kind + "-" + term] = hits
    return found


def test_sparse_restored_and_migrated_turns_fold_and_preserve_public_history(
    binary: Binary, database: Database
) -> None:
    install(database, binary)
    session = "c-fold-sparse"
    parent(database, session)
    legacy(database, session, 4, messages("needle migrated"), "needle migrated")
    before_migration = rows(
        database, "SELECT to_jsonb(t)::text FROM {s}.turns t WHERE turn=4"
    )
    assert migrate(binary, database, session).returncode == 0
    restore_unit(
        database,
        session,
        {5: [record(0, 0, "user", "prompt", "needle restored")], 8: RECORDS},
    )
    legacy(database, session, 9, "[]", None, redacted=True)
    legacy(database, session, 13, messages("history body"), "oldindex")
    legacy(database, session, 15, messages("hidden"), None)
    rows(
        database,
        "INSERT INTO {s}.projections (conversation_id,turn,kind,text,span) VALUES (%s,8,'epoch','selection',4)",
        session,
    )
    rows(
        database,
        "INSERT INTO {s}.turn_redactions (conversation_id,turns,actor,created_at) VALUES (%s,ARRAY[9],'cli:planted',%s)",
        session,
        STAMP,
    )
    unaffected = "SELECT to_jsonb(t)::text,ctid::text,xmin::text FROM {s}.turns t WHERE turn>=9 ORDER BY turn"
    retained = rows(database, unaffected)
    held = snapshot(database)
    cli = search(binary, database[1], "needle", "--conversation", session)
    assert cli.returncode == 0
    with postgres(binary, database) as server:
        asked = views(server, session)
        assert [t["turn"] for t in asked["read"]["turns"]] == [4, 5, 8, 9, 13, 15]
        assert asked["read"]["turns"][2]["messages"] == MESSAGES
        done = fold(binary, database, session)
        assert done.returncode == 0, done.stderr
        assert done.stdout == answer(session, 3, 2, skeletons=1)
        assert views(server, session) == asked
    assert rows(database, unaffected) == retained
    assert (
        rows(database, "SELECT to_jsonb(t)::text FROM {s}.turns t WHERE turn=4")
        == before_migration
    )
    assert rows(
        database,
        "SELECT turn,neosian_format,messages,search_text,actor FROM {s}.turns WHERE turn IN (5,8) ORDER BY turn",
    ) == [
        (
            5,
            1,
            json.loads(messages("needle restored")),
            "needle restored",
            "restore:oracle",
        ),
        (
            8,
            1,
            MESSAGES,
            'héllo\nBash {"command":"ls"}\na.txt\nb.txt\nTwo files.',
            "restore:oracle",
        ),
    ]
    after = snapshot(database)
    for table in [
        "conversations",
        "projections",
        "turn_redactions",
        "record_drafts",
        "record_staging",
    ]:
        assert after[table] == held[table]
    assert after["turn_records"] == after["record_turns"] == []
    repeat = fold(binary, database, session)
    assert (repeat.returncode, repeat.stdout) == (0, answer(session, 0, 5, skeletons=1))
    assert snapshot(database) == after
    assert (
        search(binary, database[1], "needle", "--conversation", session).stdout
        == cli.stdout
    )
    with postgres(binary, database) as fresh:
        assert views(fresh, session) == asked


def test_fold_selection_counts_pending_kinds_keeps_committed_and_validates_all_names(
    binary: Binary, database: Database
) -> None:
    plain = fold(binary, database, NEOSIAN_MAX_TURN_BYTES="9")
    assert (
        plain.returncode == 1
        and "records are not enabled on this store" in plain.stderr
    )
    assert "NEOSIAN_MAX_TURN_BYTES" not in plain.stderr
    install(database, binary)
    setting = fold(binary, database, NEOSIAN_MAX_TURN_BYTES="9")
    assert setting.returncode == 1 and "NEOSIAN_MAX_TURN_BYTES" in setting.stderr
    empty = fold(binary, database)
    assert (empty.returncode, empty.stdout) == (0, "")
    for name in ["Bare", "live"]:
        parent(database, name)
    legacy(database, "live", 7, messages("whole"), "whole")
    for name, kind, ident in [
        ("Stage", "restore", "p"),
        ("Mixed", "write", "one"),
        ("Mixed", "restore", "one"),
        ("Mixed", "write", "two"),
        ("Mixed", "restore", "two"),
        ("Mixed", "restore", "three"),
    ]:
        draft(database, name, kind, ident)
    # A committed empty import has durable identity even with no visible turn.
    draft(database, "Receipt", "restore", "kept", committed=True)
    old = snapshot(database)
    for bad in ["mixed", "missing", "../bad"]:
        refuse = fold(binary, database, "Mixed", bad, discard=True)
        assert refuse.returncode == 1
        why = (
            "agent_conversation_id_invalid"
            if bad == "../bad"
            else f"no conversation {bad} in this store; nothing was folded"
        )
        assert why in json.loads(refuse.stdout)["error"]
        assert snapshot(database) == old
    ordered = fold(binary, database, "live", "Receipt", "Bare", "live")
    assert (ordered.returncode, ordered.stdout) == (
        0,
        answer("live", 0, 1) + answer("Receipt", 0, 0) + answer("Bare", 0, 0),
    )
    for name, writes, restores in [("Mixed", 2, 3), ("Stage", 0, 1)]:
        refuse = fold(binary, database, name)
        assert refuse.returncode == 1
        assert (
            f"{writes} open drafts, {restores} staged restores"
            in json.loads(refuse.stdout)["error"]
        )
        assert snapshot(database) == old
    all_rows = fold(binary, database, discard=True)
    assert all_rows.returncode == 0, all_rows.stderr
    assert all_rows.stdout == "".join(
        [
            answer("Bare", 0, 0),
            answer("Mixed", 0, 0, drafts=2, restores=3),
            answer("Receipt", 0, 0),
            answer("Stage", 0, 0, restores=1),
            answer("live", 0, 1),
        ]
    )
    assert rows(
        database, "SELECT conversation_id,kind,name,state FROM {s}.record_drafts"
    ) == [("Receipt", "restore", "kept", "committed")]
    assert rows(database, "SELECT owner FROM {s}.record_staging") == []
    assert rows(database, "SELECT owner FROM {s}.turn_records") == []
    assert rows(
        database,
        "SELECT conversation_id FROM {s}.conversations ORDER BY conversation_id",
    ) == [("Bare",), ("live",)]


def test_late_oversize_or_bad_digest_undoes_prior_fold_and_requested_discard(
    binary: Binary, database: Database
) -> None:
    install(database, binary)
    session = "c-atomic"
    large = [
        record(0, 0, "user", "prompt", "x" * 65_534, size=70_000),
        record(1, 0, "user", "prompt", "x" * 4_466, content=None, chunk=1, size=70_000),
    ]
    restore_unit(
        database,
        session,
        {1: [record(0, 0, "user", "prompt", "early small")], 9: large},
    )
    draft(database, session, "write", "open")
    draft(database, session, "restore", "open")
    draft(database, "untouched", "restore", "open")
    before = snapshot(database)
    capped = fold(
        binary, database, session, discard=True, NEOSIAN_MAX_TURN_BYTES="65536"
    )
    assert capped.returncode == 1
    assert (
        "turn 9 of c-atomic holds 70000 > 65536 bytes"
        in json.loads(capped.stdout)["error"]
    )
    assert snapshot(database) == before
    original = rows(
        database, "SELECT digest FROM {s}.turn_records WHERE part=9 AND seq=1"
    )[0][0]
    rows(
        database,
        "UPDATE {s}.turn_records SET digest=decode(repeat('ff',32),'hex') WHERE part=9 AND seq=1",
    )
    damaged = snapshot(database)
    bad = fold(binary, database, session, discard=True)
    assert (
        bad.returncode == 1
        and "agent_conversation_format_unsupported" in json.loads(bad.stdout)["error"]
    )
    assert snapshot(database) == damaged
    rows(
        database,
        "UPDATE {s}.turn_records SET digest=%s WHERE part=9 AND seq=1",
        original,
    )
    good = fold(binary, database, session, discard=True)
    assert good.returncode == 0, good.stderr
    assert good.stdout == answer(session, 2, 0, drafts=1, restores=1)
    assert rows(
        database,
        "SELECT conversation_id,name,state FROM {s}.record_drafts ORDER BY conversation_id",
    ) == [(session, "backup", "committed"), ("untouched", "open", "open")]
    assert rows(database, "SELECT conversation_id FROM {s}.turn_records") == [
        ("untouched",)
    ]
    assert rows(database, "SELECT conversation_id FROM {s}.record_staging") == [
        ("untouched",)
    ]
    with postgres(binary, database) as server:
        got = server.post("conversation/read_turns", conversation_id=session)["turns"]
        assert [(t["turn"], t["messages"][0]["content"]) for t in got] == [
            (1, "early small"),
            (9, "x" * 70_000),
        ]


def test_fold_keeps_opaque_large_numbers_and_checks_every_legacy_message(
    binary: Binary, database: Database
) -> None:
    install(database, binary)
    session = "c-legacy-validation"
    parent(database, session)

    # PostgreSQL JSONB accepts this finite numeric value. Feed its lexeme to
    # PostgreSQL directly so Python's float codec never rounds it to infinity.
    def message(extra: str) -> str:
        return '{"role":"user","content":"archive","extra":' + extra + "}"

    enormous = message('{"amount":1e400}')
    legacy(database, session, 1, "[" + enormous + "]", "older search index")
    read = "SELECT messages::text,search_text,ctid::text,xmin::text FROM {s}.turns ORDER BY turn"
    first = rows(database, read)
    assert str(first[0][0]).count("0") >= 400
    kept = fold(binary, database, session)
    assert (kept.returncode, kept.stdout) == (0, answer(session, 0, 1))
    assert rows(database, read) == first

    for name, body in [
        ("bad-sibling", "[" + enormous + ',{"role":"invalid","content":"x"}]'),
        ("too-deep", "[" + message("[" * 160 + "0" + "]" * 160) + "]"),
    ]:
        parent(database, name)
        legacy(database, name, 3, body, "opaque")
        original = rows(database, read)
        rejected = fold(binary, database, name)
        assert rejected.returncode == 1, (name, rejected.stdout)
        assert (
            "agent_conversation_format_unsupported"
            in json.loads(rejected.stdout)["error"]
        )
        assert rows(database, read) == original
