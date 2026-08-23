"""Both conformance kits through RemoteStore over a PostgresStore backend
— the §18 wire's second backend, and the half of NM's done-when the
FileStore run cannot reach: `supports_optimistic_concurrency` is True
here, so the kit's expected_version race test runs over the network.

The app is in-process on `httpx.ASGITransport`; the backing store is the
conftest's per-test schema, kept in hand for substrate planting.
"""

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from neosian import PostgresStore, RemoteStore
from neosian._foundation.server.app import build_app
from neosian.conversation.testing import ConversationStoreContract
from neosian.memory.testing import MemoryStoreContract
from tests.external.postgres.conftest import plant_sql, store_schema

_PLANT_TS = datetime(2026, 1, 1, tzinfo=UTC)
_TOKEN = "postgres-contract-token"
_BASE_URL = "http://state-process"


@pytest.fixture
async def remote(store: PostgresStore) -> AsyncIterator[RemoteStore]:
    """A RemoteStore whose server is this test's PostgresStore."""
    app = await build_app(store, token=_TOKEN)
    client = await RemoteStore.connect(
        _BASE_URL, token=_TOKEN, transport=httpx.ASGITransport(app=app)
    )
    try:
        yield client
    finally:
        await client.aclose()


def _decode(line: str) -> dict[str, Any] | None:
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class TestRemotePostgresMemoryContract(MemoryStoreContract):
    _backing: PostgresStore

    @pytest.fixture(name="store")
    async def remote_store(
        self,
        store: PostgresStore,  # noqa: PT019 - the conftest fixture, renamed below
        remote: RemoteStore,
    ) -> AsyncIterator[RemoteStore]:
        self._backing = store
        yield remote

    async def plant_raw_document(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        scope: str,
        path: str,
        *,
        content: str,
        format_version: int,
        extra: Mapping[str, Any],
    ) -> None:
        schema = store_schema(self._backing)
        await plant_sql(
            self._backing,
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


class TestRemotePostgresConversationContract(ConversationStoreContract):
    _backing: PostgresStore

    @pytest.fixture(name="store")
    async def remote_store(
        self,
        store: PostgresStore,  # noqa: PT019 - the conftest fixture, renamed below
        remote: RemoteStore,
    ) -> AsyncIterator[RemoteStore]:
        self._backing = store
        yield remote

    async def plant_raw_turn(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        await self._plant_parent(conversation_id)
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
        schema = store_schema(self._backing)
        await plant_sql(
            self._backing,
            f'INSERT INTO "{schema}".turns '
            "(conversation_id, turn, messages, created_at, neosian_format) "
            "VALUES (%(cid)s, %(turn)s, %(messages)s::jsonb, %(ts)s, %(fmt)s)",
            {"cid": conversation_id, **fields},
        )

    async def plant_raw_projection(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        await self._plant_parent(conversation_id)
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
        schema = store_schema(self._backing)
        await plant_sql(
            self._backing,
            f'INSERT INTO "{schema}".projections '
            "(conversation_id, turn, kind, text, span, neosian_format) "
            "VALUES (%(cid)s, %(turn)s, %(kind)s, %(text)s, %(span)s, %(fmt)s)",
            {"cid": conversation_id, **fields},
        )

    async def _plant_parent(self, conversation_id: str) -> None:
        schema = store_schema(self._backing)
        await plant_sql(
            self._backing,
            f'INSERT INTO "{schema}".conversations (conversation_id, created_at) '
            "VALUES (%(cid)s, %(ts)s) ON CONFLICT (conversation_id) DO NOTHING",
            {"cid": conversation_id, "ts": _PLANT_TS},
        )


class TestCapabilityMirroringOverPostgres:
    async def test_the_wire_reports_the_backends_arbitration(
        self, remote: RemoteStore
    ) -> None:
        # Transmitted, never claimed: the class the handshake returned
        # carries True because the *backend* arbitrates version races.
        assert type(remote).supports_optimistic_concurrency is True
        capabilities = await remote.capabilities()
        assert capabilities["backend"] == "PostgresStore"
