"""Shared fixtures for the memory unit tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neosian._foundation.memory.file import FileStore


class ManualClock:
    """A hand-cranked clock: every read advances by one second, so
    strict timestamp ordering is deterministic without sleeping."""

    def __init__(
        self, start: datetime = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)
    ) -> None:
        self._now = start

    def now(self) -> datetime:
        current = self._now
        self._now += timedelta(seconds=1)
        return current


@pytest.fixture
def manual_clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def store(tmp_path: Path, manual_clock: ManualClock) -> FileStore:
    return FileStore(tmp_path / "memory", clock=manual_clock)
