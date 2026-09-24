"""A third substrate under both conformance kits (NC5): `examples/
sqlite_store.py` passes `MemoryStoreContract` and `ConversationStoreContract`
from the public facades with the planting hooks overridden, so nothing
skips; then the properties SQLite adds beyond the contract."""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from examples.sqlite_store import SqliteStore
from neosian.conversation.testing import ConversationStoreContract
from neosian.memory import MemoryConflictError, MemoryFormatUnsupportedError
from neosian.memory.testing import MemoryStoreContract
from tests.support.clock import ManualClock

_PLANT_TS = "2026-01-01T00:00:00.000000Z"
_SCOPE = "user:sqlite"


def _plant(store: SqliteStore, sql: str, params: tuple[Any, ...]) -> None:
    """Bypass the store: a second connection on the same file."""
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(sql, params)


def _decode(line: str) -> dict[str, Any] | None:
    """The kit's plant hooks speak raw JSONL lines: an object-shaped line
    maps field-per-column; an unparseable one is planted as
    `neosian_format = 0`, the typed-column form of a row with no readable
    format marker, which reads must refuse."""
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


@pytest.fixture
def store(tmp_path: Path, manual_clock: ManualClock) -> Iterator[SqliteStore]:
    store = SqliteStore(tmp_path / "memory.sqlite", clock=manual_clock)
    yield store
    store.close()


class TestSqliteMemoryContract(MemoryStoreContract):
    async def plant_raw_document(
        self,
        store: SqliteStore,  # type: ignore[override]
        scope: str,
        path: str,
        *,
        content: str,
        format_version: int,
        extra: Mapping[str, Any],
    ) -> None:
        _plant(
            store,
            "INSERT INTO memories (scope, path, content, version, created_at,"
            " updated_at, actor, redacted, neosian_format, extra)"
            " VALUES (?, ?, ?, 1, ?, ?, NULL, 0, ?, ?)",
            (
                scope,
                path,
                content,
                _PLANT_TS,
                _PLANT_TS,
                format_version,
                json.dumps(dict(extra)),
            ),
        )


class TestSqliteConversationContract(ConversationStoreContract):
    async def plant_raw_turn(
        self,
        store: SqliteStore,  # type: ignore[override]
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        data = _decode(line) or {"neosian_format": 0}
        _plant(
            store,
            "INSERT INTO turns (conversation_id, turn, messages, created_at,"
            " neosian_format) VALUES (?, ?, ?, ?, ?)",
            (
                conversation_id,
                data.get("turn", 1),
                json.dumps(data.get("messages", [])),
                data.get("created_at", _PLANT_TS),
                data.get("neosian_format", 0),
            ),
        )

    async def plant_raw_projection(
        self,
        store: SqliteStore,  # type: ignore[override]
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        data = _decode(line) or {"neosian_format": 0}
        _plant(
            store,
            "INSERT INTO projections (conversation_id, turn, kind, text, span,"
            " neosian_format) VALUES (?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                data.get("turn", 1),
                data.get("kind", "log"),
                data.get("text", ""),
                data.get("span", 1),
                data.get("neosian_format", 0),
            ),
        )


class HalfSecondClock:
    """Alternates whole and fractional seconds: the case where a stamp
    without fixed width would sort out of time order."""

    def __init__(self) -> None:
        self._now = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        current = self._now
        self._now += timedelta(milliseconds=500)
        return current


@pytest.mark.unit
class TestSqliteStoreSpecifics:
    """What the file adds beyond the contract."""

    async def test_two_instances_on_one_file_arbitrate(self, tmp_path: Path) -> None:
        """The flag is True for SQLite's reason: the second writer's check
        reads the committed version, whichever process wrote it."""
        file = tmp_path / "shared.sqlite"
        first, second = SqliteStore(file), SqliteStore(file)
        try:
            await first.write(_SCOPE, "doc", "one")
            moved = await second.write(_SCOPE, "doc", "two", expected_version=1)
            assert moved.version == 2
            with pytest.raises(MemoryConflictError) as caught:
                await first.write(_SCOPE, "doc", "three", expected_version=1)
            assert caught.value.reason == "version_mismatch"
            assert caught.value.actual_version == 2
        finally:
            first.close()
            second.close()

    async def test_fractional_stamps_keep_time_order(self, tmp_path: Path) -> None:
        store = SqliteStore(tmp_path / "clock.sqlite", clock=HalfSecondClock())
        try:
            for name in ("a", "b", "c"):
                await store.write(_SCOPE, name, name)
            rows = await store.history(_SCOPE)
            assert [row.path for row in rows] == ["c", "b", "a"]
            floor = datetime(2026, 8, 19, 10, 0, 0, 500_000, tzinfo=UTC)
            assert [row.path for row in await store.history(_SCOPE, since=floor)] == [
                "c",
                "b",
            ]
        finally:
            store.close()

    async def test_nul_content_round_trips(self, store: SqliteStore) -> None:
        """Where Postgres refuses U+0000 (ledger #38), SQLite keeps it."""
        await store.write(_SCOPE, "nul", "a\x00b")
        document = await store.read(_SCOPE, "nul")
        assert document is not None
        assert document.content == "a\x00b"

    async def test_a_planted_naive_timestamp_is_refused(
        self, store: SqliteStore
    ) -> None:
        _plant(
            store,
            "INSERT INTO memories (scope, path, content, version, created_at,"
            " updated_at, neosian_format) VALUES (?, 'doc', 'x', 1, ?, ?, 1)",
            (_SCOPE, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        with pytest.raises(MemoryFormatUnsupportedError):
            await store.read(_SCOPE, "doc")

    async def test_the_file_is_a_plain_sqlite_database(
        self, store: SqliteStore
    ) -> None:
        await store.write(_SCOPE, "doc", "x")
        with closing(sqlite3.connect(store.path)) as connection:
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            (rows,) = connection.execute(
                "SELECT count(*) FROM memory_versions"
            ).fetchone()
        assert {
            "memories",
            "memory_versions",
            "memory_redactions",
            "turns",
            "projections",
        } <= tables
        assert rows == 1

    async def test_a_second_open_reads_what_the_first_wrote(
        self, tmp_path: Path
    ) -> None:
        file = tmp_path / "persist.sqlite"
        first = SqliteStore(file)
        await first.write(_SCOPE, "doc", "kept")
        first.close()
        second = SqliteStore(file)
        try:
            document = await second.read(_SCOPE, "doc")
            assert document is not None
            assert (document.content, document.version) == ("kept", 1)
        finally:
            second.close()
