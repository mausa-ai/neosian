"""`neosian import DIR --url` into a PostgreSQL server with records
extension 1 (the T1e2e slice of the T1e2 plan, DESIGN §40.54 items 12 and
22): the archive of `records/test_import_resume.py` goes in bounded parts
and commits the unit digest computed from its rows, uninterrupted and cut
after an acknowledged header, inside a turn's records and after the
server committed with its answer lost. The rerun resumes from the probed
position, nothing is visible before the commit, and the 4,500 erasure acts
stand in the table in the archive's order."""

from __future__ import annotations

from datetime import datetime

import psycopg
import pytest
from psycopg import sql

from tests.contract.landing.stub import Rule, forwarding
from tests.contract.postgres.test_records_wire import Database, recording
from tests.contract.records.importing import (
    Conversation,
    failure,
    is_probe,
    restore_id,
    restores,
    rich,
    run,
    units,
    within_bounds,
)
from tests.contract.records.restoring import RESTORE, status
from tests.contract.records.test_import_resume import (
    RICH,
    STAGES,
    archive_of,
    content,
    resumed_at_the_probe,
)
from tests.contract.support import Binary, Server

pytestmark = pytest.mark.postgres


def stored(
    link: psycopg.Connection[tuple[object, ...]], name: str, id: str
) -> list[object]:
    query = sql.SQL(
        "SELECT actor, created_at, turns FROM {}.turn_redactions"
        " WHERE conversation_id = %s ORDER BY id"
    ).format(sql.Identifier(name))
    return list(link.execute(query, (id,)).fetchall())


def published_once(
    server: Server,
    link: psycopg.Connection[tuple[object, ...]],
    name: str,
    conversation: Conversation,
) -> None:
    assert server.post("store/conversations")["conversations"] == [conversation.id]
    read = server.post("conversation/read_turns", conversation_id=conversation.id)
    assert read["turns"] == [turn.read(conversation.id) for turn in conversation.turns]
    entries = server.post(
        "conversation/read_projections", conversation_id=conversation.id
    )
    assert entries["entries"] == conversation.ordered_projections()
    assert stored(link, name, conversation.id) == [
        (a["actor"], datetime.fromisoformat(a["created_at"]), a["turns"])
        for a in conversation.acts
    ]


def test_an_import_commits_the_archive_digest_on_postgres(
    database: Database, binary: Binary
) -> None:
    conversation = rich()
    archive = archive_of(binary, conversation)
    with recording(database, binary) as (server, link, name):
        with forwarding(server.url) as stub:
            assert units(run(binary, archive, stub.url)) == [conversation.unit()]
            sent = restores(stub.log)
            assert is_probe(sent[0]) and len(sent) == 7
            for exchange in sent[1:]:
                within_bounds(exchange)
            assert sent[-1].answer["digest"] == RICH
        published_once(server, link, name, conversation)


@pytest.mark.parametrize(
    "stage",
    ["after an acknowledged header", "inside a turn's records", "after the commit"],
)
def test_an_interrupted_import_resumes_on_postgres(
    database: Database, binary: Binary, stage: str
) -> None:
    how, part, _ = STAGES[stage]
    conversation = rich()
    archive = archive_of(binary, conversation)
    restore = restore_id(conversation.id)
    with recording(database, binary) as (server, link, name):
        with forwarding(server.url) as stub:
            stub.add(
                Rule(
                    when=lambda e: e.route == RESTORE
                    and e.run == 0
                    and e.nth == part + 1,
                    kind=how,
                )
            )
            failure(run(binary, archive, stub.url))
            applied = content(stub.log, 0)[: part - (how == "cut")]
            probed = applied[-1].answer["position"]
            if stage != "after the commit":
                assert server.post("store/conversations")["conversations"] == []
                assert server.post(
                    "conversation/last_turn_number", conversation_id=conversation.id
                ) == {"turn": 0}
            stub.next_run()
            assert units(run(binary, archive, stub.url)) == [conversation.unit()]
            if stage != "after the commit":
                resumed_at_the_probe(stub.log, conversation, probed)
            else:
                assert [is_probe(e) for e in restores(stub.log) if e.run == 1] == [True]
            assert status(server, conversation.id, restore)["digest"] == RICH
        published_once(server, link, name, conversation)
