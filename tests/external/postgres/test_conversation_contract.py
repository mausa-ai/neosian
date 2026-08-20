"""ConversationStoreContract over PostgresStore — the §9.7 kit against a
real server."""

import json
from datetime import UTC, datetime
from typing import Any

from neosian import PostgresStore
from neosian.conversation.testing import ConversationStoreContract
from tests.external.postgres.conftest import plant_sql, store_schema

_PLANT_TS = datetime(2026, 1, 1, tzinfo=UTC)


async def _plant_parent(store: PostgresStore, conversation_id: str) -> None:
    schema = store_schema(store)
    await plant_sql(
        store,
        f'INSERT INTO "{schema}".conversations (conversation_id, created_at) '
        "VALUES (%(cid)s, %(ts)s) ON CONFLICT (conversation_id) DO NOTHING",
        {"cid": conversation_id, "ts": _PLANT_TS},
    )


def _decode(line: str) -> dict[str, Any] | None:
    """The kit's plant hooks speak raw JSONL lines (ledger #36): an
    object-shaped line maps field-per-column; an unparseable one is
    planted as `neosian_format = 0` — the typed-column equivalent of a
    row carrying no readable format marker, which reads must refuse."""
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class TestPostgresConversationContract(ConversationStoreContract):
    async def plant_raw_turn(
        self,
        store: PostgresStore,  # type: ignore[override]
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        await _plant_parent(store, conversation_id)
        data = _decode(line)
        if data is None:
            fields = {"fmt": 0, "turn": 1, "messages": "[]", "ts": _PLANT_TS}
        else:
            fields = {
                "fmt": data.get("neosian_format", 0),
                "turn": data.get("turn", 1),
                "messages": json.dumps(data.get("messages", [])),
                "ts": datetime.fromisoformat(
                    data.get("created_at", "2026-01-01T00:00:00Z")
                ),
            }
        schema = store_schema(store)
        await plant_sql(
            store,
            f'INSERT INTO "{schema}".turns '
            "(conversation_id, turn, messages, created_at, neosian_format) "
            "VALUES (%(cid)s, %(turn)s, %(messages)s::jsonb, %(ts)s, %(fmt)s)",
            {"cid": conversation_id, **fields},
        )

    async def plant_raw_projection(
        self,
        store: PostgresStore,  # type: ignore[override]
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        await _plant_parent(store, conversation_id)
        data = _decode(line)
        if data is None:
            fields: dict[str, Any] = {
                "fmt": 0,
                "turn": 1,
                "kind": "log",
                "text": "",
                "span": 1,
            }
        else:
            fields = {
                "fmt": data.get("neosian_format", 0),
                "turn": data.get("turn", 1),
                "kind": data.get("kind", "log"),
                "text": data.get("text", ""),
                "span": data.get("span", 1),
            }
        schema = store_schema(store)
        await plant_sql(
            store,
            f'INSERT INTO "{schema}".projections '
            "(conversation_id, turn, kind, text, span, neosian_format) "
            "VALUES (%(cid)s, %(turn)s, %(kind)s, %(text)s, %(span)s, %(fmt)s)",
            {"cid": conversation_id, **fields},
        )
