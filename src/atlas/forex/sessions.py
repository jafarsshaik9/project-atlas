"""Explicit research windows, never a claim of broker trading-hour entitlement."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from atlas.config import AtlasError


def local_instant(day, clock, zone):
    naive = datetime.combine(day, clock)
    tz = ZoneInfo(zone)
    a, b = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if (
        a.utcoffset() != b.utcoffset()
        or a.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive
    ):
        raise AtlasError("Ambiguous or nonexistent session boundary; explicit resolution required.")
    return a.astimezone(UTC)


@dataclass(frozen=True)
class ResearchSession:
    name: str
    timezone: str
    opening: time
    closing: time

    def window(self, day: date, *, confirmed_windows=None):
        if day.weekday() >= 5:
            return None
        start = local_instant(day, self.opening, self.timezone)
        end = local_instant(
            day + timedelta(days=self.closing <= self.opening), self.closing, self.timezone
        )
        # Explicit instrument-specific hours/holidays must fully contain the research window.
        if confirmed_windows is None or not any(
            a <= start < end <= b for a, b in confirmed_windows
        ):
            raise AtlasError("Instrument trading hours unverified for this research window.")
        return start, end


LONDON = ResearchSession("London research", "Europe/London", time(8), time(17))
NEW_YORK = ResearchSession("New York research", "America/New_York", time(8), time(17))


def overlap(first, second):
    if first is None or second is None:
        return None
    start, end = max(first[0], second[0]), min(first[1], second[1])
    return (start, end) if start < end else None


def crosses_rollover(start, end):
    """Conservative NY 17:00 research boundary; actual financing requires division terms."""
    if end < start:
        raise AtlasError("Position time moved backwards.")
    tz = ZoneInfo("America/New_York")
    day = start.astimezone(tz).date()
    while day <= end.astimezone(tz).date():
        if start < local_instant(day, time(17), "America/New_York") <= end:
            return True
        day += timedelta(days=1)
    return False
