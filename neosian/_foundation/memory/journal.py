"""The JSONL version sidecar: one row per mutation, full content (C5).

The sidecar is the version counter's source of truth — it outlives the
document file across deletes, which is what makes re-created documents
continue their numbering. A malformed line raises, never skips: skipping
would silently corrupt the counter and let a version number be reused.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from neosian._foundation.memory.types import (
    MEMORY_FORMAT_VERSION,
    MemoryAction,
    MemoryVersion,
)
from neosian._foundation.shared.exceptions import MemoryFormatUnsupportedError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_ACTIONS = ("created", "modified", "deleted")


def atomic_write(file: Path, text: str) -> None:
    """Write via a same-directory temp file + os.replace — never partial.

    Shared with the document writer in file.py; a crash mid-rewrite must
    not destroy an audit trail.
    """
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=file.parent, prefix=".neosian-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, file)
    except BaseException:
        os.unlink(temp_name)
        raise


def read_rows(file: Path, *, scope: str, path: str) -> tuple[MemoryVersion, ...]:
    """Read all rows, oldest first; a missing sidecar is an empty history."""
    if not file.is_file():
        return ()
    rows: list[MemoryVersion] = []
    with file.open(encoding="utf-8", newline="") as handle:
        for number, line in enumerate(handle, start=1):
            rows.append(_parse_row(line, number, scope=scope, path=path))
    return tuple(rows)


def append_row(file: Path, row: MemoryVersion) -> None:
    """Append one complete row in a single write."""
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8", newline="") as handle:
        handle.write(_render_row(row))


def rewrite_rows(file: Path, rows: Sequence[MemoryVersion]) -> None:
    """Atomically replace the whole sidecar (redaction only)."""
    atomic_write(file, "".join(_render_row(row) for row in rows))


def next_version(rows: Sequence[MemoryVersion]) -> int:
    """The next monotonic version number for this document path."""
    return rows[-1].version + 1 if rows else 1


def _render_row(row: MemoryVersion) -> str:
    data = {
        "neosian_format": MEMORY_FORMAT_VERSION,
        "path": row.path,
        "version": row.version,
        "action": row.action,
        "content": row.content,
        "actor": row.actor,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
        "redacted": row.redacted,
    }
    return json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n"


def _parse_row(line: str, number: int, *, scope: str, path: str) -> MemoryVersion:
    where = f"sidecar line {number}"
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MemoryFormatUnsupportedError(scope, path, f"malformed {where}") from exc
    if not isinstance(data, dict):
        raise MemoryFormatUnsupportedError(scope, path, f"{where} is not an object")

    declared = data.get("neosian_format")
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 1:
        raise MemoryFormatUnsupportedError(scope, path, f"{where} missing format")
    if declared > MEMORY_FORMAT_VERSION:
        raise MemoryFormatUnsupportedError(
            scope, path, f"{where} format {declared} is newer than supported"
        )

    version = data.get("version")
    action = data.get("action")
    content = data.get("content")
    actor = data.get("actor")
    redacted = data.get("redacted", False)
    created_raw = data.get("created_at")
    row_path = data.get("path")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or action not in _ACTIONS
        or not isinstance(content, str)
        or not (actor is None or isinstance(actor, str))
        or not isinstance(redacted, bool)
        or not isinstance(created_raw, str)
        or not isinstance(row_path, str)
    ):
        raise MemoryFormatUnsupportedError(scope, path, f"{where} has invalid fields")
    try:
        created_at = datetime.fromisoformat(created_raw)
    except ValueError as exc:
        raise MemoryFormatUnsupportedError(
            scope, path, f"{where} has an unreadable timestamp"
        ) from exc
    if created_at.tzinfo is None:
        raise MemoryFormatUnsupportedError(
            scope, path, f"{where} has a naive timestamp"
        )

    return MemoryVersion(
        path=row_path,
        version=version,
        action=cast(MemoryAction, action),
        content=content,
        actor=actor,
        created_at=created_at,
        redacted=redacted,
    )
