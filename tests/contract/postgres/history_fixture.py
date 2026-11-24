"""Plant the independently encoded FileStore history fixture in PostgreSQL tables."""

import hashlib
import json
import os
from contextlib import AbstractContextManager
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from tests.contract.file.test_records import canonical
from tests.contract.file.test_records_search import SESSION, plant
from tests.contract.postgres.test_records_extension import Database, install
from tests.contract.support import Binary, Server, serve


def native(
    database: Database,
    session: str,
    number: int,
    stamp: str,
    records: list[dict[str, Any]],
) -> None:
    connection, name, _ = database
    schema = sql.Identifier(name)
    connection.execute(
        sql.SQL(
            "INSERT INTO {}.conversations VALUES (%s,%s) ON CONFLICT DO NOTHING"
        ).format(schema),
        (session, stamp),
    )
    connection.execute(
        sql.SQL(
            "INSERT INTO {}.turns (conversation_id,turn,messages,created_at,neosian_format) "
            "VALUES (%s,%s,'[]',%s,2)"
        ).format(schema),
        (session, number, stamp),
    )
    allocated = connection.execute(
        "SELECT nextval(pg_get_serial_sequence(%s, 'owner'))",
        (f"{name}.record_drafts",),
    ).fetchone()
    assert allocated is not None
    owner = allocated[0]
    size = sum(len(r["text"].encode()) for r in records)
    connection.execute(
        sql.SQL("INSERT INTO {}.record_turns VALUES (%s,%s,%s,0,%s,%s,%s,%s)").format(
            schema
        ),
        (session, number, owner, f"d{number}", len(records), size, stamp),
    )
    columns = sql.SQL(", ").join(map(sql.Identifier, records[0]))
    values = sql.SQL(", ").join([sql.Placeholder()] * len(records[0]))
    stmt = sql.SQL(
        "INSERT INTO {}.turn_records (conversation_id,owner,part,{},last,digest) "
        "VALUES (%s,%s,0,{},%s,%s)"
    ).format(schema, columns, values)
    for i, r in enumerate(records):
        last = i + 1 == len(records) or records[i + 1]["chunk"] == 0
        digest = hashlib.sha256(canonical(r).encode()).digest()
        connection.execute(stmt, (session, owner, *r.values(), last, digest))


def plant_mixed(binary: Binary, database: Database) -> None:
    install(database, binary)
    connection, name, _ = database
    schema = sql.Identifier(name)
    plant(binary.home)
    source = binary.home / "conversations" / SESSION
    for line in (source / "turns.jsonl").read_text().splitlines():
        row = json.loads(line)
        number, stamp = row["turn"], row["created_at"]
        if row["neosian_format"] == 1:
            connection.execute(
                sql.SQL(
                    "INSERT INTO {}.conversations VALUES (%s,%s) ON CONFLICT DO NOTHING"
                ).format(schema),
                (SESSION, stamp),
            )
            connection.execute(
                sql.SQL(
                    "INSERT INTO {}.turns (conversation_id,turn,messages,created_at,search_text) "
                    "VALUES (%s,%s,%s,%s,%s)"
                ).format(schema),
                (
                    SESSION,
                    number,
                    Jsonb(row["messages"]),
                    stamp,
                    row["messages"][0]["content"],
                ),
            )
        else:
            file = source / "records" / f"{number}.jsonl"
            records = []
            for raw in file.read_text().splitlines():
                item = json.loads(raw)
                assert item.pop("neosian_format") == 1
                checksum = item.pop("sha256")
                assert checksum == hashlib.sha256(canonical(item).encode()).hexdigest()
                records.append(item)
            native(database, SESSION, number, stamp, records)


def postgres(
    binary: Binary, database: Database, **settings: str
) -> AbstractContextManager[Server]:
    return serve(
        binary,
        postgres_dsn=os.environ["NEOSIAN_TEST_POSTGRES_DSN"],
        schema=database[1],
        extra_env=settings,
    )
