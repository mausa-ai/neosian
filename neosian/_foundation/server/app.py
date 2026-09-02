"""The state process's ASGI app (DESIGN §18).

One neosian-owned Starlette composes four surfaces: `/health`
(unauthenticated — a container healthcheck needs no token), the
authenticated `/v1/capabilities` handshake, the twelve store-shaped
routes, and — when mounts are given — MCP over streamable HTTP at
`/mcp`, built from the same `create_memory_server` factory the stdio
transport uses, so all five transports execute one dispatcher.

Auth is one bearer token compared timing-safely on every request except
`/health`; a miss is a plain 401 (no §18 envelope — auth is transport,
not a store error, and the client propagates it raw). Inside the gate,
one body ceiling covers every surface (`ceiling.py`). The SDK's own auth
stack is OAuth-resource-server shaped and deliberately unused (NM scope:
bearer only; TLS is a reverse proxy's job).

The MCP session manager owns its own lifespan: the SDK enters the MCP
server's lifespan once per manager, not per session — so on this
transport the ledger #51 instructions refresh happens once per process,
and a client wanting the live index calls `view /` (recorded in §18).
"""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from importlib import metadata
from typing import TYPE_CHECKING

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.server.ceiling import (
    MAX_REQUEST_BYTES,
    BodyCeilingMiddleware,
)
from neosian._foundation.server.routes import store_routes
from neosian._foundation.server.sdk import (
    JSONResponse,
    Middleware,
    Request,
    Response,
    Route,
    Starlette,
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)
from neosian._foundation.server.wire import WIRE_VERSION
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from starlette.routing import BaseRoute
    from starlette.types import ASGIApp, Receive, Scope, Send

    from neosian._foundation.memory.mounts import Mount

_HEALTH_PATH = "/health"


class BearerAuthMiddleware:
    """Pure ASGI: one token, compared timing-safely, `/health` exempt."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == _HEALTH_PATH:
            await self._app(scope, receive, send)
            return
        supplied = b""
        for name, value in scope["headers"]:
            if name == b"authorization":
                supplied = value
                break
        if not secrets.compare_digest(supplied, self._expected):
            response = Response(
                "unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


async def _health(request: Request) -> Response:  # noqa: ARG001 - route shape
    return JSONResponse({"status": "ok"})


def _capabilities(
    store: MemoryStore,
) -> Callable[[Request], Awaitable[Response]]:
    async def capabilities(request: Request) -> Response:  # noqa: ARG001
        return JSONResponse(
            {
                "wire_version": WIRE_VERSION,
                "neosian_version": metadata.version("neosian"),
                "backend": type(store).__name__,
                "supports_optimistic_concurrency": bool(
                    type(store).supports_optimistic_concurrency
                ),
            }
        )

    return capabilities


async def build_app(
    store: MemoryStore,
    *,
    token: str,
    mounts: Sequence[Mount] = (),
    actor: str | None = "serve",
) -> Starlette:
    """Build the state process's app over one both-seams store.

    The caller owns the store's lifetime (the ledger #33 rule); the app
    never closes it. `mounts` gates the MCP surface: without them the
    process serves the store-shaped API only — a RemoteStore-only
    deployment has no natural scope to mount.
    """
    if not token:
        raise ConfigurationError(
            "a bearer token is required — the state process never serves "
            "unauthenticated (set NEOSIAN_SERVE_TOKEN)"
        )
    if not isinstance(store, ConversationStore):
        raise ConfigurationError(
            f"{type(store).__name__} does not implement ConversationStore — "
            "the state process serves both seams (DESIGN §18)"
        )

    routes: list[BaseRoute] = [
        Route(_HEALTH_PATH, _health, methods=["GET"]),
        Route("/v1/capabilities", _capabilities(store), methods=["GET"]),
        *store_routes(store, store),
    ]

    manager: StreamableHTTPSessionManager | None = None
    if mounts:
        # The stdio factory, reused verbatim: one tool, one dispatcher,
        # every transport (ledger #50).
        from neosian._foundation.mcp.server import create_memory_server

        mcp_server = await create_memory_server(
            MemoryConfig(store=store, mounts=tuple(mounts)), actor=actor
        )
        # security_settings stays None: host filtering is a reverse
        # proxy's job and the bearer gate covers rebinding (§18).
        # The SDK's own body limit is raised to ours: the ceiling
        # middleware is outer and counts first, so one limit, one shape.
        manager = StreamableHTTPSessionManager(
            app=mcp_server, max_request_body_size=MAX_REQUEST_BYTES
        )
        routes.append(Route("/mcp", endpoint=StreamableHTTPASGIApp(manager)))

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:  # noqa: ARG001
        if manager is None:
            yield
            return
        async with manager.run():
            yield

    return Starlette(
        routes=routes,
        middleware=[
            Middleware(BearerAuthMiddleware, token=token),
            Middleware(BodyCeilingMiddleware),
        ],
        lifespan=lifespan,
    )
