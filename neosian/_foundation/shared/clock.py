"""Injectable time source.

Every datetime neosian stores or returns is tz-aware UTC (ECOSYSTEM §9);
a naive datetime at any seam is a contract violation. Concrete stores take
a `Clock` at construction so tests control time deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A source of tz-aware UTC datetimes."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """The wall clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)
