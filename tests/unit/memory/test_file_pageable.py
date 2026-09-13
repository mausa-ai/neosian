"""FileStore pages its four listings (NQ2 slice C, §8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from neosian._foundation.memory.file import FileStore
from tests.support.clock import ManualClock

from .paging import FrozenClock, PagingSuite


class TestFilePaging(PagingSuite):
    @pytest.fixture
    def store(self, tmp_path: Path) -> FileStore:
        return FileStore(tmp_path / "memory", clock=ManualClock())


class TestFilePagingOnTies(PagingSuite):
    @pytest.fixture
    def store(self, tmp_path: Path) -> FileStore:
        return FileStore(tmp_path / "memory", clock=FrozenClock())
