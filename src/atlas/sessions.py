"""Injectable UTC clock and explicit exchange sessions; unknown dates fail closed."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from atlas.config import AtlasError
from atlas.domain import MarketBar, TradingSession, Venue, utc


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass
class ReplayClock:
    timestamp: datetime

    def __post_init__(self) -> None:
        utc(self.timestamp)

    def now(self) -> datetime:
        return self.timestamp

    def advance(self, timestamp: datetime) -> None:
        if utc(timestamp) < self.timestamp:
            raise AtlasError("Replay clocks cannot move backwards.")
        self.timestamp = timestamp


class SessionCalendar(Protocol):
    def session(self, venue: Venue, local_date: date) -> TradingSession | None: ...


@dataclass(frozen=True)
class FixtureCalendar:
    """Entries explicitly distinguish a holiday (None) from an unknown date."""

    entries: tuple[tuple[Venue, date, TradingSession | None], ...]
    version: str = "synthetic-sessions-v1"

    def __post_init__(self) -> None:
        keys = [(venue, day) for venue, day, _ in self.entries]
        if len(keys) != len(set(keys)):
            raise AtlasError("Duplicate calendar entries.")
        for venue, day, session in self.entries:
            if session is not None and (session.venue != venue or session.local_date != day):
                raise AtlasError("Calendar/session mismatch.")

    def session(self, venue: Venue, local_date: date) -> TradingSession | None:
        for entry_venue, entry_day, session in self.entries:
            if entry_venue == venue and entry_day == local_date:
                return session
        raise AtlasError("Calendar date is unknown; new risk is disabled.")


def local_time(session: TradingSession, timestamp: datetime) -> datetime:
    return utc(timestamp).astimezone(timezone(timedelta(minutes=session.utc_offset_minutes)))


def bar_errors(
    bars: tuple[MarketBar, ...],
    session: TradingSession,
    as_of: datetime,
    instrument_id: str,
    interval: timedelta = timedelta(minutes=5),
) -> tuple[str, ...]:
    """Independently check every expected slot through the last visible bar."""
    utc(as_of)
    if not bars:
        return ("MISSING_BARS",)
    errors = set()
    visible = [bar for bar in bars if bar.visible_time <= as_of]
    if len(visible) != len(bars):
        errors.add("FUTURE_DATA")
    ordered = sorted(visible, key=lambda bar: bar.start)
    starts = [bar.start for bar in ordered]
    if len(starts) != len(set(starts)):
        errors.add("DUPLICATE_BARS")
    expected = session.open
    for bar in ordered:
        if bar.instrument_id != instrument_id:
            errors.add("INSTRUMENT_MISMATCH")
        if bar.start != expected or bar.end - bar.start != interval:
            errors.add("MISSING_BARS")
        if bar.start < session.open or bar.end > session.close:
            errors.add("OUTSIDE_SESSION")
        if not bar.complete or bar.end > as_of:
            errors.add("INCOMPLETE_BARS")
        if bar.quality:
            errors.add("BAD_DATA")
        expected = bar.end
    if ordered and as_of - ordered[-1].end > interval:
        errors.add("STALE_BARS")
    return tuple(sorted(errors))
