"""The actor grammar (DESIGN §20), validated — never interpreted by a store.

An actor names who wrote a row: `<kind>:<id>` segments joined by `/`,
optionally suffixed `#<turn>` — the scope grammar's image (ECOSYSTEM §2)
plus the turn-ref Conversation has stamped since NP. Kinds are open
convention (`conv`, `cli`, `mcp`, `serve`, `eval`, `claude-code`, …), and
the daemon composes `<client>/<body-actor>` so a client's word is always
subordinate to the identity its token asserted.

Enforcement is a convention, not a seam: neosian's own writers, the shell
and the daemon validate; the `MemoryStore` ABC keeps `actor` opaque, so a
host's bare conversation ids stay legal on its own store.
"""

from __future__ import annotations

import re
from typing import Final, NewType

from neosian._foundation.shared.exceptions import MemoryActorInvalidError

# A validated actor string. Typing courtesy, not a runtime guarantee.
Actor = NewType("Actor", str)

ACTOR_MAX_LENGTH: Final = 512  # the segments; a turn suffix rides on top
_MAX_SEGMENTS: Final = 8
_SEGMENT: Final = r"[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9_.-]{1,128}"

# Anchored \A…\Z: `$` would accept a trailing newline. Exported, so it
# must be safe under .match() in host code too.
ACTOR_PATTERN: Final = re.compile(rf"\A{_SEGMENT}(?:/{_SEGMENT})*(?:#[1-9][0-9]*)?\Z")


def parse_actor(value: str) -> Actor:
    """Validate `value` against the grammar and return it as an Actor.

    Raises:
        MemoryActorInvalidError: On any shape violation. Never normalizes.
    """
    segments_part, _, _turn = value.partition("#")
    if len(segments_part) > ACTOR_MAX_LENGTH:
        raise MemoryActorInvalidError(
            value, f"longer than {ACTOR_MAX_LENGTH} characters"
        )
    if not ACTOR_PATTERN.match(value):
        raise MemoryActorInvalidError(
            value, "does not match <kind>:<id>[/...][#<turn>]"
        )
    segments = segments_part.split("/")
    if len(segments) > _MAX_SEGMENTS:
        raise MemoryActorInvalidError(value, f"more than {_MAX_SEGMENTS} segments")
    for segment in segments:
        _, segment_id = segment.split(":", 1)
        if segment_id in (".", ".."):
            raise MemoryActorInvalidError(value, f"id {segment_id!r} is reserved")
    return Actor(value)


def actor_matches(actor: str | None, pattern: str) -> bool:
    """The audit filter: does `actor` fall under `pattern`?

    A match is segment-prefix containment with the turn suffix ignored —
    `claude-code:abc` matches `claude-code:abc`, `claude-code:abc#4` and
    `claude-code:abc/conv:x#2`, never `claude-code:abcd`. An unparseable
    stored actor (a host's bare id) matches only itself, verbatim.
    """
    if actor is None:
        return False
    if actor == pattern:
        return True
    head = actor.partition("#")[0]
    return head == pattern or head.startswith(pattern + "/")
