"""Time. Business logic asks a `Clock` for "now" so demos and tests can run at a fixed moment.

Bangladesh has used UTC+6 all year since 2009, so a fixed offset is exact and avoids
needing a timezone database on Windows.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol

BDT = timezone(timedelta(hours=6), name="BDT")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SimClock:
    """A settable clock. It starts at `start` and then advances with real time unless `frozen`."""

    def __init__(self, start: datetime, *, frozen: bool = False) -> None:
        self._start = _aware(start)
        self._t0 = time.monotonic()
        self.frozen = frozen

    def now(self) -> datetime:
        if self.frozen:
            return self._start
        return self._start + timedelta(seconds=time.monotonic() - self._t0)

    def set(self, moment: datetime) -> None:
        self._start = _aware(moment)
        self._t0 = time.monotonic()

    def advance(self, delta: timedelta) -> None:
        self.set(self.now() + delta)


def _aware(dt: datetime) -> datetime:
    """Naive datetimes are read as Bangladesh local time."""
    return dt.replace(tzinfo=BDT) if dt.tzinfo is None else dt


def to_local(dt: datetime) -> datetime:
    return _aware(dt).astimezone(BDT)


def local_date(dt: datetime) -> date:
    return to_local(dt).date()


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC start (inclusive) and end (exclusive) of a Bangladesh calendar day."""
    start = datetime(day.year, day.month, day.day, tzinfo=BDT)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)
