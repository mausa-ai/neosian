"""Value types for the memory storage seam (DESIGN §8).

Frozen records crossing the `MemoryStore` boundary. Field lists are part
of the host-facing contract: every store implementation must populate
them, and the conformance kit asserts against them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

# The storage format marker (`neosian_format`); stores refuse to read
# anything newer (C6) — the escape hatch for evolving the schema without
# breaking existing stores.
MEMORY_FORMAT_VERSION: Final = 1

# What a version row records. `redact` is deliberately absent: redaction
# clears content in place and appends no row (C3).
MemoryAction = Literal["created", "modified", "deleted"]

_EMPTY_EXTRA: Final[Mapping[str, Any]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    """The current state of one memory document.

    `extra` carries unknown storage-envelope keys, preserved verbatim
    across writes (C6). It is output-only: `MemoryStore.write` accepts no
    extra keys — they enter only from the substrate (hand edits, future
    format versions).
    """

    scope: str
    path: str
    content: str
    version: int
    created_at: datetime
    updated_at: datetime
    actor: str | None
    redacted: bool = False
    extra: Mapping[str, Any] = field(default=_EMPTY_EXTRA)


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """One row of a scope listing. Deleted documents never appear;
    redacted ones appear with `redacted=True`."""

    path: str
    version: int
    created_at: datetime
    updated_at: datetime
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class MemoryVersion:
    """One audit row: a mutation of one document, with full content (C5).

    After redaction the skeleton survives — `content` is empty and
    `redacted` is True, but path, version, actor and timestamp remain.
    """

    path: str
    version: int
    action: MemoryAction
    content: str
    actor: str | None
    created_at: datetime
    redacted: bool = False
