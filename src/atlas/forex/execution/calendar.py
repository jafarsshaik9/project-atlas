"""Intraday research windows; conservative exclusions are not historical market entitlements."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from atlas.config import AtlasError
from atlas.forex.audit_calendar import classification
from atlas.forex.execution.contracts import MINUTE, Run, Settings
from atlas.forex.quality import NY, minute_times
from atlas.forex.sessions import LONDON, NEW_YORK, local_instant


@dataclass(frozen=True)
class Window:
    opening: datetime
    flatten_at: datetime
    last_signal_at: datetime


def window(run: Run, settings: Settings) -> Window:
    if run.day.weekday() >= 5:
        raise AtlasError("FX_SESSION_WEEKEND")
    spans = {}
    for name, rule in (("LONDON", LONDON), ("NEW_YORK", NEW_YORK)):
        spans[name] = (
            local_instant(run.day, rule.opening, rule.timezone),
            local_instant(run.day, rule.closing, rule.timezone),
        )
    if run.session == "OVERLAP":
        opening, closing = max(s[0] for s in spans.values()), min(s[1] for s in spans.values())
    else:
        opening, closing = spans[run.session]
    # Close before the 16:45 NY research exclusion; never approach the 17:00 rollover.
    rollover_cutoff = local_instant(opening.astimezone(NY).date(), time(16, 45), "America/New_York")
    flatten = min(closing - timedelta(minutes=settings.flat_buffer_minutes), rollover_cutoff)
    if (opening.astimezone(NY).month, opening.astimezone(NY).day) in (
        (12, 24),
        (12, 25),
        (12, 31),
        (1, 1),
    ):
        raise AtlasError("FX_CONSERVATIVE_HOLIDAY_EXCLUSION")
    if not opening + 3 * MINUTE <= flatten:
        raise AtlasError("FX_NO_COMPLETE_EXECUTION_INTERVAL")
    if not run.coverage_start <= opening < flatten <= run.coverage_end:
        raise AtlasError("FX_INCOMPLETE_DECLARED_COVERAGE")
    if settings.costs.known_at > opening:
        raise AtlasError("FX_FUTURE_COST_CONFIGURATION")
    return Window(opening, flatten, flatten - 2 * MINUTE)


def market_reason(start: datetime) -> str | None:
    local = start.astimezone(NY)
    clock = local.hour * 60 + local.minute
    if 1005 <= clock < 1050:
        return "FX_ROLLOVER_EXCLUSION"
    observed = classification(start, observed=True)
    if observed != "OBSERVED":
        return observed
    absent = classification(start, observed=False)
    if absent != "UNRESOLVED_ABSENCE":
        return absent
    return None


def interval_reason(start: datetime, end: datetime) -> str | None:
    return next((r for t in minute_times(start, end) if (r := market_reason(t))), None)
