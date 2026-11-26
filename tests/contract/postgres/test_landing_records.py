"""Turns land as records in parts on Postgres with records extension 1 (DESIGN
§40.54 items 7, 9 and 17.3; the T1e1b slice of the T1e1 plan): a Stop writes
its turn as one draft and seals it, `read_turns` answers it as a schema
without records answers its `append_turn` turn, a turn of 2,100 tool rounds
lands whole, and a seal whose answer was lost is finished once. The FileStore twins are in
`tests/contract/landing/test_local_records.py`."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests.contract.landing.test_local_records import SESSION, STAMP, raw_turns
from tests.contract.support import Binary, Server, serve

pytestmark = pytest.mark.postgres
Connection = psycopg.Connection[tuple[object, ...]]
Database = tuple[Connection, str, Server]


def dsn() -> str:
    return os.environ["NEOSIAN_TEST_POSTGRES_DSN"]


def hook(binary: Binary, schema: str, name: str, **payload: Any) -> Any:
    result = subprocess.run(
        [str(binary.executable), "record", "--schema", schema]
        + ["--scope", "user:contract", "--json"],
        input=json.dumps({"session_id": SESSION, "hook_event_name": name, **payload}),
        env={**binary.environment(), "NEOSIAN_POSTGRES_DSN": dsn()},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def install_records(connection: Connection, binary: Binary, schema: str) -> None:
    ddl = binary.run("schema", "records-postgres", "--schema", schema)
    assert ddl.returncode == 0, ddl.stderr
    connection.execute(ddl.stdout)


@contextmanager
def plain_schema(connection: Connection, binary: Binary) -> Iterator[str]:
    """A second schema 4 without records, dropped afterwards."""
    name = "neosian_landing_" + uuid4().hex
    ddl = binary.run("schema", "postgres", "--schema", name)
    assert ddl.returncode == 0, ddl.stderr
    connection.execute(ddl.stdout)
    try:
        yield name
    finally:
        connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
        )


def rows(connection: Connection, query: str, schema: str) -> list[tuple[object, ...]]:
    statement = sql.SQL(query).format(s=sql.Identifier(schema))
    return connection.execute(statement, (SESSION,)).fetchall()


def test_a_stop_lands_records_that_read_as_the_append_turn_twin(
    database: Database, binary: Binary
) -> None:
    connection, name, _ = database
    install_records(connection, binary, name)
    with plain_schema(connection, binary) as twin:
        for schema in (name, twin):
            hook(binary, schema, "UserPromptSubmit", prompt='build "it" \\ é 中 😀')
            for n in range(2):
                hook(
                    binary,
                    schema,
                    "PostToolUse",
                    tool_name="Bash",
                    tool_use_id=f"toolu_{n}",
                    tool_input={"command": f"make part-{n}"},
                    tool_response=f'line {n} \t é 中 😀 "quoted"\n' * 30_000,
                )
            landed = hook(binary, schema, "Stop", last_assistant_message="built")
            assert (landed["disposition"], landed["turn"]) == ("recorded", 1)
        formats = (
            "SELECT turn, neosian_format FROM {s}.turns WHERE conversation_id = %s"
        )
        assert rows(connection, formats, name) == [(1, 2)]
        assert rows(connection, formats, twin) == [(1, 1)]
        ((turn, records, total, draft),) = rows(
            connection,
            "SELECT turn, records, bytes, draft FROM {s}.record_turns"
            " WHERE conversation_id = %s",
            name,
        )
        assert (turn, isinstance(draft, str)) == (1, True)
        assert isinstance(total, int) and total > 983_040
        stored = "SELECT count(*) FROM {s}.turn_records WHERE conversation_id = %s"
        assert rows(connection, stored, name) == [(records,)]
        answers = []
        for schema in (name, twin):
            with serve(binary, postgres_dsn=dsn(), schema=schema) as server:
                answers.append(raw_turns(server))
        assert STAMP.sub(b'"created_at":""', answers[0]) == STAMP.sub(
            b'"created_at":""', answers[1]
        )


def test_a_turn_of_2100_tool_rounds_lands_whole(
    database: Database, binary: Binary
) -> None:
    connection, name, _ = database
    install_records(connection, binary, name)
    span = [{"kind": "prompt", "text": "many rounds"}] + [
        {
            "kind": "tool",
            "id": f"call-{n}",
            "name": "Bash",
            "input": {"command": f"echo {n}"},
            "response": str(n),
        }
        for n in range(2_100)
    ]
    spool = binary.home / "spool"
    spool.mkdir(parents=True)
    (spool / f"{SESSION}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in span)
    )
    landed = hook(binary, name, "Stop", last_assistant_message="all done")
    assert (landed["disposition"], landed["turn"]) == ("recorded", 1)
    mapping = "SELECT turn, records FROM {s}.record_turns WHERE conversation_id = %s"
    assert rows(connection, mapping, name) == [(1, 4_202)]
    with serve(binary, postgres_dsn=dsn(), schema=name) as server:
        (turn,) = server.post("conversation/read_turns", conversation_id=SESSION)[
            "turns"
        ]
    messages = turn["messages"]
    assert len(messages) == 4_202
    assert [messages[0]["content"], messages[-1]["content"]] == [
        "many rounds",
        "all done",
    ]
    assert messages[-2]["content"] == "2099"


def test_a_seal_whose_answer_was_lost_is_finished_and_stored_once(
    database: Database, binary: Binary
) -> None:
    """The draft is sealed in the store while its turn waits in the queue,
    as when a seal's answer never reached the lander: the next run's probe
    answers `sealed`, and the turn is finished without being stored twice."""
    import fcntl

    connection, name, _ = database
    install_records(connection, binary, name)
    hook(binary, name, "UserPromptSubmit", prompt="sealed before")
    pending = binary.home / "spool" / "pending" / SESSION
    pending.mkdir(parents=True)
    (pending / ".lock").write_bytes(b"\0")
    with (pending / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        queued = hook(binary, name, "Stop", last_assistant_message="its answer")
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert queued["disposition"] == "pending"
    state = json.loads((pending / "1.state").read_text())
    texts = [("user", "prompt", "sealed before"), ("assistant", "reply", "its answer")]
    records = [
        {"seq": n, "message": n, "role": role, "kind": kind, "content": "text"}
        | {"chunk": 0, "size": len(text.encode()), "text": text}
        for n, (role, kind, text) in enumerate(texts)
    ]
    with serve(binary, postgres_dsn=dsn(), schema=name) as server:
        sealed = server.post(
            "conversation/write_records",
            conversation_id=SESSION,
            draft=state["draft"],
            actor=state["actor"],
            turn_bytes=sum(r["size"] for r in records),
            seal=True,
            records=records,
        )
        assert (sealed["state"], sealed["turn"]["turn"]) == ("sealed", 1)
        hook(binary, name, "UserPromptSubmit", prompt="after")
        landed = hook(binary, name, "Stop", last_assistant_message="done")
        assert (landed["disposition"], landed["turn"]) == ("recorded", 2)
        turns = server.post("conversation/read_turns", conversation_id=SESSION)
    assert [[m["content"] for m in t["messages"]] for t in turns["turns"]] == [
        ["sealed before", "its answer"],
        ["after", "done"],
    ]
    mapping = (
        "SELECT turn, draft FROM {s}.record_turns WHERE conversation_id = %s"
        " ORDER BY turn"
    )
    assert rows(connection, mapping, name)[0] == (1, state["draft"])
    assert not pending.exists()
