"""The JSONL version sidecar: one row per mutation, full content (C5).

The sidecar is the version counter's source of truth — it outlives the
document file across deletes, which is what makes re-created documents
continue their numbering. A malformed line raises, never skips: skipping
would silently corrupt the counter and let a version number be reused.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from neosian._foundation.memory.paths import validate_document_path
from neosian._foundation.memory.types import (
    MEMORY_FORMAT_VERSION,
    MemoryAction,
    MemoryRedaction,
    MemoryVersion,
)
from neosian._foundation.shared import fileio
from neosian._foundation.shared.exceptions import (
    MemoryFormatUnsupportedError,
    MemoryPathInvalidError,
)
from neosian._foundation.shared.fileio import private_mkdir

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_ACTIONS = ("created", "modified", "deleted")


def atomic_write(file: Path, text: str) -> None:
    """A private, atomic document write (ledger #126) — never partial.

    Shared with the document writer in file.py; a crash mid-rewrite must
    not destroy an audit trail.
    """
    private_mkdir(file.parent)
    fileio.atomic_write(file, text)


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
    """Append one complete row in a single write, fsync'd: the sidecar is
    written before the document (file.py), and the row must be on disk
    before the document can claim its number — otherwise a crash between
    the two could let the number be reused."""
    private_mkdir(file.parent)
    fileio.append_line(file, _render_row(row), fsync=True)


def rewrite_rows(file: Path, rows: Sequence[MemoryVersion]) -> None:
    """Atomically replace the whole sidecar (redaction only); the temp
    file is fsync'd before the rename."""
    private_mkdir(file.parent)
    fileio.atomic_write(file, "".join(_render_row(row) for row in rows), fsync=True)


def logical_paths(versions_dir: Path) -> list[str]:
    """Every document path with a sidecar under `versions_dir`, sorted —
    deleted documents included (the sidecar outlives the document).
    Foreign files with ungrammatical names are skipped, as in listings."""
    paths: list[str] = []
    if not versions_dir.is_dir():
        return paths
    for journal_file in versions_dir.rglob("*.jsonl"):
        relative = journal_file.relative_to(versions_dir)
        logical = "/".join((*relative.parts[:-1], relative.name[: -len(".jsonl")]))
        try:
            validate_document_path(logical)
        except MemoryPathInvalidError:
            continue
        paths.append(logical)
    return sorted(paths)


def newest_first(rows: list[MemoryVersion]) -> list[MemoryVersion]:
    """The ledger's order (DESIGN §20): `created_at` desc, then path,
    then version desc — two stable sorts, so ties stay deterministic."""
    rows.sort(key=lambda row: (row.path, -row.version))
    rows.sort(key=lambda row: row.created_at, reverse=True)
    return rows


def append_redaction(trail: Path, act: MemoryRedaction) -> None:
    """One erasure act on the scope's trail (`redactions.jsonl`)."""
    line = json.dumps(
        {
            "ts": act.created_at.isoformat().replace("+00:00", "Z"),
            "actor": act.actor,
            "path": act.path,
            "count": act.count,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    private_mkdir(trail.parent)
    fileio.append_line(trail, line + "\n")


def read_redactions(trail: Path, *, scope: str) -> tuple[MemoryRedaction, ...]:
    """The trail oldest first; a missing trail is an empty history."""
    if not trail.is_file():
        return ()
    acts: list[MemoryRedaction] = []
    with trail.open(encoding="utf-8", newline="") as handle:
        for number, line in enumerate(handle, start=1):
            acts.append(_parse_redaction(line, number, scope=scope, trail=trail))
    return tuple(acts)


def _parse_redaction(
    line: str, number: int, *, scope: str, trail: Path
) -> MemoryRedaction:
    where = f"redaction trail line {number}"
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MemoryFormatUnsupportedError(
            scope, trail.name, f"malformed {where}"
        ) from exc
    if not isinstance(data, dict):
        raise MemoryFormatUnsupportedError(
            scope, trail.name, f"{where} is not an object"
        )
    ts, actor, path, count = (data.get(k) for k in ("ts", "actor", "path", "count"))
    if (
        not isinstance(ts, str)
        or not (actor is None or isinstance(actor, str))
        or not (path is None or isinstance(path, str))
        or not isinstance(count, int)
        or isinstance(count, bool)
    ):
        raise MemoryFormatUnsupportedError(
            scope, trail.name, f"{where} has invalid fields"
        )
    try:
        created_at = datetime.fromisoformat(ts)
    except ValueError as exc:
        raise MemoryFormatUnsupportedError(
            scope, trail.name, f"{where} has an unreadable timestamp"
        ) from exc
    if created_at.tzinfo is None:
        raise MemoryFormatUnsupportedError(
            scope, trail.name, f"{where} has a naive timestamp"
        )
    return MemoryRedaction(path=path, actor=actor, created_at=created_at, count=count)


def since_window(since: datetime | None, limit: int | None) -> None:
    """The ledger reads' argument checks (C4 on `since`; a negative limit
    is the same programmer error `versions` raises)."""
    if since is not None and since.tzinfo is None:
        raise ValueError("since must be timezone-aware (UTC)")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")


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
