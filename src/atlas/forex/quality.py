"""Bounded daily Forex diagnostics. Candle components are not synchronous quotes."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

from atlas.market_data.contracts import digest

NY = ZoneInfo("America/New_York")
MINUTE = timedelta(minutes=1)
BINS = (
    Decimal(0),
    Decimal(".5"),
    Decimal(1),
    Decimal(2),
    Decimal(5),
    Decimal(10),
    Decimal(25),
    Decimal(50),
    Decimal(100),
)
SCHEDULE = "OANDA_PUBLISHED_NY_1705_1659_ASSUMED_HISTORICAL_V1"


def closure(stamp):
    local = stamp.astimezone(NY)
    minute = local.hour * 60 + local.minute
    day = local.weekday()
    if day == 5 or (day == 4 and minute >= 1019) or (day == 6 and minute < 1025):
        return "EXPECTED_WEEKEND_CLOSURE_ASSUMED"
    if 1019 <= minute < 1025:
        return "EXPECTED_DAILY_BREAK_ASSUMED"
    return None


def minute_times(start, end):
    while start < end:
        yield start
        start += MINUTE


@lru_cache(maxsize=2048)
def open_minutes(start, end):
    return sum(closure(t) is None for t in minute_times(start, end))


def add_spread(stats, value):
    stats["count"] += 1
    stats["sum"] = str(Decimal(stats["sum"]) + value)
    stats["min"] = str(value if stats["min"] is None else min(Decimal(stats["min"]), value))
    stats["max"] = str(value if stats["max"] is None else max(Decimal(stats["max"]), value))
    index = next((i for i, bound in enumerate(BINS) if value < bound), len(BINS))
    stats["histogram"][index] += 1


def new_spreads():
    return {"count": 0, "sum": "0", "min": None, "max": None, "histogram": [0] * (len(BINS) + 1)}


def evaluate(plan, bars):
    """At most one UTC date; warnings retain unusual valid observations."""
    if plan.end - plan.start > timedelta(days=1):
        raise ValueError("Daily quality report exceeds memory bound.")
    observed = {b.start for b in bars}
    counts, warnings, hourly, missing = Counter(), Counter(), {}, []
    for stamp in minute_times(plan.start, plan.end):
        classification = closure(stamp)
        if stamp not in observed:
            code = classification or "UNEXPLAINED_MISSING_MINUTE"
            counts[code] += 1
            if missing and missing[-1]["kind"] == code and missing[-1]["end"] == stamp.isoformat():
                missing[-1]["end"] = (stamp + MINUTE).isoformat()
            else:
                missing.append(
                    {"start": stamp.isoformat(), "end": (stamp + MINUTE).isoformat(), "kind": code}
                )
        elif classification:
            warnings["OBSERVED_DURING_ASSUMED_CLOSURE"] += 1
    previous = None
    critical = Counter()
    for bar in bars:
        if not bar.complete or any(x is None for x in (bar.bid, bar.ask, bar.mid)):
            critical["INCOMPLETE_OR_MISSING_COMPONENT"] += 1
            continue
        if previous and bar.start <= previous.start:
            critical["DUPLICATE_OR_NONMONOTONIC"] += 1
        spread = (bar.ask.c - bar.bid.c) / plan.instrument.pip
        add_spread(hourly.setdefault(str(bar.start.hour), new_spreads()), spread)
        if any(
            -price.as_tuple().exponent > plan.instrument.display_precision
            for component in (bar.bid, bar.ask, bar.mid)
            for price in (component.o, component.h, component.l, component.c)
        ):
            warnings["PRECISION_BEYOND_DISPLAY_METADATA_REVIEW"] += 1
        # Endpoints need not coincide: flag rather than invent simultaneous quotes.
        if spread < 0 or bar.ask.o < bar.bid.o:
            warnings["ASYNC_ENDPOINT_CROSS_REVIEW"] += 1
        if not bar.bid.l <= bar.mid.c <= bar.ask.h:
            warnings["MID_OUTSIDE_COMPONENT_ENVELOPE_REVIEW"] += 1
        if spread > 25:
            warnings["WIDE_INDICATIVE_SPREAD_OVER_25_PIPS"] += 1
        if (bar.mid.h - bar.mid.l) / plan.instrument.pip > 100:
            warnings["RANGE_OVER_100_PIPS_REVIEW"] += 1
        if previous and abs(bar.mid.o - previous.mid.c) / plan.instrument.pip > 50:
            warnings["PRICE_DISCONTINUITY_OVER_50_PIPS_REVIEW"] += 1
        previous = bar
    if not bars and open_minutes(plan.start, plan.end):
        critical["NO_OBSERVATIONS_DURING_EXPECTED_OPEN"] += 1
    report = {
        "schema": "forex-daily-quality-v1",
        "schedule": SCHEDULE,
        "schedule_status": "PUBLISHED_PATTERN_NOT_ACCOUNT_OR_HOLIDAY_VERIFIED",
        "instrument": plan.instrument.name,
        "start": plan.start.isoformat(),
        "end": plan.end.isoformat(),
        "bars": len(bars),
        "counts": dict(counts),
        "warnings": dict(warnings),
        "critical": dict(critical),
        "status": "FAIL" if critical else "PASS_WITH_WARNINGS",
        "missing_intervals": missing,
        "spread_by_utc_hour": hourly,
        "spread_basis": "ASK_CLOSE_MINUS_BID_CLOSE_INDICATIVE_NOT_SIMULTANEOUS",
        "histogram_upper_bounds_pips": [str(v) for v in BINS] + ["INFINITY"],
        "availability": "UNKNOWN_ORIGINAL_PUBLICATION_TIMES",
    }
    return report | {"checksum": digest(report)}
