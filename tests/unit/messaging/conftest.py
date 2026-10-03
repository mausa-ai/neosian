"""Deterministic time and a real local store for mailbox integration tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neosian import FileStore, MemoryConfig, Mount
from neosian.messaging import Mailbox

SCOPE = "user:test/proj:mail"


@dataclass
class Clock:
    value: datetime = datetime(2026, 10, 3, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def memory(tmp_path: Path, clock: Clock) -> MemoryConfig:
    return MemoryConfig(
        FileStore(tmp_path / "store", clock=clock),
        (
            Mount(SCOPE, "project"),
            Mount("user:test", "user"),
        ),
    )


@pytest.fixture
def mailbox(memory: MemoryConfig, clock: Clock) -> Mailbox:
    return Mailbox(memory, session="recipient", actor="test:reader", clock=clock)
