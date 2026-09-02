"""The bearer gate (DESIGN §18): every surface behind one token, compared
timing-safely; `/health` alone is unauthenticated (a container healthcheck
carries no token); an auth miss is a plain 401, never a §18 envelope."""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.app import build_app
from neosian._foundation.server.wire import WIRE_VERSION
from neosian._foundation.shared.exceptions import ConfigurationError

from .conftest import BASE_URL, TOKEN


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = await build_app(FileStore(tmp_path / "mem"), token=TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as bare:
        yield bare


class TestBearerGate:
    async def test_no_token_is_401_with_the_challenge(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/v1/capabilities")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    async def test_wrong_token_is_401(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/memory/read",
            json={"scope": "user:a", "path": "x"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401

    async def test_the_401_body_is_no_wire_envelope(
        self, client: httpx.AsyncClient
    ) -> None:
        # Auth is transport, not a store error: RemoteStore must propagate
        # it raw (the ledger #39 posture), so the body carries no "error"
        # envelope a decoder could mistake for one.
        response = await client.get("/v1/capabilities")
        assert b"error" not in response.content

    async def test_health_needs_no_token(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_the_right_token_passes(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/v1/capabilities", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["wire_version"] == WIRE_VERSION
        assert payload["backend"] == "FileStore"
        assert payload["supports_optimistic_concurrency"] is False
        assert payload["client"] == "client:default"  # a bare token's client


class TestBuildAppRefusals:
    async def test_an_empty_token_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="never serves"):
            await build_app(FileStore(tmp_path / "mem"), token="")

    async def test_a_single_seam_store_refuses(self) -> None:
        from neosian._foundation.memory.base import MemoryStore
        from neosian._foundation.memory.types import (
            MemoryDocument,
            MemoryEntry,
            MemoryRedaction,
            MemoryVersion,
        )

        class MemoryOnly(MemoryStore):
            """One seam only — the state process serves both (§18)."""

            async def read(self, scope: str, path: str) -> MemoryDocument | None:
                raise NotImplementedError

            async def write(
                self,
                scope: str,
                path: str,
                content: str,
                *,
                actor: str | None = None,
                expected_version: int | None = None,
            ) -> MemoryDocument:
                raise NotImplementedError

            async def delete(
                self, scope: str, path: str, *, actor: str | None = None
            ) -> bool:
                raise NotImplementedError

            async def rename(
                self, scope: str, src: str, dst: str, *, actor: str | None = None
            ) -> MemoryDocument:
                raise NotImplementedError

            async def list_documents(
                self, scope: str, *, prefix: str = ""
            ) -> tuple[MemoryEntry, ...]:
                raise NotImplementedError

            async def versions(
                self, scope: str, path: str, *, limit: int = 50
            ) -> tuple[MemoryVersion, ...]:
                raise NotImplementedError

            async def redact(
                self, scope: str, *, path: str | None = None, actor: str | None = None
            ) -> int:
                raise NotImplementedError

            async def history(
                self,
                scope: str,
                *,
                since: datetime | None = None,
                limit: int | None = None,
            ) -> tuple[MemoryVersion, ...]:
                raise NotImplementedError

            async def redactions(
                self,
                scope: str,
                *,
                since: datetime | None = None,
                limit: int | None = None,
            ) -> tuple[MemoryRedaction, ...]:
                raise NotImplementedError

        with pytest.raises(ConfigurationError, match="ConversationStore"):
            await build_app(MemoryOnly(), token=TOKEN)
