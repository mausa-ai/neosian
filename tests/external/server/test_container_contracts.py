"""Both conformance kits through `RemoteStore` against the *running
container* — NM's done-when: the same conformance tests, over a real
socket into the published image, on both backends (volume FileStore /
Postgres DSN), with the bearer token enforced (DESIGN §18.8).

`scripts/container_test.sh` starts one container per backend and runs
this module once per leg; the other leg's tests self-skip on their
missing env.
"""

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import yaml

from neosian import RemoteStore
from neosian._foundation.memory.paths import path_segments
from neosian._foundation.memory.scope import Scope, parse_scope, scope_directory
from neosian.conversation.testing import ConversationStoreContract
from neosian.memory.testing import MemoryStoreContract
from tests.external.postgres.conftest import plant_sql, store_schema

from .conftest import FileLeg, PgLeg

_PLANT_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _fresh_scope() -> Scope:
    """A namespace no test (or server-side cache) has ever seen — the
    long-running container's per-test isolation (see conftest)."""
    return parse_scope(f"user:kit-{uuid.uuid4().hex[:12]}")


def _fresh_conversation() -> str:
    return f"kit-{uuid.uuid4().hex[:12]}"


# --- The volume-FileStore leg -------------------------------------------


class TestContainerFileMemoryContract(MemoryStoreContract):
    _harness: FileLeg

    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return f"{store.client}/{actor}"

    @pytest.fixture
    def scope(self) -> Scope:
        return _fresh_scope()

    @pytest.fixture
    async def store(self, file_leg: FileLeg) -> AsyncIterator[RemoteStore]:
        self._harness = file_leg
        yield file_leg.remote

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
        # The volume is the substrate: written from the host, read by
        # the container — the test_remote_memory_contract idiom across
        # the mount.
        segments = path_segments(path)
        doc_file = self._harness.root.joinpath(
            *scope_directory(parse_scope(scope)),
            "documents",
            *segments[:-1],
            segments[-1] + ".md",
        )
        doc_file.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {
            "neosian_format": format_version,
            "version": 1,
            "created_at": "2026-08-19T09:00:00Z",
            "updated_at": "2026-08-19T09:00:00Z",
            **extra,
        }
        rendered = yaml.safe_dump(frontmatter, sort_keys=False)
        doc_file.write_text(
            f"---\n{rendered}---\n{content}", encoding="utf-8", newline=""
        )


class TestContainerFileConversationContract(ConversationStoreContract):
    _harness: FileLeg

    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return f"{store.client}/{actor}"

    @pytest.fixture
    def conversation_id(self) -> str:
        return _fresh_conversation()

    @pytest.fixture
    async def store(self, file_leg: FileLeg) -> AsyncIterator[RemoteStore]:
        self._harness = file_leg
        yield file_leg.remote

    async def plant_raw_turn(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        self._plant(conversation_id, "turns.jsonl", line)

    async def plant_raw_projection(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        self._plant(conversation_id, "projections.jsonl", line)

    def _plant(self, conversation_id: str, filename: str, line: str) -> None:
        file = self._harness.root / "conversations" / conversation_id / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line + "\n")


class TestFileBackendCapability:
    async def test_the_wire_reports_no_arbitration(self, file_leg: FileLeg) -> None:
        # Transmitted, never claimed (§18.5): files do not arbitrate.
        assert type(file_leg.remote).supports_optimistic_concurrency is False
        capabilities = await file_leg.remote.capabilities()
        assert capabilities["backend"] == "FileStore"


# --- The Postgres-DSN leg -----------------------------------------------


def _decode(line: str) -> dict[str, Any] | None:
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class TestContainerPostgresMemoryContract(MemoryStoreContract):
    _harness: PgLeg

    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return f"{store.client}/{actor}"

    @pytest.fixture
    def scope(self) -> Scope:
        return _fresh_scope()

    @pytest.fixture
    async def store(self, pg_leg: PgLeg) -> AsyncIterator[RemoteStore]:
        self._harness = pg_leg
        yield pg_leg.remote

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
        schema = store_schema(self._harness.backing)
        await plant_sql(
            self._harness.backing,
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


class TestContainerPostgresConversationContract(ConversationStoreContract):
    _harness: PgLeg

    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return f"{store.client}/{actor}"

    @pytest.fixture
    def conversation_id(self) -> str:
        return _fresh_conversation()

    @pytest.fixture
    async def store(self, pg_leg: PgLeg) -> AsyncIterator[RemoteStore]:
        self._harness = pg_leg
        yield pg_leg.remote

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
        schema = store_schema(self._harness.backing)
        await plant_sql(
            self._harness.backing,
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
        schema = store_schema(self._harness.backing)
        await plant_sql(
            self._harness.backing,
            f'INSERT INTO "{schema}".projections '
            "(conversation_id, turn, kind, text, span, neosian_format) "
            "VALUES (%(cid)s, %(turn)s, %(kind)s, %(text)s, %(span)s, %(fmt)s)",
            {"cid": conversation_id, **fields},
        )

    async def _plant_parent(self, conversation_id: str) -> None:
        schema = store_schema(self._harness.backing)
        await plant_sql(
            self._harness.backing,
            f'INSERT INTO "{schema}".conversations (conversation_id, created_at) '
            "VALUES (%(cid)s, %(ts)s) ON CONFLICT (conversation_id) DO NOTHING",
            {"cid": conversation_id, "ts": _PLANT_TS},
        )


class TestPostgresBackendCapability:
    async def test_the_wire_reports_the_backends_arbitration(
        self, pg_leg: PgLeg
    ) -> None:
        assert type(pg_leg.remote).supports_optimistic_concurrency is True
        capabilities = await pg_leg.remote.capabilities()
        assert capabilities["backend"] == "PostgresStore"


# --- The gate and the health check, on the real socket -------------------


class TestContainerSurface:
    async def test_health_is_unauthenticated(self, server_url: str) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{server_url}/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_the_bearer_gate_holds(self, server_url: str) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server_url}/v1/memory/read",
                json={"scope": "user:gate", "path": "x"},
            )
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
