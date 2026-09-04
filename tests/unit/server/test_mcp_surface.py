"""MCP over streamable HTTP (DESIGN §18): the same `create_memory_server`
factory the stdio transport uses, mounted at `/mcp` behind the bearer
gate. Driven with raw JSON-RPC over the ASGI transport — the SDK's own
client speaks httpx2 and would need a real socket.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.server import ceiling
from neosian._foundation.server.app import build_app

from .conftest import BASE_URL, TOKEN

_MOUNTS = (Mount(scope="user:demo", mount_path="memories"),)
_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "neosian-tests", "version": "0"},
    },
}


def _sse_payload(body: str) -> dict[str, Any]:
    """The one JSON-RPC message out of a streamable-HTTP SSE response."""
    for line in body.splitlines():
        if line.startswith("data: "):
            decoded: dict[str, Any] = json.loads(line[len("data: ") :])
            return decoded
    raise AssertionError(f"no SSE data frame in {body!r}")


@asynccontextmanager
async def _mcp_client(root: Path) -> AsyncIterator[httpx.AsyncClient]:
    """App, lifespan and client in one scope.

    Entered inside the test's own task deliberately: the session
    manager's anyio task group refuses to exit in a different task than
    it was entered in, which a fixture generator's finalization would be.
    """
    app = await build_app(
        FileStore(root), token=TOKEN, mounts=_MOUNTS, actor="serve:test"
    )
    transport = httpx.ASGITransport(app=app)
    # ASGITransport runs no lifespan; enter the manager's by hand,
    # exactly as Starlette would.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client,
    ):
        yield client


class TestMcpOverHttp:
    async def test_initialize_reports_the_memory_instructions(
        self, tmp_path: Path
    ) -> None:
        async with _mcp_client(tmp_path / "mem") as client:
            response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
        assert response.status_code == 200
        result = _sse_payload(response.text)["result"]
        assert result["serverInfo"]["name"] == "neosian-memory"
        # The prompt pack plus the live index — the stdio server's own
        # instructions, over HTTP (ledger #51 on this transport: rendered
        # once per process; `view /` is the live read).
        assert "memories" in result["instructions"]

    async def test_the_state_set_is_served_at_mcp(self, tmp_path: Path) -> None:
        """NB slice B (§21.7): `/mcp` lists memory and recall_turn — the
        state process already proves the store keeps conversations."""
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        async with _mcp_client(tmp_path / "mem") as client:
            init = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
            session = {"mcp-session-id": init.headers["mcp-session-id"]}
            note = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=_HEADERS | session,
            )
            assert note.status_code in (200, 202)
            listed = await client.post("/mcp", json=listing, headers=_HEADERS | session)
        assert listed.status_code == 200
        tools = _sse_payload(listed.text)["result"]["tools"]
        assert [tool["name"] for tool in tools] == [
            "memory",
            "list_skills",
            "load_skill",
            "recall_turn",
        ]

    async def test_the_mcp_surface_is_behind_the_bearer_gate(
        self, tmp_path: Path
    ) -> None:
        unauthenticated = {k: v for k, v in _HEADERS.items() if k != "Authorization"}
        async with _mcp_client(tmp_path / "mem") as client:
            response = await client.post(
                "/mcp", json=_INITIALIZE, headers=unauthenticated
            )
        assert response.status_code == 401


class TestMcpBodyCeiling:
    """The one ceiling covers `/mcp` too, in the §18 envelope — not the
    SDK's own plain-text 413 (IN-3's second half)."""

    async def test_a_declared_oversize_post_is_the_envelope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ceiling, "MAX_REQUEST_BYTES", 256)
        padded = {**_INITIALIZE, "pad": "x" * 300}
        async with _mcp_client(tmp_path / "mem") as client:
            response = await client.post("/mcp", json=padded, headers=_HEADERS)
        assert response.status_code == 413
        error = response.json()["error"]
        assert error["code"] == "value_error"
        assert "exceeds 256 bytes" in error["message"]

    async def test_a_chunked_oversize_post_is_the_envelope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ceiling, "MAX_REQUEST_BYTES", 256)

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b"x" * 100

        async with _mcp_client(tmp_path / "mem") as client:
            response = await client.post("/mcp", content=chunks(), headers=_HEADERS)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "value_error"

    async def test_a_post_under_the_ceiling_still_initializes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ceiling, "MAX_REQUEST_BYTES", 4096)
        async with _mcp_client(tmp_path / "mem") as client:
            response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
        assert response.status_code == 200


class TestWithoutMounts:
    async def test_no_mounts_means_no_mcp_surface(self, tmp_path: Path) -> None:
        app = await build_app(FileStore(tmp_path / "mem"), token=TOKEN)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
        assert response.status_code == 404
