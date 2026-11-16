"""Streamed answers and size paging on Postgres (S14, DESIGN §40.46).

Rows stream from one statement through a portal, each charged against
`NEOSIAN_MAX_RESPONSE_BYTES` as it arrives. Crossing the budget after the
first 64 KiB of an answer have left ends the 200 body with the envelope a 400
would carry, after a line feed; the binary's remote client reports the same
error as a local read. Size pages read one bounded statement each, so the same
data pages under the same budget. Rows are planted directly.
"""

import json
import os
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


def plant(connection: psycopg.Connection[tuple[object, ...]], schema: str) -> None:
    name = sql.Identifier(schema)
    connection.execute(
        sql.SQL(
            "INSERT INTO {}.conversations VALUES (%s, '2026-11-01T00:00:00Z')"
        ).format(name),
        (LONG,),
    )
    for number in range(1, 41):
        text = f"long dashboard turn {number} " + "word " * 2000
        message: dict[str, Any] = {
            "role": "user",
            "content": text,
            "reasoning": None,
            "tool_calls": [],
            "tool_call_id": None,
        }
        connection.execute(
            sql.SQL(
                "INSERT INTO {}.turns (conversation_id, turn, messages, created_at,"
                " search_text) VALUES (%s, %s, %s::jsonb, %s, %s)"
            ).format(name),
            (
                LONG,
                number,
                json.dumps([message]),
                f"2026-11-02T09:00:{number:02d}Z",
                text,
            ),
        )


def raw(server: Server, route: str, body: dict[str, Any]) -> tuple[int, bytes]:
    request = Request(
        f"{server.url}/v1/{route}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {ADMIN}"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def test_a_budget_crossed_mid_stream_is_a_trailer(
    binary: Binary, database: Database
) -> None:
    connection, schema, _ = database
    plant(connection, schema)
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    with serve(binary, postgres_dsn=dsn, schema=schema, extra_env=BUDGET) as server:
        requests: list[tuple[str, dict[str, Any]]] = [
            ("conversation/read_turns", {"conversation_id": LONG}),
            ("conversation/search_turns", {"query": "dashboard", "limit": 500}),
        ]
        for route, request in requests:
            status, answer = raw(server, route, request)
            assert status == 200 and len(answer) > 64 * 1024
            with pytest.raises(json.JSONDecodeError):
                json.loads(answer)
            prefix, trailer = answer.split(b"\n", 1)
            assert prefix.startswith(b'{"turns":[{"conversation_id":"pg-long"')
            error = json.loads(trailer)["error"]
            assert (error["code"], error["details"]) == ("value_error", {})
            assert error["message"].startswith(
                "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
            )
            assert error["message"].endswith(
                " > 200000; select a smaller batch or raise this setting"
            )
        # Size pages are one bounded statement each and fit the same budget.
        turns: list[Any] = []
        body: dict[str, Any] = {"conversation_id": LONG, "page_bytes": 60000}
        while True:
            page = server.post("conversation/read_turns", **body)
            turns += page["turns"]
            if page["continuation"] is None:
                break
            body["continuation"] = page["continuation"]
        assert [turn["turn"] for turn in turns] == list(range(1, 41))
        remote = run(
            binary,
            "continue",
            LONG,
            "--url",
            server.url,
            env={"NEOSIAN_CLIENT_TOKEN": ADMIN},
        )
    local = run(
        binary,
        "continue",
        LONG,
        "--schema",
        schema,
        env={"NEOSIAN_POSTGRES_DSN": dsn, **BUDGET},
    )
    # The remote client pages at 1 MiB, past this server's budget, and
    # reports the trailer's refusal as a local read reports its own.
    assert remote.returncode == local.returncode == 1
    for result in (remote, local):
        assert result.stderr.startswith(
            "error: resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
        ), result.stderr
        assert result.stderr.endswith(
            " > 200000; select a smaller batch or raise this setting\n"
        )
