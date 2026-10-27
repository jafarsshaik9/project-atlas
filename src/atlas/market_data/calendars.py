"""Maintained cash-equity schedules, bounded and independent of fixture calendars."""

from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
from importlib.resources import files
from zoneinfo import ZoneInfo

from atlas.config import AtlasError
from atlas.domain import TradingSession, Venue
from atlas.market_data.contracts import digest


class HistoricalCalendar:
    """Materialize only requested dates; library rules are versioned, not infallible."""

    def __init__(self, venue: Venue, start: date, end: date):
        if not isinstance(venue, Venue) or not date(2000, 1, 1) <= start <= end <= date(
            2030, 12, 31
        ):
            raise AtlasError("Historical calendar supports explicit dates from 2000 through 2030.")
        if (end - start).days > 368:
            raise AtlasError("Calendar request exceeds the 368-day materialization bound.")
        try:
            import exchange_calendars as xcals
        except ImportError:
            raise AtlasError(
                "Install the approved market-data calendar extra before calendar use."
            ) from None
        self.venue, self.start, self.end = venue, start, end
        self.library_version = version("exchange-calendars")
        self.calendar_name = "XLON" if venue == Venue.LSE else "XNYS"
        self.timezone_name = "Europe/London" if venue == Venue.LSE else "America/New_York"
        with files("tzdata.zoneinfo").joinpath(*self.timezone_name.split("/")).open("rb") as handle:
            self.zone = ZoneInfo.from_file(handle, key=self.timezone_name)
        # Add padding so an all-holiday/weekend request still constructs a valid calendar.
        calendar = xcals.get_calendar(
            self.calendar_name,
            start=start - timedelta(days=7),
            end=end + timedelta(days=7),
            side="left",
        )
        self._sessions = {}
        day = start
        while day <= end:
            if calendar.is_session(day.isoformat()):
                opened = calendar.session_open(day.isoformat()).to_pydatetime().astimezone(UTC)
                closed = calendar.session_close(day.isoformat()).to_pydatetime().astimezone(UTC)
                local_open, local_close = opened.astimezone(self.zone), closed.astimezone(self.zone)
                regular = (16, 30) if venue == Venue.LSE else (16, 0)
                session = TradingSession(
                    venue,
                    day,
                    opened,
                    closed,
                    self.timezone_name,
                    int(local_open.utcoffset().total_seconds() // 60),
                    (local_close.hour, local_close.minute) < regular,
                )
            else:
                session = None
            self._sessions[day] = session
            day += timedelta(days=1)

    def session(self, venue: Venue, local_date: date):
        if venue != self.venue or local_date not in self._sessions:
            raise AtlasError("Date/venue outside materialized historical calendar coverage.")
        return self._sessions[local_date]

    def expected_starts(self, start: datetime, end: datetime):
        expected = set()
        for session in self._sessions.values():
            if session is None:
                continue
            current = max(start, session.open)
            stop = min(end, session.close)
            while current < stop:
                expected.add(current)
                current += timedelta(minutes=1)
        return expected

    def sessions(self):
        return tuple(s for s in self._sessions.values() if s is not None)

    def provenance(self):
        entries = [
            {
                "date": day.isoformat(),
                "open": s.open.isoformat() if s else None,
                "close": s.close.isoformat() if s else None,
                "half_day": s.half_day if s else None,
                "utc_offset_minutes": s.utc_offset_minutes if s else None,
            }
            for day, s in sorted(self._sessions.items())
        ]
        return {
            "source": "exchange_calendars",
            "version": self.library_version,
            "calendar": self.calendar_name,
            "venue": self.venue.value,
            "timezone": self.timezone_name,
            "timezone_database": version("tzdata"),
            "timezone_source": "Pinned tzdata projection; library UTC schedule fully fingerprinted",
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "interval_policy": "regular_cash_session_start_inclusive_end_exclusive",
            "nasdaq_alias": self.venue == Venue.NASDAQ,
            "coverage_note": "Community rules; exceptional-closure completeness not guaranteed.",
            "entries": entries,
        }

    @property
    def checksum(self):
        return digest(self.provenance())


class RecordedCalendar:
    """Revalidation uses the exact saved effective schedule, not today's changed rules."""

    def __init__(self, provenance):
        self._provenance = provenance
        self.venue = Venue(provenance["venue"])
        self._sessions = {}
        for row in provenance["entries"]:
            day = date.fromisoformat(row["date"])
            self._sessions[day] = (
                None
                if row["open"] is None
                else TradingSession(
                    self.venue,
                    day,
                    datetime.fromisoformat(row["open"]),
                    datetime.fromisoformat(row["close"]),
                    provenance["timezone"],
                    row["utc_offset_minutes"],
                    row["half_day"],
                )
            )

    expected_starts = HistoricalCalendar.expected_starts
    sessions = HistoricalCalendar.sessions
    session = HistoricalCalendar.session

    def provenance(self):
        return self._provenance
