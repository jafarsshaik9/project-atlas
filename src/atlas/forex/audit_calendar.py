"""Versioned diagnostic overlay, not authority to trade or a source-data rewrite."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from atlas.forex.quality import NY, closure

VERSION = "OANDA_OBSERVED_BOUNDARIES_HOLIDAY_EVIDENCE_V2"
LONDON = ZoneInfo("Europe/London")


def session(stamp):
    london, new_york = stamp.astimezone(LONDON), stamp.astimezone(NY)
    lon = london.weekday() < 5 and 8 <= london.hour < 17
    ny = new_york.weekday() < 5 and 8 <= new_york.hour < 17
    return (
        "OVERLAP" if lon and ny else "LONDON_ONLY" if lon else "NEW_YORK_ONLY" if ny else "OUTSIDE"
    )


def near_dst(stamp, zone):
    # Within three days of an offset change, including US/UK mismatch weeks.
    return (stamp - timedelta(days=3)).astimezone(zone).utcoffset() != (
        stamp + timedelta(days=3)
    ).astimezone(zone).utcoffset()


def classification(stamp, *, observed):
    original = closure(stamp)
    local = stamp.astimezone(NY)
    clock = local.hour * 60 + local.minute
    if observed:
        if original and (
            (local.weekday() < 5 and clock == 1019)
            or (local.weekday() in (0, 1, 2, 3, 6) and clock == 1024)
        ):
            return "OBSERVED_BOUNDARY_REVIEW_NOT_CONFIRMED_TRADABLE"
        return "OBSERVED_DURING_ASSUMED_CLOSURE_REVIEW" if original else "OBSERVED"
    if original:
        return original
    # Narrow intervals only. UK official historical entries support these dates;
    # Christmas 2024 has regional supporting evidence but remains a hypothesis.
    day = local.date()
    holiday = day if clock < 1025 else day + timedelta(days=1)
    if str(holiday) in ("2025-01-01", "2025-12-25", "2026-01-01") and (
        day == holiday or clock >= 1019
    ):
        return "HOLIDAY_CLOSURE_SUPPORTED_NOT_ACCOUNT_VERIFIED"
    if str(holiday) == "2024-12-25" and (day == holiday or clock >= 1019):
        return "HOLIDAY_CLOSURE_REGIONAL_EVIDENCE_UNVERIFIED"
    return "UNRESOLVED_ABSENCE"
