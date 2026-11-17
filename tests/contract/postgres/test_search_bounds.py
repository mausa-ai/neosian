"""Memory-bounded search on Postgres (S13, DESIGN §40.45).

Relevance keeps facts, not rows, and refuses past the candidate bound. Each
row is checked alone against the response budget as it arrives (#418): a row
over it refuses by name. Rows are planted directly; expected texts are
literal.
"""

import json
import os
import subprocess

import psycopg
import pytest
from psycopg import sql

from tests.contract.profile_support import run
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres
Database = tuple[psycopg.Connection[tuple[object, ...]], str, Server]
TURNS = (
    "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than 2 turns "
    "hold a search term; narrow the words or the time range"
)


def plant(connection: psycopg.Connection[tuple[object, ...]], schema: str) -> None:
    name = sql.Identifier(schema)
    connection.execute(
        sql.SQL(
            "INSERT INTO {}.conversations VALUES ('pg-bound', '2026-11-01T00:00:00Z')"
        ).format(name)
    )
    for number, text in enumerate(
        ["deploy the dashboard", "deploy again", "dashboard notes", "lunch"], 1
    ):
        message = {"role": "user", "content": text, "tool_calls": []}
        connection.execute(
            sql.SQL(
                "INSERT INTO {}.turns (conversation_id, turn, messages, created_at,"
                " search_text) VALUES ('pg-bound', %s, %s::jsonb, %s, %s)"
            ).format(name),
            (number, json.dumps([message]), f"2026-11-0{number}T09:00:00Z", text),
        )


def search(
    binary: Binary, schema: str, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    return run(
        binary,
        "search",
        *args,
        "--schema",
        schema,
        "--json",
        env={"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"], **env},
    )


def test_relevance_refuses_past_the_bound_and_answers_within_it(
    binary: Binary, database: Database
) -> None:
    connection, schema, _ = database
    plant(connection, schema)
    # "deploy dashboard" has three candidates: turns 1, 2 and 3.
    within = search(binary, schema, "deploy", "dashboard", NEOSIAN_SEARCH_MAX_CANDIDATES="3")  # fmt: skip
    unbounded = search(binary, schema, "deploy", "dashboard")
    assert within.returncode == 0, within.stderr
    assert within.stdout == unbounded.stdout
    page = json.loads(within.stdout)
    assert [hit["turn"] for hit in page["hits"]] == [1, 2, 3]
    refused = search(binary, schema, "deploy", "dashboard", NEOSIAN_SEARCH_MAX_CANDIDATES="2")  # fmt: skip
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["error"] == TURNS
    newest = search(
        binary,
        schema,
        "deploy",
        "--order",
        "newest",
        "--since",
        "2026-11-01T00:00:00Z",
        NEOSIAN_SEARCH_MAX_CANDIDATES="1",
    )
    assert newest.returncode == 0
    hits = json.loads(newest.stdout)["hits"]
    assert [hit["turn"] for hit in hits] == [2, 1]


def test_the_response_budget_checks_each_row_alone(
    binary: Binary, database: Database
) -> None:
    # #418: every read checks each row alone, so a search or a whole
    # conversation read whose rows together pass the budget is answered, as
    # 2.16.0 answered it; a single row over the budget refuses by name.
    connection, schema, server = database
    plant(connection, schema)
    budget = {"NEOSIAN_MAX_RESPONSE_BYTES": "600"}
    for args in [("deploy", "--order", "newest"), ("deploy",)]:
        found = search(binary, schema, *args, **budget)
        assert found.returncode == 0, found.stdout
        assert [hit["turn"] for hit in json.loads(found.stdout)["hits"]] in (
            [2, 1],
            [1, 2],
        )
    tiny = {"NEOSIAN_MAX_RESPONSE_BYTES": "100"}
    for args in [("deploy", "--order", "newest"), ("deploy",)]:
        refused = search(binary, schema, *args, **tiny)
        assert refused.returncode == 1
        error = json.loads(refused.stdout)["error"]
        assert error.startswith("resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: ")
        assert error.endswith(" > 100; select a smaller batch or raise this setting")
    dsn = {"NEOSIAN_POSTGRES_DSN": os.environ["NEOSIAN_TEST_POSTGRES_DSN"]}
    whole = run(
        binary, "continue", "pg-bound", "--schema", schema, env={**dsn, **budget}
    )
    assert whole.returncode == 0, whole.stderr
    refused = run(
        binary, "continue", "pg-bound", "--schema", schema, env={**dsn, **tiny}
    )
    assert refused.returncode == 1
    assert refused.stderr.startswith(
        "error: resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
    )
    assert refused.stderr.endswith(
        " > 100; select a smaller batch or raise this setting\n"
    )
    assert search(binary, schema, "deploy").returncode == 0
    # The stored rows are untouched by a refused read.
    assert connection.execute(
        sql.SQL("SELECT count(*) FROM {}.turns").format(sql.Identifier(schema))
    ).fetchone() == (4,)
