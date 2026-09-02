"""Per-client tokens — the daemon asserts who writes (NL, DESIGN §20).

`NEOSIAN_SERVE_TOKEN` carries either one bare token (today's shape: the
one client is `client:default`) or a table, `actor=token[,actor=token…]`,
each actor an identity in the §20 grammar. The bearer gate maps the
presented token to its actor and stamps it on the request; the routes
record `<client>` for a bodyless write and `<client>/<body-actor>` when
the client names a sub-identity — the client's word is subordinate,
never trusted alone, and no client can claim another's prefix.

The body tail stays opaque, like every store's actor: a RemoteStore
client speaking a host's own convention (bare conversation ids) is not
refused at the daemon — what the daemon asserts is the prefix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from neosian._foundation.memory.actor import parse_actor
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    MemoryActorInvalidError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_CLIENT: Final = "client:default"
_HINT: Final = "the value is one token, or actor=token[,actor=token…] (DESIGN §20)"


def parse_clients(value: str) -> Mapping[str, str]:
    """`NEOSIAN_SERVE_TOKEN` → {token: actor}; refuses, never guesses."""
    if not value:
        raise ConfigurationError(
            "a bearer token is required — the state process never serves "
            "unauthenticated (set NEOSIAN_SERVE_TOKEN)"
        )
    if "=" not in value:
        return {value: DEFAULT_CLIENT}
    clients: dict[str, str] = {}
    for index, entry in enumerate(value.split(","), start=1):
        actor, sep, token = entry.partition("=")
        if not sep or not token:
            raise ConfigurationError(
                f"NEOSIAN_SERVE_TOKEN entry {index} is malformed: {_HINT}"
            )
        try:
            parse_actor(actor)
        except MemoryActorInvalidError as exc:
            raise ConfigurationError(
                f"NEOSIAN_SERVE_TOKEN entry {index}: actor {actor!r} {exc.reason}"
            ) from exc
        if token in clients or actor in clients.values():
            raise ConfigurationError(
                f"NEOSIAN_SERVE_TOKEN entry {index} repeats a token or an actor"
            )
        clients[token] = actor
    return clients


def stamp(client: str, body: str | None) -> str:
    """The recorded actor: the asserted client, its body-supplied tail under it."""
    return client if body is None else f"{client}/{body}"
