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

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import Mount
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

    async def test_the_mcp_surface_is_behind_the_bearer_gate(
        self, tmp_path: Path
    ) -> None:
        unauthenticated = {k: v for k, v in _HEADERS.items() if k != "Authorization"}
        async with _mcp_client(tmp_path / "mem") as client:
            response = await client.post(
                "/mcp", json=_INITIALIZE, headers=unauthenticated
            )
        assert response.status_code == 401


class TestWithoutMounts:
    async def test_no_mounts_means_no_mcp_surface(self, tmp_path: Path) -> None:
        app = await build_app(FileStore(tmp_path / "mem"), token=TOKEN)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            response = await client.post("/mcp", json=_INITIALIZE, headers=_HEADERS)
        assert response.status_code == 404
