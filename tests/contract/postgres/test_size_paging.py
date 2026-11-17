"""Spooled answers and size paging on Postgres (S14, DESIGN §40.46, #417).

A spooled read charges each row alone against `NEOSIAN_MAX_RESPONSE_BYTES`,
so answers 2.16.0 served are served (one row over the budget is still the
400 it names); reads whose rows are collected in memory keep S13's total
charge. The answer is spooled before it is sent, so a slow reader holds no
pooled connection and leaves no backend idle in transaction. The binary's
client sends searches unpaged. Rows are planted directly.
"""

import json
import os
import socket
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import psycopg
import pytest
from psycopg import sql

from tests.contract.profile_support import run
from tests.contract.support import ADMIN, Binary, Server, serve

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]
LONG = "pg-long"
BUDGET = {"NEOSIAN_MAX_RESPONSE_BYTES": "200000"}


def plant(
    connection: psycopg.Connection[tuple[object, ...]],
    schema: str,
    count: int = 40,
    words: int = 2000,
) -> None:
    name = sql.Identifier(schema)
    connection.execute(
        sql.SQL(
            "INSERT INTO {}.conversations VALUES (%s, '2026-11-01T00:00:00Z')"
        ).format(name),
        (LONG,),
    )
    rows = []
    for number in range(1, count + 1):
        text = f"long dashboard turn {number} " + "word " * words
        message: dict[str, Any] = {
            "role": "user",
            "content": text,
            "reasoning": None,
            "tool_calls": [],
            "tool_call_id": None,
        }
        stamp = f"2026-11-02T09:{number // 60:02d}:{number % 60:02d}Z"
        rows.append((LONG, number, json.dumps([message]), stamp, text))
    with connection.cursor() as cursor:
        cursor.executemany(
            sql.SQL(
                "INSERT INTO {}.turns (conversation_id, turn, messages, created_at,"
                " search_text) VALUES (%s, %s, %s::jsonb, %s, %s)"
            ).format(name),
            rows,
        )


def raw(server: Server, route: str, body: dict[str, Any]) -> tuple[int, bytes]:
    request = Request(
        f"{server.url}/v1/{route}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {ADMIN}"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=60) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def test_spooled_reads_charge_each_row_and_collected_reads_the_total(
    binary: Binary, database: Database
) -> None:
    connection, schema, _ = database
    plant(connection, schema)
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    with serve(binary, postgres_dsn=dsn, schema=schema, extra_env=BUDGET) as server:
        # About 400 KB of rows under a 200 KB budget: 2.16.0 had no budget
        # here and served these, and so does a spooled read.
        requests: list[tuple[str, dict[str, Any]]] = [
            ("conversation/read_turns", {"conversation_id": LONG}),
            ("conversation/search_turns", {"query": "dashboard", "limit": 500}),
            (
                "conversation/search_turns",
                {"query": "dashboard", "limit": 500, "order": "relevance"},
            ),
        ]
        for route, body in requests:
            status, answer = raw(server, route, body)
            assert status == 200, answer[:300]
            assert len(json.loads(answer)["turns"]) == 40
        remote = run(
            binary,
            "continue",
            LONG,
            "--url",
            server.url,
            env={"NEOSIAN_CLIENT_TOKEN": ADMIN},
        )
        assert remote.returncode == 0, remote.stderr
    # One row heavier than the budget is refused by name, before any byte.
    with serve(
        binary,
        postgres_dsn=dsn,
        schema=schema,
        extra_env={"NEOSIAN_MAX_RESPONSE_BYTES": "5000"},
    ) as server:
        status, answer = raw(
            server, "conversation/read_turns", {"conversation_id": LONG}
        )
        assert status == 400
        error = json.loads(answer)["error"]
        assert error["code"] == "value_error" and error["details"] == {}
        assert error["message"].startswith(
            "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
        )
        assert error["message"].endswith(
            " > 5000; select a smaller batch or raise this setting"
        )
    # A local whole read collects every turn, so S13's total charge applies.
    local = run(
        binary,
        "continue",
        LONG,
        "--schema",
        schema,
        env={"NEOSIAN_POSTGRES_DSN": dsn, **BUDGET},
    )
    assert local.returncode == 1
    assert local.stderr.startswith(
        "error: resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
    )
    assert local.stderr.endswith(
        " > 200000; select a smaller batch or raise this setting\n"
    )


