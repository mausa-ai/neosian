"""Record erasure and whole-conversation deletion through public interfaces."""

import pytest
from psycopg import sql

from tests.contract.file.test_records_search import SESSION
from tests.contract.postgres.history_fixture import plant_mixed, postgres
from tests.contract.postgres.test_records_extension import Database
from tests.contract.support import Binary

pytestmark = pytest.mark.postgres

TABLES = (
    "conversations",
    "turns",
    "projections",
    "turn_redactions",
    "record_drafts",
    "record_staging",
    "record_turns",
    "turn_records",
)


def test_redact_removes_a_chunked_turn_above_the_whole_turn_read_limit(
    binary: Binary, database: Database
) -> None:
    plant_mixed(binary, database)
    with postgres(binary, database, NEOSIAN_MAX_TURN_BYTES="65536") as server:
        page = {"conversation_id": SESSION, "after": 1, "limit": 1}
        refused = server.request("/v1/conversation/read_turns", page)
        assert refused.status == 400
        assert "NEOSIAN_MAX_TURN_BYTES exceeded" in refused.body["error"]["message"]
        assert server.post(
            "conversation/redact_turns", conversation_id=SESSION, turns=[2]
        ) == {"count": 1}
        turns = server.post("conversation/read_turns", conversation_id=SESSION)["turns"]
        assert [t["turn"] for t in turns] == [1, 2, 3, 4]
        assert [t["turn"] for t in turns if t["redacted"]] == [2]
        assert turns[1]["messages"] == []
        assert turns[3]["messages"][0]["content"] == "alpha four"


def test_documented_close_removes_visible_and_pending_data(
    binary: Binary, database: Database
) -> None:
    plant_mixed(binary, database)
    connection, name, _ = database
    schema = sql.Identifier(name)
    other = "another-session"
    connection.execute(
        sql.SQL(
            "INSERT INTO {}.projections (conversation_id,turn,kind,text) "
            "VALUES (%s,1,'log','projection')"
        ).format(schema),
        (SESSION,),
    )
    connection.execute(
        sql.SQL(
            "INSERT INTO {}.turn_redactions (conversation_id,turns,created_at) "
            "VALUES (%s,ARRAY[1],now())"
        ).format(schema),
        (SESSION,),
    )
    for session in (SESSION, other):
        connection.execute(
            sql.SQL(
                "WITH draft AS (INSERT INTO {s}.record_drafts "
                "(conversation_id,kind,name,state,opened_at,touched_at,position,bytes) "
                "VALUES (%s,'restore','same','open',now(),now(),'{{}}',100) "
                "RETURNING conversation_id,owner) "
                "INSERT INTO {s}.record_staging (conversation_id,owner,ord,item,body,digest) "
                "SELECT conversation_id,owner,1,'turn','{{}}',decode('00','hex') "
                "FROM draft"
            ).format(s=schema),
            (session,),
        )

    # Extract the command the installed binary shows to the operator. psycopg
    # supplies the same bound value as psql's quoted variable substitution.
    rendered = binary.run("docs", "stores")
    assert rendered.returncode == 0, rendered.stderr
    recipe = rendered.stdout.split("### Closing a conversation\n", 1)[1]
    steps = recipe.split("```sql\n", 1)[1].split("\n```", 1)[0].splitlines()
    assert steps[0] == "BEGIN;" and steps[-1] == "COMMIT;"

    def counts(session: str) -> list[int]:
        result = []
        for table in TABLES:
            query = sql.SQL(
                "SELECT count(*) FROM {} WHERE conversation_id = %s"
            ).format(sql.Identifier(name, table))
            row = connection.execute(query, (session,)).fetchone()
            assert row is not None and isinstance(row[0], int)
            result.append(row[0])
        return result

    def close(session: str, *, rollback: bool = False) -> None:
        with connection.transaction(force_rollback=rollback):
            for step in steps[1:-1]:
                query = sql.SQL(
                    step.replace("neosian.", "{}.").replace(":'conversation_id'", "%s")
                ).format(schema)
                connection.execute(query, (session,))

    before, pending = counts(SESSION), counts(other)
    assert all(before)
    assert pending == [0, 0, 0, 0, 1, 1, 0, 0]
    close(SESSION, rollback=True)
    assert counts(SESSION) == before
    close(SESSION)
    assert not any(counts(SESSION)) and counts(other) == pending
    close(other)
    assert not any(counts(other))
    # Closing a conversation does not remove the extension's schema marker.
    assert connection.execute(
        sql.SQL("SELECT version FROM {}.neosian_records").format(schema)
    ).fetchall() == [(1,)]
