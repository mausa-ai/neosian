"""The deterministic clock every store suite injects."""

from datetime import UTC, datetime, timedelta


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
