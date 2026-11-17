"""Where answers are spooled on Postgres, and when they cannot be (§40.46, #419).

An unwritable or failing temporary directory never turns an answer into a 500
or a cut body: the server finishes it in memory within
`NEOSIAN_MAX_RESPONSE_BYTES`, as 2.16.0 held it, and refuses past that with
`value_error`, which the binary answers in size pages. A listing asked for no
rows reads none: a row over the response budget refuses any read of it, so a
`limit: 0` answer proves nothing was read. Rows are planted directly.
"""

import json
import os
from typing import Any

import pytest

from tests.contract.postgres.test_size_paging import LONG, Database, plant, raw
from tests.contract.profile_support import run
from tests.contract.support import ADMIN, Binary, serve
from tests.contract.test_spool import WARNING, no_larger_files, unwritable

pytestmark = pytest.mark.postgres
READ: tuple[str, dict[str, Any]] = (
    "conversation/read_turns",
    {"conversation_id": LONG},
)
NEWEST: tuple[str, dict[str, Any]] = (
    "conversation/search_turns",
    {"query": "dashboard", "order": "newest", "limit": 500},
)


def answers(binary: Binary, schema: str, **options: Any) -> list[tuple[int, bytes]]:
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    with serve(binary, postgres_dsn=dsn, schema=schema, **options) as server:
        return [raw(server, route, body) for route, body in (READ, NEWEST)]


def through_the_binary(binary: Binary, schema: str, **options: Any) -> None:
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    with serve(binary, postgres_dsn=dsn, schema=schema, **options) as server:
        for args in [("continue", LONG), ("search", "dashboard", "word", "--json")]:
            remote = run(
                binary, *args, "--url", server.url, env={"NEOSIAN_CLIENT_TOKEN": ADMIN}
            )
            local = run(
                binary, *args, "--schema", schema, env={"NEOSIAN_POSTGRES_DSN": dsn}
            )
            assert remote.returncode == local.returncode == 0, remote.stderr
            if "--json" in args:
                mine, theirs = json.loads(remote.stdout), json.loads(local.stdout)
                mine.pop("client")
                theirs.pop("client")
                assert mine == theirs
            else:
                assert remote.stdout == local.stdout


def test_an_unwritable_temporary_directory_answers_from_memory(
    binary: Binary, database: Database
) -> None:
    connection, schema, _ = database
    plant(connection, schema, count=120, words=13_000)
    expected = answers(binary, schema)
    assert [status for status, _ in expected] == [200, 200]
    log: list[str] = []
    tmp = unwritable(binary)
    assert answers(binary, schema, extra_env=tmp, log=log) == expected
    assert log[0].count(WARNING) == 1, log[0]
    through_the_binary(binary, schema, extra_env=tmp)
    budget = {**tmp, "NEOSIAN_MAX_RESPONSE_BYTES": str(2 << 20)}
    refused = answers(binary, schema, extra_env=budget)
    for status, answer in refused:
        assert status == 400
        assert json.loads(answer)["error"]["message"].startswith(
            "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
        )
    through_the_binary(binary, schema, extra_env=budget)


@pytest.mark.skipif(os.name == "nt", reason="POSIX resource limits")
def test_a_spool_that_fails_midway_is_still_the_whole_answer(
    binary: Binary, database: Database
) -> None:
    connection, schema, _ = database
    plant(connection, schema, count=120, words=13_000)
    expected = answers(binary, schema)
    assert answers(binary, schema, preexec=no_larger_files(3 << 20)) == expected


def test_a_listing_asked_for_no_rows_reads_none(
    binary: Binary, database: Database
) -> None:
    _, schema, server = database
    for content in ("x" * 20_000, "y" * 20_000):
        server.post("memory/write", scope="user:contract", path="a.md", content=content)
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    small = {"NEOSIAN_MAX_RESPONSE_BYTES": "4096"}
    with serve(binary, postgres_dsn=dsn, schema=schema, extra_env=small) as tight:
        for route, key in (
            ("memory/history", {}),
            ("memory/versions", {"path": "a.md"}),
        ):
            body = {"scope": "user:contract", **key}
            assert tight.post(route, **body, limit=0) == {
                "versions": [],
                "next_cursor": None,
            }
            refused = tight.request("/v1/" + route, {**body, "limit": 1})
            assert refused.status == 400
            assert refused.body["error"]["message"].startswith(
                "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
            )
