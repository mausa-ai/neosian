"""The state process's ASGI app (DESIGN §18).

One neosian-owned Starlette composes four surfaces: `/health`
(unauthenticated — a container healthcheck needs no token), the
authenticated `/v1/capabilities` handshake, the fourteen store-shaped
routes plus the four `store/*` routes (NC4), and — when mounts are given — MCP over streamable HTTP at
`/mcp`, built from the same `create_memory_server` factory the stdio
transport uses, so all five transports execute one dispatcher.

Auth is a bearer-token table compared timing-safely on every request
except `/health`, each token naming the client it asserts (§20); a miss
is a plain 401 (no §18 envelope — auth is transport,
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
from neosian._foundation.memory.actor import parse_actor
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.server.ceiling import (
    MAX_REQUEST_BYTES,
    BodyCeilingMiddleware,
)
from neosian._foundation.server.portable_routes import portable_routes
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
from neosian._foundation.server.settings import DEFAULT_ACTOR
from neosian._foundation.server.tokens import Client, parse_clients
from neosian._foundation.server.wire import FORBIDDEN_CODE, WIRE_VERSION
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

    from starlette.routing import BaseRoute
    from starlette.types import ASGIApp, Receive, Scope, Send

    from neosian._foundation.memory.mounts import Mount

_HEALTH_PATH = "/health"
_MCP_PATH = "/mcp"


class BearerAuthMiddleware:
    """Pure ASGI: the token table, each compared timing-safely, `/health`
    exempt; a hit stamps the presenting client on the request — its actor
    (§20) and what it may reach (§18.4)."""

    def __init__(self, app: ASGIApp, *, clients: Mapping[str, Client]) -> None:
        self._app = app
        self._table = {
            f"Bearer {token}".encode(): client for token, client in clients.items()
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == _HEALTH_PATH:
            await self._app(scope, receive, send)
            return
        supplied = b""
        for name, value in scope["headers"]:
            if name == b"authorization":
                supplied = value
                break
        # Every candidate is compared — the table is small, and a
        # short-circuit would leak which token prefix matched.
        client: Client | None = None
        for expected, candidate in self._table.items():
            if secrets.compare_digest(supplied, expected):
                client = candidate
        if client is None:
            response = Response(
                "unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        if client.constrained and scope["path"] == _MCP_PATH:
            # The surface's reach is its **mounts** — operator config
            # chosen at serve time, not a parameter a request names — so
            # there is nothing for a prefix to match and no honest way to
            # fence it per token. The allowlist rule applies (§18.4).
            await _forbidden(
                "this token's allowance does not reach /mcp — the surface "
                "serves the operator's mounts, which no prefix can fence"
            )(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["actor"] = client.actor
        state["client"] = client
        await self._app(scope, receive, send)


def _forbidden(message: str) -> Response:
    """403 in the §18 envelope: authenticated, outside the allowance."""
    return JSONResponse(
        {"error": {"code": FORBIDDEN_CODE, "message": message, "details": {}}},
        status_code=403,
    )


async def _health(request: Request) -> Response:  # noqa: ARG001 - route shape
    return JSONResponse({"status": "ok"})


def _unconstrained(app: ASGIApp) -> ASGIApp:
    """`/mcp` for tokens with no allowance only (§18.4, IN-4).

    The surface's reach is its **mounts** — operator config chosen at
    serve time, not a parameter a request names — so there is nothing for
    a scope prefix to match against and no honest way to fence it per
    token. Refusing is the allowlist rule applied: what an allowance does
    not name, it does not reach.
    """

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        client: Client = scope["state"]["client"]
        if client.constrained:
            response = JSONResponse(
                {
                    "error": {
                        "code": FORBIDDEN_CODE,
                        "message": (
                            "this token's allowance does not reach /mcp — the "
                            "surface serves the operator's mounts, which no "
                            "prefix can fence"
                        ),
                        "details": {},
                    }
                },
                status_code=403,
            )
            await response(scope, receive, send)
            return
        await app(scope, receive, send)

    return guarded


def _capabilities(
    store: MemoryStore,
) -> Callable[[Request], Awaitable[Response]]:
    async def capabilities(request: Request) -> Response:
        return JSONResponse(
            {
                "wire_version": WIRE_VERSION,
                "neosian_version": metadata.version("neosian"),
                "backend": type(store).__name__,
                "supports_optimistic_concurrency": bool(
                    type(store).supports_optimistic_concurrency
                ),
                # Who the presented token makes the caller (§20) — the
                # prefix every write through this connection records.
                "client": request.state.actor,
            }
        )

    return capabilities


async def build_app(
    store: MemoryStore,
    *,
    token: str,
    mounts: Sequence[Mount] = (),
    actor: str | None = DEFAULT_ACTOR,
) -> Starlette:
    """Build the state process's app over one both-seams store.

    `token` is the raw `NEOSIAN_SERVE_TOKEN` value — one token or the
    per-client table (`tokens.py`). The caller owns the store's lifetime
    (the ledger #33 rule); the app never closes it. `mounts` gates the
    MCP surface: without them the process serves the store-shaped API
    only — a RemoteStore-only deployment has no natural scope to mount.
    """
    clients = parse_clients(token)  # refuses an empty or malformed table
    if actor is not None:
        parse_actor(actor)  # the /mcp surface's own identity (§20)
    if not isinstance(store, ConversationStore):
        raise ConfigurationError(
            f"{type(store).__name__} does not implement ConversationStore — "
            "the state process serves both seams (DESIGN §18)"
        )

    routes: list[BaseRoute] = [
        Route(_HEALTH_PATH, _health, methods=["GET"]),
        Route("/v1/capabilities", _capabilities(store), methods=["GET"]),
        *store_routes(store, store),
        *portable_routes(store),
    ]

    manager: StreamableHTTPSessionManager | None = None
    if mounts:
        # The stdio factory, reused verbatim: the state set — memory and
        # recall_turn — one dispatcher, every transport (ledger #50, §21.7).
        from neosian._foundation.mcp.server import create_memory_server

        mcp_server = await create_memory_server(
            MemoryConfig(store=store, mounts=tuple(mounts)),
            actor=actor,
            conversations=store,
        )
        # security_settings stays None: host filtering is a reverse
        # proxy's job and the bearer gate covers rebinding (§18).
        # The SDK's own body limit is raised to ours: the ceiling
        # middleware is outer and counts first, so one limit, one shape.
        manager = StreamableHTTPSessionManager(
            app=mcp_server, max_request_body_size=MAX_REQUEST_BYTES
        )
        routes.append(Route(_MCP_PATH, endpoint=StreamableHTTPASGIApp(manager)))

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
            Middleware(BearerAuthMiddleware, clients=clients),
            Middleware(BodyCeilingMiddleware),
        ],
        lifespan=lifespan,
    )
