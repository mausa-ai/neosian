"""Tests for the injectable Clock (shared/clock.py)."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neosian._foundation.shared.clock import Clock, SystemClock


class ManualClock:
    """A hand-cranked clock for deterministic tests."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class TestSystemClock:
    def test_now_is_tz_aware_utc(self) -> None:
        now = SystemClock().now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(SystemClock(), Clock)


class TestManualClock:
    def test_satisfies_the_protocol_and_advances(self) -> None:
        clock = ManualClock(datetime(2026, 8, 19, tzinfo=UTC))
        assert isinstance(clock, Clock)
        before = clock.now()
        clock.advance(timedelta(seconds=5))
        assert clock.now() - before == timedelta(seconds=5)


@pytest.mark.unit
def test_no_naive_clock_call_in_the_library() -> None:
    """Every clock neosian writes is tz-aware (ECOSYSTEM §9; the review's
    EC-3/6/7/8): no `datetime.now()` or `utcnow()` call site survives."""
    root = Path(__file__).resolve().parents[3] / "neosian"
    naive = re.compile(r"datetime\.(now\(\)|utcnow\()")
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if naive.search(line)
    ]
    assert offenders == []
