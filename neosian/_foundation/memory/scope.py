"""The scope grammar (ECOSYSTEM §2), validated — never interpreted.

A scope is `<type>:<id>` segments joined by `/`. Neosian validates shape
and never parses meaning: no containment, no inheritance, no routing on
type. Hierarchy is explicit mounts, chosen by the application.

`scope_segments` and `scope_directory` are the only scope decomposition
in the library; a test pins that nothing outside this module calls
`scope_segments` (and only `file_layout.py` calls `scope_directory`).
"""

from __future__ import annotations

import re
from typing import Final, NewType
from urllib.parse import quote

from neosian._foundation.shared.exceptions import MemoryScopeInvalidError

# A validated scope string. Typing courtesy, not a runtime guarantee —
# stores re-validate on every call.
Scope = NewType("Scope", str)

SCOPE_MAX_LENGTH: Final = 512
_MAX_SEGMENTS: Final = 8

# Anchored \A…\Z: `$` would accept a trailing newline. The pattern is
# exported, so it must be safe under .match() in host code too.
SCOPE_PATTERN: Final = re.compile(
    r"\A[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,128}"
    r"(?:/[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,128})*\Z"
)


def parse_scope(value: str) -> Scope:
    """Validate `value` against the grammar and return it as a Scope.

    Raises:
        MemoryScopeInvalidError: On any shape violation. Never normalizes:
            case, whitespace and length are the caller's problem.
    """
    if len(value) > SCOPE_MAX_LENGTH:
        raise MemoryScopeInvalidError(
            value, f"longer than {SCOPE_MAX_LENGTH} characters"
        )
    if not SCOPE_PATTERN.match(value):
        raise MemoryScopeInvalidError(value, "does not match <type>:<id>[/...]")
    segments = value.split("/")
    if len(segments) > _MAX_SEGMENTS:
        raise MemoryScopeInvalidError(value, f"more than {_MAX_SEGMENTS} segments")
    for segment in segments:
        _, segment_id = segment.split(":", 1)
        if segment_id in (".", ".."):
            raise MemoryScopeInvalidError(value, f"id {segment_id!r} is reserved")
    return Scope(value)


def scope_segments(scope: Scope) -> tuple[tuple[str, str], ...]:
    """Decompose a validated scope into (type, id) pairs.

    Exists for completeness of the grammar module; neosian itself never
    interprets a scope (pinned by test_encapsulation).
    """
    pairs = []
    for segment in scope.split("/"):
        segment_type, segment_id = segment.split(":", 1)
        pairs.append((segment_type, segment_id))
    return tuple(pairs)


def scope_directory(scope: Scope) -> tuple[str, ...]:
    """Encode a scope as filesystem directory components, one per segment.

    Percent-encoding keeps every component under filesystem name limits
    (a whole 512-char scope would not fit in one) and guarantees a `%3A`
    in each — no collision with FileStore's reserved names is possible.
    """
    return tuple(quote(segment, safe="") for segment in scope.split("/"))
