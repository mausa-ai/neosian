"""MemoryStoreContract over PostgresStore — the §8 conformance kit run
against a real server, including the optimistic-concurrency test the
FileStore run skips (`supports_optimistic_concurrency = True`)."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from neosian import PostgresStore
from neosian.memory.testing import MemoryStoreContract
from tests.external.postgres.conftest import plant_sql, store_schema

_PLANT_TS = datetime(2026, 1, 1, tzinfo=UTC)


class TestPostgresMemoryContract(MemoryStoreContract):
    async def plant_raw_document(
        self,
        store: PostgresStore,  # type: ignore[override]
        scope: str,
        path: str,
        *,
        content: str,
        format_version: int,
        extra: Mapping[str, Any],
    ) -> None:
        """Insert a row directly, bypassing the store (ledger #36): the
        JSONL substrate's raw envelope maps field-per-column here, with
        `extra` landing in the jsonb column the store round-trips."""
        schema = store_schema(store)
        await plant_sql(
            store,
            f'INSERT INTO "{schema}".memories '
            "(scope, path, content, version, created_at, updated_at, actor, "
            "redacted, neosian_format, extra) "
            "VALUES (%(scope)s, %(path)s, %(content)s, 1, %(ts)s, %(ts)s, "
            "NULL, false, %(fmt)s, %(extra)s::jsonb)",
            {
                "scope": scope,
                "path": path,
                "content": content,
                "ts": _PLANT_TS,
                "fmt": format_version,
                "extra": json.dumps(dict(extra)),
            },
        )
