"""The four `store/*` routes — the `Portable` protocol over the wire
(NC4, §26.4; the `WIRE_VERSION` 3 step).

Restores are verbatim: the presenting client's actor stamp is *not*
applied — the rows' actors are the archive's truth, and a bearer holder
is already trusted to redact whole scopes (ledger #166). A backend
without the protocol answers every route with `ConfigurationError` in
the §18 envelope, naming itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neosian._foundation.memory.portable import Portable
from neosian._foundation.server.routes import endpoint
from neosian._foundation.server.sdk import Route
from neosian._foundation.server.wire_archive import (
    decode_conversation_archive,
    decode_scope_archive,
)
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def portable_routes(store: object) -> list[Route]:
    def portable() -> Portable:
        if not isinstance(store, Portable):
            raise ConfigurationError(
                f"{type(store).__name__} does not implement Portable — this "
                "backend cannot be moved whole (DESIGN §26.1)"
            )
        return store

    async def scopes(payload: dict[str, Any], client: str) -> dict[str, Any]:
        del payload, client  # a read records nobody
        return {"scopes": list(await portable().scopes())}

    async def conversations(payload: dict[str, Any], client: str) -> dict[str, Any]:
        del payload, client
        return {"conversations": list(await portable().conversations())}

    async def restore_scope(payload: dict[str, Any], client: str) -> dict[str, Any]:
        del client  # verbatim: the archive's actors, never the caller's
        await portable().restore_scope(decode_scope_archive(payload))
        return {}

    async def restore_conversation(
        payload: dict[str, Any], client: str
    ) -> dict[str, Any]:
        del client
        await portable().restore_conversation(decode_conversation_archive(payload))
        return {}

    handlers: dict[str, Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]] = {
        "store/scopes": scopes,
        "store/conversations": conversations,
        "store/restore_scope": restore_scope,
        "store/restore_conversation": restore_conversation,
    }
    return [
        Route(f"/v1/{name}", endpoint(handler), methods=["POST"])
        for name, handler in handlers.items()
    ]
