"""One request-body ceiling for every surface (DESIGN §18, IN-3).

A body over `MAX_REQUEST_BYTES` is 413 in the §18 envelope before the
app reads a byte it cannot hold — the twelve store routes and `/mcp`
alike, one middleware inside the bearer gate so an unauthenticated
oversize body stays a 401. A declared Content-Length over the ceiling
is refused outright; a chunked body is counted as it streams and cut
off past the ceiling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from neosian._foundation.server.routes import envelope

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from neosian._foundation.server.sdk import Response

# Generous because a turn may carry base64 media blocks; bounded because
# the body is buffered whole before it is decoded.
MAX_REQUEST_BYTES: Final = 64 * 1024 * 1024


class _BodyTooLarge(Exception):
    """Raised from the counting `receive`; never a Starlette HTTPException,
    so the router re-raises it to this middleware untouched."""


def _too_large(limit: int) -> Response:
    return envelope(f"request body exceeds {limit} bytes", status=413)


class BodyCeilingMiddleware:
    """Pure ASGI: the ceiling on every HTTP request, `/mcp` included."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit = MAX_REQUEST_BYTES  # read per request — tests patch it
        for name, value in scope["headers"]:
            if name == b"content-length" and value.isdigit() and int(value) > limit:
                await _too_large(limit)(scope, receive, send)
                return
        seen = 0
        started = False

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > limit:
                raise _BodyTooLarge
            return message

        async def guarded(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self._app(scope, counted, guarded)
        except _BodyTooLarge:
            if started:  # headers are out: a torn response the server logs
                raise
            await _too_large(limit)(scope, receive, send)
