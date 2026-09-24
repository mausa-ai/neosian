"""A third substrate: neosian memory and conversations in one SQLite file.

The worked example of `neosian docs stores`: a store neosian does not
ship (FileStore and PostgresStore are the two first-party substrates,
SQLite stays community custody) that passes both shipped conformance
kits, `tests/unit/examples/test_sqlite_store.py`. Copy the file; it is
yours.

Usage:
    uv run python examples/sqlite_store.py memory.sqlite

Shape. One connection on one file: every mutation is a `BEGIN IMMEDIATE`
transaction, every read one statement, all of it inline on the event
loop under one `asyncio.Lock` (indexed point work on a local file, the
FileStore precedent). The schema is the shipped Postgres one in SQLite
terms, applied idempotently at construction: the reference stores apply
theirs by an explicit act, a one-file example opens ready. Timestamps
are fixed-width ISO-8601 `Z` strings so that text order is time order,
flags are integers, `extra` and `messages` are JSON text.

`supports_optimistic_concurrency` is True for a reason of SQLite's own:
`BEGIN IMMEDIATE` takes the database's single writer lock across
processes, so a second writer's `expected_version` check reads the
committed version and fails, and the `(scope, path, version)` primary
key is the second guard. Not on a network filesystem, where SQLite's
locking is not reliable.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, cast

from neosian import Message
from neosian.conversation import (
    CONVERSATION_FORMAT_VERSION,
    ConversationFormatUnsupportedError,
    ConversationProjection,
    ConversationStore,
    ConversationTurn,
    ProjectionKind,
    message_from_json,
    message_to_json,
    parse_conversation_id,
)
from neosian.memory import (
    MEMORY_FORMAT_VERSION,
    Clock,
    MemoryAction,
    MemoryConflictError,
    MemoryDocument,
    MemoryDocumentNotFoundError,
    MemoryEntry,
    MemoryFormatUnsupportedError,
    MemoryRedaction,
    MemoryStore,
    MemoryVersion,
    SystemClock,
    parse_scope,
    validate_document_path,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS neosian_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version   INTEGER NOT NULL);
INSERT OR IGNORE INTO neosian_schema (singleton, version) VALUES (1, 1);
CREATE TABLE IF NOT EXISTS memories (
    scope TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
    version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    actor TEXT, redacted INTEGER NOT NULL DEFAULT 0,
    neosian_format INTEGER NOT NULL DEFAULT 1, extra TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (scope, path));
CREATE TABLE IF NOT EXISTS memory_versions (
    scope TEXT NOT NULL, path TEXT NOT NULL, version INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('created', 'modified', 'deleted')),
    content TEXT NOT NULL, actor TEXT, created_at TEXT NOT NULL,
    redacted INTEGER NOT NULL DEFAULT 0, neosian_format INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (scope, path, version));
CREATE INDEX IF NOT EXISTS memory_versions_scope_time
    ON memory_versions (scope, created_at);
CREATE TABLE IF NOT EXISTS memory_redactions (
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, path TEXT, actor TEXT,
    count INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
    conversation_id TEXT NOT NULL, turn INTEGER NOT NULL, messages TEXT NOT NULL,
    created_at TEXT NOT NULL, neosian_format INTEGER NOT NULL DEFAULT 1, actor TEXT,
    PRIMARY KEY (conversation_id, turn));
CREATE TABLE IF NOT EXISTS projections (
    id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, turn INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('log', 'digest', 'epoch')),
    text TEXT NOT NULL, span INTEGER NOT NULL DEFAULT 1,
    neosian_format INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS projections_order
    ON projections (conversation_id, turn, span, id);
"""

_DOCUMENT_COLUMNS = (
    "content, version, created_at, updated_at, actor, redacted, neosian_format, extra"
)
_VERSION_COLUMNS = (
    "path, version, action, content, actor, created_at, redacted, neosian_format"
)
_UPSERT_DOCUMENT = """
    INSERT INTO memories (scope, path, content, version, created_at, updated_at,
                          actor, redacted, neosian_format, extra)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (scope, path) DO UPDATE SET
        content = excluded.content, version = excluded.version,
        updated_at = excluded.updated_at, actor = excluded.actor,
        redacted = excluded.redacted, neosian_format = excluded.neosian_format
"""


