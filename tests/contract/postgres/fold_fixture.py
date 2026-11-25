"""PostgreSQL fold inputs authored from the record and restore formats."""

import hashlib
import json
from typing import Any

from psycopg import sql

from tests.contract.file.test_records import STAMP, canonical, record
from tests.contract.postgres.history_fixture import native
from tests.contract.postgres.test_records_erasure import TABLES
from tests.contract.postgres.test_records_extension import Database
from tests.contract.postgres.test_records_migrate import rows


def snapshot(database: Database) -> dict[str, list[tuple[object, ...]]]:
    conn, schema, _ = database
    return {
        t: conn.execute(
            sql.SQL(
                "SELECT to_jsonb(r)::text,r.ctid::text,r.xmin::text FROM {} r "
                "ORDER BY to_jsonb(r)::text"
            ).format(sql.Identifier(schema, t))
        ).fetchall()
        for t in TABLES
    }


def draft(
    database: Database, session: str, kind: str, name: str, *, committed: bool = False
) -> int:
    marker = {
        "restore": name,
        "position": {
            "turn": 0,
            "seq": 0,
            "complete": True,
            "projections": 0,
            "redactions": 0,
        },
        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "last_part": None,
    }
    result = rows(
        database,
        "INSERT INTO {s}.record_drafts "
        "(conversation_id,kind,name,state,opened_at,touched_at,position,bytes) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,100) RETURNING owner",
        session,
        kind,
        name,
        "committed" if committed else "open",
        STAMP,
        STAMP,
        json.dumps(marker if committed else {}),
    )
    owner = result[0][0]
    assert isinstance(owner, int)
    if not committed:
        r = record(0, 0, "user", "prompt", "pending")
        rows(
            database,
            "INSERT INTO {s}.turn_records "
            "(conversation_id,owner,part,seq,message,role,kind,content,chunk,size,last,text,digest) "
            "VALUES (%s,%s,%s,0,0,'user','prompt','text',0,7,true,'pending',%s)",
            session,
            owner,
            1 if kind == "restore" else 0,
            hashlib.sha256(canonical(r).encode()).digest(),
        )
    if not committed and kind == "restore":
        rows(
            database,
            "INSERT INTO {s}.record_staging VALUES (%s,%s,1,'turn','{{}}',decode('00','hex'))",
            session,
            owner,
        )
    return owner


def restored_turn(
    database: Database, session: str, n: int, owner: int, records: list[dict[str, Any]]
) -> None:
    """Place a signed-format turn under one restore's identity and its source turn number."""
    native(database, session, n, STAMP, records)
    rows(
        database,
        "UPDATE {s}.turn_records r SET owner=%s,part=%s FROM {s}.record_turns m "
        "WHERE m.conversation_id=%s AND m.turn=%s AND r.conversation_id=m.conversation_id "
        "AND (r.owner,r.part)=(m.owner,m.part)",
        owner,
        n,
        session,
        n,
    )
    rows(
        database,
        "UPDATE {s}.record_turns SET owner=%s,part=turn,draft='backup' WHERE conversation_id=%s AND turn=%s",
        owner,
        session,
        n,
    )
    rows(
        database,
        "UPDATE {s}.turns SET actor='restore:oracle' WHERE conversation_id=%s AND turn=%s",
        session,
        n,
    )


def restore_unit(
    database: Database, session: str, turns: dict[int, list[dict[str, Any]]]
) -> None:
    """One restored unit with multiple source turns and a durable content digest."""
    owner = draft(database, session, "restore", "backup", committed=True)
    digest = hashlib.sha256()
    for number, records in turns.items():
        restored_turn(database, session, number, owner, records)
        header = {
            "turn": number,
            "created_at": STAMP,
            "actor": "restore:oracle",
            "redacted": False,
            "records": len(records),
            "bytes": sum(len(r["text"].encode()) for r in records),
        }
        digest.update(hashlib.sha256(canonical(header).encode()).digest())
        for r in records:
            digest.update(
                hashlib.sha256(canonical({"turn": number, **r}).encode()).digest()
            )
    last = max(turns)
    position = {
        "turn": last,
        "seq": len(turns[last]),
        "complete": True,
        "projections": 0,
        "redactions": 0,
    }
    marker = {
        "restore": "backup",
        "position": position,
        "digest": digest.hexdigest(),
        "last_part": None,
    }
    rows(
        database,
        "UPDATE {s}.record_drafts SET position=%s::jsonb,bytes=%s WHERE owner=%s",
        json.dumps(marker),
        len("backup") + 2 * len(session) + 256,
        owner,
    )
