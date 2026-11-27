"""`neosian records drafts` on PostgreSQL (the T1e2c slice of the T1e2 plan,
DESIGN §40.54 item 13): fifty-one conversations whose open drafts are aged
in the database, one past the default page, swept from the DSN and through
the server in front of the same schema. Both print the same JSON Lines in
the route's row shape; the preview removes nothing and `--discard` removes
the stale drafts only."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

from tests.contract.file.test_records import canonical
from tests.contract.postgres.test_records_wire import Database, recording
from tests.contract.postgres.test_records_wire_search import age
from tests.contract.records.support import (
    bookkeeping,
    probe,
    record,
    record_bytes,
    write,
)
from tests.contract.support import ADMIN, Binary

pytestmark = pytest.mark.postgres


def drafts(binary: Binary, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary.executable), "records", "drafts", *args],
        env=binary.environment() | env,
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def test_postgres_and_its_server_print_the_same_pages(
    database: Database, binary: Binary
) -> None:
    one = [record(0, 0, "user", "prompt", "stale")]
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    with recording(database, binary) as (server, link, name):
        expected: list[dict[str, Any]] = []
        for number in range(51):
            conversation = f"c-{number:02}"
            write(server, conversation, "x", one)
            age(link, name, conversation)
            freed = bookkeeping(conversation, "x") + record_bytes(conversation, one[0])
            expected.append(
                {
                    "conversation_id": conversation,
                    "drafts": 1,
                    "restores": 0,
                    "freed_bytes": freed,
                }
            )
        write(server, "c-99", "fresh", one)
        output = "".join(canonical(row) + "\n" for row in expected)
        local = {"NEOSIAN_POSTGRES_DSN": dsn}
        remote = {"NEOSIAN_CLIENT_TOKEN": ADMIN}

        direct = drafts(binary, "--schema", name, **local)
        assert direct.returncode == 0, direct.stderr
        assert direct.stdout == output
        served = drafts(binary, "--url", server.url, "--json", **remote)
        assert served.returncode == 0, served.stderr
        assert served.stdout == output
        assert probe(server, "c-50", "x")["next_seq"] == 1, "a preview keeps"

        discarded = drafts(binary, "--schema", name, "--discard", "--json", **local)
        assert discarded.returncode == 0, discarded.stderr
        assert discarded.stdout == output
        for conversation in ["c-00", "c-49", "c-50"]:
            assert probe(server, conversation, "x")["state"] == "none"
        assert probe(server, "c-99", "fresh")["next_seq"] == 1
        again = drafts(binary, "--url", server.url, **remote)
        assert (again.returncode, again.stdout) == (0, "")
