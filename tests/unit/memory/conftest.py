"""Shared fixtures for the memory unit tests."""

from pathlib import Path

import pytest

from neosian._foundation.memory.file import FileStore
from tests.support.clock import ManualClock


@pytest.fixture
def manual_clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def store(tmp_path: Path, manual_clock: ManualClock) -> FileStore:
    return FileStore(tmp_path / "memory", clock=manual_clock)
