"""Per-client tokens — the daemon asserts who writes, and what it reaches
(NL, DESIGN §20; the allowance is NQ2, IN-4).

`NEOSIAN_SERVE_TOKEN` carries either one bare token (today's shape: the
one client is `client:default`) or a table, `actor=token[,actor=token…]`,
each actor an identity in the §20 grammar. The bearer gate maps the
presented token to its client and stamps it on the request; the routes
record `<client>` for a bodyless write and `<client>/<body-actor>` when
the client names a sub-identity — the client's word is subordinate,
never trusted alone, and no client can claim another's prefix.

An actor may carry an **allowance**, written left of the `=` so the token
itself stays opaque to the last character:

    client:alice@user:alice+alice-=tok1   scopes under `user:alice`,
                                         ids starting `alice-`
    client:reader@user:alice=tok2        those scopes; no conversation
    client:ops=tok3                      everything, exactly as before

Both prefixes are **literal text**, matched with `str.startswith` against
the parameter the request names. Neither is interpreted: the scope
grammar is still validated only by the store, and a conversation id is
still one opaque flat segment (§9.4, ledger #123/#139) — what an
allowance encodes is an operator's own naming convention, declared by a
human, never inferred by the library. An allowance is an **allowlist**:
what it does not name is refused, so `reader@…` above reaches no
conversation, and a client carrying any allowance is refused the four
`store/*` routes outright — those move the store whole and restore
verbatim under the archive's own actors (ledger #166), which is the
trust an allowance withdraws.

The body tail stays opaque, like every store's actor: a RemoteStore
client speaking a host's own convention (bare conversation ids) is not
refused at the daemon — what the daemon asserts is the prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from neosian._foundation.memory.actor import parse_actor
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    MemoryActorInvalidError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_CLIENT: Final = "client:default"
_HINT: Final = (
    "the value is one token, or actor[@scope-prefix][+id-prefix]=token[,…] "
    "(DESIGN §20, §18.4)"
)


@dataclass(frozen=True, slots=True)
class Client:
    """A presented token's identity and what it may reach.

    `scopes`/`conversations` are None when unconstrained — the shape every
    token had before IN-4, and the one a bare token still has.
    """

    actor: str
    scopes: str | None = None
    conversations: str | None = None

    @property
    def constrained(self) -> bool:
        return self.scopes is not None or self.conversations is not None

    def may_reach_scope(self, scope: str) -> bool:
        return self.scopes is None or scope.startswith(self.scopes)

    def may_reach_conversation(self, conversation_id: str) -> bool:
        if not self.constrained:
            return True
        prefix = self.conversations
        return prefix is not None and conversation_id.startswith(prefix)


def parse_clients(value: str) -> Mapping[str, Client]:
    """`NEOSIAN_SERVE_TOKEN` → {token: Client}; refuses, never guesses."""
    if not value:
        raise ConfigurationError(
            "a bearer token is required — the state process never serves "
            "unauthenticated (set NEOSIAN_SERVE_TOKEN)"
        )
    if "=" not in value:
        return {value: Client(DEFAULT_CLIENT)}
    clients: dict[str, Client] = {}
    for index, entry in enumerate(value.split(","), start=1):
        left, sep, token = entry.partition("=")
        if not sep or not token:
            raise ConfigurationError(
                f"NEOSIAN_SERVE_TOKEN entry {index} is malformed: {_HINT}"
            )
        client = _client(left, index)
        if token in clients or any(c.actor == client.actor for c in clients.values()):
            raise ConfigurationError(
                f"NEOSIAN_SERVE_TOKEN entry {index} repeats a token or an actor"
            )
        clients[token] = client
    return clients


def _client(left: str, index: int) -> Client:
    """`actor[@scope-prefix][+id-prefix]` — the allowance rides the actor
    side so a token may still hold any character but `,`."""
    head, plus, conversations = left.partition("+")
    actor, at, scopes = head.partition("@")
    try:
        parse_actor(actor)
    except MemoryActorInvalidError as exc:
        raise ConfigurationError(
            f"NEOSIAN_SERVE_TOKEN entry {index}: actor {actor!r} {exc.reason}"
        ) from exc
    for delimiter, name, prefix in ((at, "scope", scopes), (plus, "id", conversations)):
        if delimiter and not prefix:
            raise ConfigurationError(
                f"NEOSIAN_SERVE_TOKEN entry {index}: the {name} prefix is empty "
                f"— omit the delimiter to leave that half unconstrained: {_HINT}"
            )
    return Client(actor, scopes=scopes or None, conversations=conversations or None)


def stamp(client: str, body: str | None) -> str:
    """The recorded actor: the asserted client, its body-supplied tail under it."""
    return client if body is None else f"{client}/{body}"
