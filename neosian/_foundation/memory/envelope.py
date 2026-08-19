"""The FileStore storage envelope: exact frontmatter codec.

Not the shared `parse_frontmatter` — that strips the body. Here the body
must round-trip byte-exact (content that itself begins with `---`
included), so only the FIRST fence pair is the envelope and everything
after the closing fence line is content, verbatim.

Unknown envelope keys are preserved across writes (C6) and surface as
`MemoryDocument.extra`. Timestamps are ISO-8601 `Z` strings; a naive
timestamp is refused, never coerced (ECOSYSTEM §9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml

from neosian._foundation.memory.types import _EMPTY_EXTRA, MEMORY_FORMAT_VERSION
from neosian._foundation.shared.exceptions import MemoryFormatUnsupportedError

if TYPE_CHECKING:
    from collections.abc import Mapping

_FENCE = "---"


@dataclass(frozen=True, slots=True)
class Envelope:
    """The storage metadata wrapped around one document's content."""

    version: int
    created_at: datetime
    updated_at: datetime
    actor: str | None = None
    redacted: bool = False
    extra: Mapping[str, Any] = field(default=_EMPTY_EXTRA)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def render(envelope: Envelope, content: str) -> str:
    """Serialize envelope + content; `parse` is its exact inverse."""
    data: dict[str, Any] = {
        "neosian_format": MEMORY_FORMAT_VERSION,
        "version": envelope.version,
        "created_at": _timestamp(envelope.created_at),
        "updated_at": _timestamp(envelope.updated_at),
    }
    if envelope.actor is not None:
        data["actor"] = envelope.actor
    if envelope.redacted:
        data["redacted"] = True
    data.update(envelope.extra)
    # width: the default 80 would line-wrap long preserved values and
    # break round-trip; sort_keys=False keeps the reserved-first order.
    rendered = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, width=1_000_000
    )
    return f"{_FENCE}\n{rendered}{_FENCE}\n{content}"


def parse(text: str, *, scope: str, path: str) -> tuple[Envelope, str]:
    """Split a stored file into envelope and byte-exact content.

    Raises:
        MemoryFormatUnsupportedError: On a missing or malformed envelope,
            a newer `neosian_format`, or a naive timestamp.
    """
    lines = text.split("\n")
    if lines[0].rstrip("\r") != _FENCE:
        raise MemoryFormatUnsupportedError(scope, path, "missing envelope fence")
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r") == _FENCE:
            break
    else:
        raise MemoryFormatUnsupportedError(scope, path, "unterminated envelope fence")
    raw = "\n".join(lines[1:index])
    content = "\n".join(lines[index + 1 :])

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise MemoryFormatUnsupportedError(
            scope, path, "invalid envelope YAML"
        ) from exc
    if not isinstance(data, dict):
        raise MemoryFormatUnsupportedError(scope, path, "envelope is not a mapping")

    declared = data.pop("neosian_format", None)
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 1:
        raise MemoryFormatUnsupportedError(scope, path, "missing neosian_format")
    if declared > MEMORY_FORMAT_VERSION:
        raise MemoryFormatUnsupportedError(
            scope, path, f"format {declared} is newer than {MEMORY_FORMAT_VERSION}"
        )

    version = data.pop("version", None)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise MemoryFormatUnsupportedError(scope, path, "missing or invalid version")

    created_at = _read_timestamp(
        data.pop("created_at", None), "created_at", scope, path
    )
    updated_at = _read_timestamp(
        data.pop("updated_at", None), "updated_at", scope, path
    )

    actor = data.pop("actor", None)
    if actor is not None and not isinstance(actor, str):
        raise MemoryFormatUnsupportedError(scope, path, "actor must be a string")
    redacted = data.pop("redacted", False)
    if not isinstance(redacted, bool):
        raise MemoryFormatUnsupportedError(scope, path, "redacted must be a boolean")

    envelope = Envelope(
        version=version,
        created_at=created_at,
        updated_at=updated_at,
        actor=actor,
        redacted=redacted,
        extra=data,
    )
    return envelope, content


def _read_timestamp(value: Any, key: str, scope: str, path: str) -> datetime:
    # YAML may hand back a str, a datetime, or (from a bare date) a date;
    # humans edit these files. Naive is rejected, never coerced.
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise MemoryFormatUnsupportedError(
                scope, path, f"unreadable {key} timestamp"
            ) from exc
    if not isinstance(value, datetime):
        # A bare YAML date parses to datetime.date — timezone-less, refused.
        raise MemoryFormatUnsupportedError(
            scope, path, f"missing or invalid {key} timestamp"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise MemoryFormatUnsupportedError(scope, path, f"naive {key} timestamp")
    return value