def backends(connection: psycopg.Connection[tuple[object, ...]]) -> list[str]:
    rows = connection.execute(
        "SELECT state FROM pg_stat_activity WHERE datname = current_database()"
        " AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
    ).fetchall()
    return [str(row[0]) for row in rows]


def test_a_slow_reader_holds_no_connection_or_transaction(database: Database) -> None:
    # Review F1: a 20 MB answer read slowly used to keep its pooled connection
    # and an open transaction until the reader finished.
    connection, schema, server = database
    plant(connection, schema, count=300, words=13_000)
    port = int(server.url.rsplit(":", 1)[1])
    data = json.dumps({"conversation_id": LONG}).encode()
    reader = socket.socket()
    reader.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    reader.connect(("127.0.0.1", port))
    try:
        reader.sendall(
            b"POST /v1/conversation/read_turns HTTP/1.1\r\nHost: x\r\n"
            + f"Authorization: Bearer {ADMIN}\r\nContent-Length: {len(data)}\r\n\r\n".encode()
            + data
        )
        got = 0
        while got < 65_536:
            chunk = reader.recv(4096)
            assert chunk
            got += len(chunk)
        assert "idle in transaction" not in backends(connection)
        for _ in range(8):
            started = time.monotonic()
            status, _ = raw(
                server, "conversation/last_turn_number", {"conversation_id": LONG}
            )
            assert status == 200 and time.monotonic() - started < 2.0
        # Several slow readers at once still leave room for others.
        others = [socket.create_connection(("127.0.0.1", port)) for _ in range(4)]
        for other in others:
            other.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            other.sendall(
                b"POST /v1/conversation/read_turns HTTP/1.1\r\nHost: x\r\n"
                + f"Authorization: Bearer {ADMIN}\r\nContent-Length: {len(data)}\r\n\r\n".encode()
                + data
            )
            assert other.recv(4096)
        started = time.monotonic()
        status, _ = raw(
            server, "conversation/last_turn_number", {"conversation_id": LONG}
        )
        assert status == 200 and time.monotonic() - started < 2.0
        assert "idle in transaction" not in backends(connection)
        for other in others:
            other.close()
    finally:
        reader.close()


def test_the_client_sends_searches_unpaged(binary: Binary, database: Database) -> None:
    # Review F3's table: on one server a limit-500 newest search took 0.73 s
    # with the 2.16.0 client and 22.5 s with a client that paged searches.
    # Bound: a remote search within 1.5 times the same search run locally on
    # the database, plus half a second for the transfer.
    connection, schema, server = database
    plant(connection, schema, count=500, words=13_000)
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    for args in [
        ("dashboard", "--order", "newest", "--limit", "500"),
        ("dashboard", "word", "--limit", "100"),
    ]:
        started = time.monotonic()
        local = run(
            binary,
            "search",
            *args,
            "--json",
            "--schema",
            schema,
            env={"NEOSIAN_POSTGRES_DSN": dsn},
        )
        local_seconds = time.monotonic() - started
        started = time.monotonic()
        remote = run(
            binary,
            "search",
            *args,
            "--json",
            "--url",
            server.url,
            env={"NEOSIAN_CLIENT_TOKEN": ADMIN},
        )
        remote_seconds = time.monotonic() - started
        assert local.returncode == remote.returncode == 0, remote.stderr
        mine, theirs = json.loads(remote.stdout), json.loads(local.stdout)
        mine.pop("client")
        theirs.pop("client")
        assert mine == theirs
        assert remote_seconds <= 1.5 * local_seconds + 0.5, (
            args,
            local_seconds,
            remote_seconds,
        )
