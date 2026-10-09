"""The two erasure routes: the `Erasable` protocol over the wire (N8,
DESIGN §38, §18; the `WIRE_VERSION` 6 step).

`conversation/redact_turns` is fenced by its `conversation_id` like every
conversation route and records the act under the caller's client
(§20.4); `conversation/turn_redactions` is checked id by id over its
`conversations` list, and a read naming no list is the whole store,
refused for a constrained token (the `search_turns` rule). The trail is
bounded like a search, never a page. A backend without the protocol
answers both routes with `ConfigurationError` in the §18 envelope,
naming itself; the handshake's `erasable` says which.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neosian._foundation.conversation.erasable import Erasable
from neosian._foundation.server.paging import PAGE
from neosian._foundation.server.routes import endpoint
from neosian._foundation.server.sdk import Route
from neosian._foundation.server.tokens import stamp
from neosian._foundation.server.wire import (
    encode_turn_redaction,
    optional_int,
    optional_ints,
    optional_str,
    optional_strs,
    optional_timestamp,
    require_str,
)
from neosian._foundation.shared.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def erasure_routes(store: object) -> list[Route]:
    def erasable() -> Erasable:
        if not isinstance(store, Erasable):
            raise ConfigurationError(
                f"{type(store).__name__} does not implement Erasable — this "
                "backend's turns cannot be redacted (DESIGN §38)"
            )
        return store

    async def redact_turns(payload: dict[str, Any], client: str) -> dict[str, Any]:
        count = await erasable().redact_turns(
            require_str(payload, "conversation_id"),
            through=optional_int(payload, "through"),
            turns=optional_ints(payload, "turns"),
            actor=stamp(client, optional_str(payload, "actor")),
        )
        return {"count": count}

    async def turn_redactions(payload: dict[str, Any], client: str) -> dict[str, Any]:
        del client  # a read records nobody
        limit = optional_int(payload, "limit")
        if limit is not None and limit > PAGE:
            raise ValueError(
                f"the trail answers at most {PAGE} acts; narrow the window"
            )
        acts = await erasable().turn_redactions(
            conversations=optional_strs(payload, "conversations"),
            since=optional_timestamp(payload, "since"),
            limit=50 if limit is None else limit,
        )
        return {"redactions": [encode_turn_redaction(act) for act in acts]}

    handlers: dict[str, Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]] = {
        "conversation/redact_turns": redact_turns,
        "conversation/turn_redactions": turn_redactions,
    }
    return [
        Route(f"/v1/{name}", endpoint(handler), methods=["POST"])
        for name, handler in handlers.items()
    ]