def _stamp(moment: datetime) -> str:
    """Fixed width, so text order is time order (`Z` sorts after a dot)."""
    utc = moment.astimezone(UTC).isoformat(timespec="microseconds")
    return utc.replace("+00:00", "Z")


def _moment(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("naive timestamp")
    return parsed.astimezone(UTC)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _window(since: datetime | None, limit: int | None) -> None:
    if since is not None and since.tzinfo is None:
        raise ValueError("since must be timezone-aware (UTC)")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")


def _cursor(after: int, limit: int | None) -> None:
    if after < 0:
        raise ValueError(f"after must be >= 0, got {after}")
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")


def _bound(limit: int | None) -> int:
    return -1 if limit is None else limit  # SQLite: a negative LIMIT is no limit


class SqliteStore(MemoryStore, ConversationStore):
    """Both storage seams over one SQLite file."""

    supports_optimistic_concurrency: ClassVar[bool] = True

    def __init__(self, path: str | Path, *, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self._db = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        self._db.executescript(_SCHEMA)
        self._clock = clock if clock is not None else SystemClock()
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self._db.close()

    # Memory: documents ----------------------------------------------------

    async def read(self, scope: str, path: str) -> MemoryDocument | None:
        scope = parse_scope(scope)
        validate_document_path(path)
        return self._document(scope, path)

    async def write(
        self,
        scope: str,
        path: str,
        content: str,
        *,
        actor: str | None = None,
        expected_version: int | None = None,
    ) -> MemoryDocument:
        scope = parse_scope(scope)
        validate_document_path(path)
        async with self._lock:
            with self._transaction():
                existing = self._document(scope, path)
                if expected_version is not None:
                    if existing is None:
                        raise MemoryConflictError(
                            scope,
                            path,
                            "document_absent",
                            expected_version=expected_version,
                        )
                    if existing.version != expected_version:
                        raise MemoryConflictError(
                            scope,
                            path,
                            "version_mismatch",
                            expected_version=expected_version,
                            actual_version=existing.version,
                        )
                now = self._now()
                document = MemoryDocument(
                    scope=scope,
                    path=path,
                    content=content,
                    version=self._next_version(scope, path, existing),
                    created_at=existing.created_at if existing else now,
                    updated_at=now,
                    actor=actor,
                    extra=MappingProxyType(dict(existing.extra) if existing else {}),
                )
                action: MemoryAction = "modified" if existing else "created"
                self._version_row(
                    scope, path, document.version, action, content, actor, now
                )
                self._upsert(document)
                return document

    async def delete(self, scope: str, path: str, *, actor: str | None = None) -> bool:
        scope = parse_scope(scope)
        validate_document_path(path)
        async with self._lock:
            with self._transaction():
                existing = self._document(scope, path)
                if existing is None:
                    return False
                self._version_row(
                    scope,
                    path,
                    self._next_version(scope, path, existing),
                    "deleted",
                    existing.content,
                    actor,
                    self._now(),
                    existing.redacted,
                )
                self._db.execute(
                    "DELETE FROM memories WHERE scope = ? AND path = ?", (scope, path)
                )
                return True

    async def rename(
        self, scope: str, src: str, dst: str, *, actor: str | None = None
    ) -> MemoryDocument:
        scope = parse_scope(scope)
        validate_document_path(src)
        validate_document_path(dst)
        async with self._lock:
            with self._transaction():
                existing = self._document(scope, src)
                if existing is None:
                    raise MemoryDocumentNotFoundError(scope, src)
                occupied = self._db.execute(
                    "SELECT 1 FROM memories WHERE scope = ? AND path = ?", (scope, dst)
                ).fetchone()
                if occupied is not None:
                    raise MemoryConflictError(scope, dst, "destination_exists")
                now = self._now()
                self._version_row(
                    scope,
                    src,
                    self._next_version(scope, src, existing),
                    "deleted",
                    existing.content,
                    actor,
                    now,
                    existing.redacted,
                )
                moved = MemoryDocument(
                    scope=scope,
                    path=dst,
                    content=existing.content,
                    version=self._next_version(scope, dst, None),
                    created_at=now,
                    updated_at=now,
                    actor=actor,
                    redacted=existing.redacted,
                    extra=MappingProxyType(dict(existing.extra)),
                )
                self._version_row(
                    scope,
                    dst,
                    moved.version,
                    "created",
                    moved.content,
                    actor,
                    now,
                    moved.redacted,
                )
                self._upsert(moved)
                self._db.execute(
                    "DELETE FROM memories WHERE scope = ? AND path = ?", (scope, src)
                )
                return moved

    async def list_documents(
        self, scope: str, *, prefix: str = ""
    ) -> tuple[MemoryEntry, ...]:
        scope = parse_scope(scope)
        rows = self._db.execute(
            "SELECT path, version, created_at, updated_at, redacted, neosian_format"
            " FROM memories WHERE scope = ? AND substr(path, 1, ?) = ?"
            " ORDER BY path COLLATE BINARY",
            (scope, len(prefix), prefix),
        ).fetchall()
        return tuple(self._entry(scope, row) for row in rows)

    async def versions(
        self, scope: str, path: str, *, limit: int = 50
    ) -> tuple[MemoryVersion, ...]:
        scope = parse_scope(scope)
        validate_document_path(path)
        if limit < 0:
            raise ValueError("limit must be >= 0")
        rows = self._db.execute(
            f"SELECT {_VERSION_COLUMNS} FROM memory_versions"
            " WHERE scope = ? AND path = ? ORDER BY version DESC LIMIT ?",
            (scope, path, limit),
        ).fetchall()
        return tuple(self._version(scope, row) for row in rows)

    async def redact(
        self, scope: str, *, path: str | None = None, actor: str | None = None
    ) -> int:
        scope = parse_scope(scope)
        if path is not None:
            validate_document_path(path)
        async with self._lock:
            with self._transaction():
                (matched,) = self._db.execute(
                    "SELECT count(*) FROM (SELECT path FROM memories"
                    " WHERE scope = ? AND (? IS NULL OR path = ?) UNION"
                    " SELECT path FROM memory_versions"
                    " WHERE scope = ? AND (? IS NULL OR path = ?))",
                    (scope, path, path, scope, path, path),
                ).fetchone()
                if not matched:
                    return 0
                for table in ("memories", "memory_versions"):
                    self._db.execute(  # content only; updated_at stays (C3)
                        f"UPDATE {table} SET content = '', redacted = 1"
                        " WHERE scope = ? AND (? IS NULL OR path = ?)",
                        (scope, path, path),
                    )
                self._db.execute(
                    "INSERT INTO memory_redactions"
                    " (scope, path, actor, count, created_at) VALUES (?, ?, ?, ?, ?)",
                    (scope, path, actor, matched, _stamp(self._now())),
                )
                return int(matched)

    async def history(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryVersion, ...]:
        scope = parse_scope(scope)
        _window(since, limit)
        floor = None if since is None else _stamp(since)
        rows = self._db.execute(
            f"SELECT {_VERSION_COLUMNS} FROM memory_versions"
            " WHERE scope = ? AND (? IS NULL OR created_at >= ?)"
            " ORDER BY created_at DESC, path COLLATE BINARY, version DESC LIMIT ?",
            (scope, floor, floor, _bound(limit)),
        ).fetchall()
        return tuple(self._version(scope, row) for row in rows)

    async def redactions(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryRedaction, ...]:
        scope = parse_scope(scope)
        _window(since, limit)
        floor = None if since is None else _stamp(since)
        rows = self._db.execute(
            "SELECT path, actor, created_at, count FROM memory_redactions"
            " WHERE scope = ? AND (? IS NULL OR created_at >= ?)"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (scope, floor, floor, _bound(limit)),
        ).fetchall()
        return tuple(
            MemoryRedaction(
                path=path, actor=actor, created_at=_moment(created_at), count=count
            )
            for path, actor, created_at, count in rows
        )

    # Conversations: turns and projections ---------------------------------

    async def append_turn(
        self,
        conversation_id: str,
        messages: Sequence[Message],
        *,
        actor: str | None = None,
    ) -> ConversationTurn:
        conversation_id = parse_conversation_id(conversation_id)
        if not messages:
            raise ValueError("a turn must carry at least one message")
        encoded = _dump([message_to_json(message) for message in messages])
        async with self._lock:
            with self._transaction():
                turn = self._last_turn(conversation_id) + 1
                created_at = self._now()
                self._db.execute(
                    "INSERT INTO turns (conversation_id, turn, messages, created_at,"
                    " neosian_format, actor) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        conversation_id,
                        turn,
                        encoded,
                        _stamp(created_at),
                        CONVERSATION_FORMAT_VERSION,
                        actor,
                    ),
                )
        return ConversationTurn(
            conversation_id=conversation_id,
            turn=turn,
            messages=tuple(messages),
            created_at=created_at,
            actor=actor,
        )

    async def read_turns(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationTurn, ...]:
        conversation_id = parse_conversation_id(conversation_id)
        _cursor(after, limit)
        rows = self._db.execute(
            "SELECT turn, messages, created_at, neosian_format, actor FROM turns"
            " WHERE conversation_id = ? AND turn > ? ORDER BY turn LIMIT ?",
            (conversation_id, after, _bound(limit)),
        ).fetchall()
        return tuple(self._turn(conversation_id, row) for row in rows)

    async def last_turn_number(self, conversation_id: str) -> int:
        return self._last_turn(parse_conversation_id(conversation_id))

    async def append_projections(
        self, conversation_id: str, entries: Sequence[ConversationProjection]
    ) -> None:
        conversation_id = parse_conversation_id(conversation_id)
        if not entries:
            return
        async with self._lock:
            with self._transaction():
                self._db.executemany(
                    "INSERT INTO projections (conversation_id, turn, kind, text, span,"
                    " neosian_format) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            conversation_id,
                            entry.turn,
                            entry.kind,
                            entry.text,
                            entry.span,
                            CONVERSATION_FORMAT_VERSION,
                        )
                        for entry in entries
                    ],
                )

    async def read_projections(
        self, conversation_id: str, *, after: int = 0, limit: int | None = None
    ) -> tuple[ConversationProjection, ...]:
        conversation_id = parse_conversation_id(conversation_id)
        _cursor(after, limit)
        rows = self._db.execute(
            "SELECT turn, kind, text, span, neosian_format FROM projections"
            " WHERE conversation_id = ? AND turn > ? ORDER BY turn, span, id LIMIT ?",
            (conversation_id, after, _bound(limit)),
        ).fetchall()
        return tuple(self._projection(conversation_id, row) for row in rows)

    # Plumbing -------------------------------------------------------------

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Clock returned a naive datetime (ECOSYSTEM §9)")
        return now

    def _last_turn(self, conversation_id: str) -> int:
        (last,) = self._db.execute(
            "SELECT COALESCE(MAX(turn), 0) FROM turns WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return int(last)

    def _next_version(
        self, scope: str, path: str, existing: MemoryDocument | None
    ) -> int:
        """One past the highest number ever issued for the path, a planted
        document with no history included."""
        (top,) = self._db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM memory_versions"
            " WHERE scope = ? AND path = ?",
            (scope, path),
        ).fetchone()
        return max(int(top), existing.version if existing else 0) + 1

    def _version_row(
        self,
        scope: str,
        path: str,
        version: int,
        action: MemoryAction,
        content: str,
        actor: str | None,
        at: datetime,
        redacted: bool = False,
    ) -> None:
        self._db.execute(
            "INSERT INTO memory_versions (scope, path, version, action, content,"
            " actor, created_at, redacted, neosian_format)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope,
                path,
                version,
                action,
                content,
                actor,
                _stamp(at),
                int(redacted),
                1,
            ),
        )

    def _upsert(self, document: MemoryDocument) -> None:
        self._db.execute(
            _UPSERT_DOCUMENT,
            (
                document.scope,
                document.path,
                document.content,
                document.version,
                _stamp(document.created_at),
                _stamp(document.updated_at),
                document.actor,
                int(document.redacted),
                MEMORY_FORMAT_VERSION,
                _dump(dict(document.extra)),
            ),
        )

    def _document(self, scope: str, path: str) -> MemoryDocument | None:
        row = self._db.execute(
            f"SELECT {_DOCUMENT_COLUMNS} FROM memories WHERE scope = ? AND path = ?",
            (scope, path),
        ).fetchone()
        if row is None:
            return None
        content, version, created_at, updated_at, actor, redacted, declared, extra = row
        _check_format(scope, path, declared)
        loaded = json.loads(extra)
        if not isinstance(loaded, dict):
            raise MemoryFormatUnsupportedError(scope, path, "extra is not an object")
        return MemoryDocument(
            scope=scope,
            path=path,
            content=content,
            version=version,
            created_at=_memory_moment(scope, path, created_at),
            updated_at=_memory_moment(scope, path, updated_at),
            actor=actor,
            redacted=bool(redacted),
            extra=MappingProxyType(cast("dict[str, Any]", loaded)),
        )

    def _entry(self, scope: str, row: tuple[Any, ...]) -> MemoryEntry:
        path, version, created_at, updated_at, redacted, declared = row
        _check_format(scope, path, declared)
        return MemoryEntry(
            path=path,
            version=version,
            created_at=_memory_moment(scope, path, created_at),
            updated_at=_memory_moment(scope, path, updated_at),
            redacted=bool(redacted),
        )

    def _version(self, scope: str, row: tuple[Any, ...]) -> MemoryVersion:
        path, version, action, content, actor, created_at, redacted, declared = row
        _check_format(scope, path, declared)
        return MemoryVersion(
            path=path,
            version=version,
            action=cast(MemoryAction, action),
            content=content,
            actor=actor,
            created_at=_memory_moment(scope, path, created_at),
            redacted=bool(redacted),
        )

    def _turn(self, conversation_id: str, row: tuple[Any, ...]) -> ConversationTurn:
        turn, encoded, created_at, declared, actor = row
        where = f"turn row {turn}"
        _check_turn_format(conversation_id, where, declared)
        try:
            decoded: Any = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ConversationFormatUnsupportedError(
                conversation_id, f"malformed {where}"
            ) from exc
        if not isinstance(decoded, list) or not decoded:
            raise ConversationFormatUnsupportedError(
                conversation_id, f"{where} has no message array"
            )
        try:
            messages = tuple(message_from_json(item) for item in decoded)
            moment = _moment(created_at)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConversationFormatUnsupportedError(
                conversation_id, f"{where} has an undecodable field"
            ) from exc
        return ConversationTurn(
            conversation_id=conversation_id,
            turn=turn,
            messages=messages,
            created_at=moment,
            actor=actor,
        )

    def _projection(
        self, conversation_id: str, row: tuple[Any, ...]
    ) -> ConversationProjection:
        turn, kind, text, span, declared = row
        where = f"projection row (turn {turn})"
        _check_turn_format(conversation_id, where, declared)
        try:
            return ConversationProjection(
                turn=turn, kind=cast(ProjectionKind, kind), text=text, span=span
            )
        except ValueError as exc:
            raise ConversationFormatUnsupportedError(
                conversation_id, f"{where} has invalid fields"
            ) from exc


def _check_format(scope: str, path: str, declared: int) -> None:
    if not 1 <= declared <= MEMORY_FORMAT_VERSION:
        raise MemoryFormatUnsupportedError(
            scope, path, f"neosian_format {declared} is not supported"
        )


def _memory_moment(scope: str, path: str, text: str) -> datetime:
    try:
        return _moment(text)
    except ValueError as exc:
        raise MemoryFormatUnsupportedError(scope, path, f"{exc}: {text!r}") from exc


def _check_turn_format(conversation_id: str, where: str, declared: int) -> None:
    if not 1 <= declared <= CONVERSATION_FORMAT_VERSION:
        raise ConversationFormatUnsupportedError(
            conversation_id, f"{where} declares unsupported format {declared}"
        )


async def main(argv: list[str]) -> None:
    store = SqliteStore(argv[1] if len(argv) > 1 else "memory.sqlite")
    try:
        document = await store.write("user:demo", "tea", "takes tea", actor="cli:demo")
        print(f"wrote /{document.path} v{document.version} into {store.path}")
        for entry in await store.list_documents("user:demo"):
            print(f"  {entry.path}  v{entry.version}  {entry.updated_at.isoformat()}")
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
