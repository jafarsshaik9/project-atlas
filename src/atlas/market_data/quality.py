"""Explicit historical quality findings; source rows are never filled or repaired."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from atlas.config import AtlasError
from atlas.market_data.contracts import (
    Adjustment,
    Availability,
    Finding,
    HistoricalBar,
    QualityReport,
)

# Bounds keep exact integer comparisons cheap even for adversarial numeric strings.
ARITHMETIC_POLICY = {
    "version": "exact-ratio-v1",
    "max_digits": 64,
    "max_abs_exponent": 64,
    "decimal_serialization": "uppercase-exponent-v1",
}


def bounded_decimal(value):
    parts = value.as_tuple()
    return (
        value.is_finite()
        and len(parts.digits) <= ARITHMETIC_POLICY["max_digits"]
        and abs(parts.exponent) <= ARITHMETIC_POLICY["max_abs_exponent"]
    )


def large_gap(opening, prior_close):
    # Exact positive rational comparison: no ambient Decimal precision or rounding.
    numerator, denominator = opening.as_integer_ratio()
    previous_numerator, previous_denominator = prior_close.as_integer_ratio()
    left = numerator * previous_denominator * 10
    right = previous_numerator * denominator
    return left > right * 13 or left < right * 7


def assess(request, pages, calendar, *, actions=(), action_coverage="UNKNOWN", heartbeat=None):
    counts = Counter()
    severity = {}
    descriptions = {}

    def issue(code, detail, level="FAIL", count=1):
        if count:
            counts[code] += count
            severity[code], descriptions[code] = level, detail

    mapping = request.instrument.mapping(request.provider, request.start, request.end)
    bars = []
    raw_count = 0
    last_massive_start = None
    for page in pages:
        body = page.body
        meta, rows = body.get("meta"), body.get("values")
        if not isinstance(meta, dict) or not isinstance(rows, list) or body.get("status") != "ok":
            issue("MALFORMED_RESPONSE", "Required market metadata/row collection/status is absent.")
            continue
        valid_meta = True
        if request.provider == "alpaca-sip":
            from atlas.market_data.alpaca import verify_evidence

            try:
                if (
                    page.original_availability != "unknown"
                    or page.normalization_order != "ascending"
                ):
                    raise ValueError
                verify_evidence(request, body)
            except (KeyError, ValueError, TypeError):
                issue("ALPACA_PROVENANCE_MISMATCH", "SIP raw source projection is inconsistent.")
                valid_meta = False
            issue(
                "ALPACA_METADATA_DECLARED",
                "Research symbol/listing metadata is declared, not dated-reference verified.",
                "WARNING",
            )
        if request.provider == "massive":
            from atlas.market_data.massive import verify_evidence

            try:
                if (
                    page.original_availability != "unknown"
                    or page.normalization_order != "ascending"
                ):
                    raise ValueError("Massive page provenance policy mismatch")
                verify_evidence(request, body)
            except (KeyError, ValueError, TypeError):
                issue(
                    "MASSIVE_PROVENANCE_MISMATCH",
                    "Dated reference identity or aggregate adjustment evidence is invalid.",
                )
                valid_meta = False
        for key, expected in (
            ("symbol", mapping.symbol),
            ("mic_code", mapping.mic.value),
            ("currency", mapping.currency_code),
            ("interval", "1min"),
            ("exchange_timezone", request.instrument.exchange_timezone),
        ):
            if meta.get(key) != expected:
                issue(
                    "METADATA_MISMATCH_" + key.upper(),
                    "Provider metadata contradicts the supplied instrument definition.",
                )
                valid_meta = False
        previous = None
        for row in rows:
            raw_count += 1
            if heartbeat and raw_count % 1000 == 0:
                heartbeat()
            if raw_count > 200000:
                raise ValueError("Historical normalization exceeds the 200000-row bound.")
            if not valid_meta:
                continue
            if not isinstance(row, dict):
                issue("MALFORMED_ROW", "Source row is not an object.")
                continue
            try:
                original = row["datetime"]
                if not isinstance(original, str):
                    raise ValueError
                if request.provider == "massive":
                    if not original.isascii() or not original.isdigit() or len(original) > 15:
                        raise ValueError
                    start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(original))
                    timestamp_zone = "UTC (provider Unix epoch milliseconds)"
                elif request.provider == "twelve-data":
                    start = datetime.strptime(original, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                    timestamp_zone = "UTC (explicit provider request parameter)"
                else:
                    start = datetime.fromisoformat(original)
                    if start.tzinfo is None or start.utcoffset() != timedelta(0):
                        raise ValueError
                    timestamp_zone = "UTC (explicit fixture timestamp)"
                    if request.provider == "alpaca-sip":
                        timestamp_zone = "UTC (provider RFC3339 bar-start timestamp)"
            except (KeyError, ValueError, TypeError, OverflowError):
                issue(
                    "INVALID_TIMESTAMP",
                    "Missing, malformed or ambiguous provider bar-start timestamp.",
                )
                continue
            if previous is not None and (
                (page.normalization_order == "ascending" and start < previous)
                or (page.normalization_order == "descending" and start > previous)
            ):
                issue(
                    "SOURCE_ORDER_VIOLATION",
                    "Page order contradicts the provider's documented convention.",
                )
            if request.provider in ("massive", "alpaca-sip"):
                if last_massive_start is not None and start <= last_massive_start:
                    issue(
                        "SOURCE_PAGE_ORDER_VIOLATION",
                        (
                            "Massive page sequence overlaps or moves backwards; no repair."
                            if request.provider == "massive"
                            else "Alpaca page sequence overlaps or moves backwards; no repair."
                        ),
                    )
                last_massive_start = start
            previous = start
            try:
                values = {}
                for key in ("open", "high", "low", "close", "volume"):
                    if not isinstance(row[key], str):
                        raise ValueError
                    values[key] = Decimal(row[key])
                    if not bounded_decimal(values[key]):
                        raise ValueError
            except (KeyError, TypeError, ValueError, InvalidOperation):
                issue(
                    "INVALID_NUMBER_OR_MISSING_FIELD",
                    "Finite source decimal strings within 64 digits/exponent +/-64 required.",
                )
                continue
            source_available = None
            availability, permitted = Availability.UNKNOWN, None
            try:
                if (
                    page.original_availability == "fixture_known"
                    and row.get("source_available_at") is not None
                ):
                    source_available = datetime.fromisoformat(row["source_available_at"])
                    availability, permitted = Availability.KNOWN, source_available
                elif request.assumed_delay_seconds is not None:
                    availability = Availability.ASSUMED
                    permitted = start + timedelta(minutes=1, seconds=request.assumed_delay_seconds)
                adjustment = Adjustment(row.get("adjustment", request.adjustment.value))
                complete = row.get("complete", start + timedelta(minutes=1) <= page.fetched_at)
                bar = HistoricalBar(
                    instrument_id=request.instrument.instrument_id,
                    venue=request.instrument.venue,
                    provider=request.provider,
                    start=start,
                    end=start + timedelta(minutes=1),
                    event_at=start + timedelta(minutes=1),
                    source_available_at=source_available,
                    availability=availability,
                    permitted_at=permitted,
                    ingested_at=page.fetched_at,
                    provider_timestamp=original,
                    provider_timestamp_timezone=timestamp_zone,
                    **values,
                    currency=request.instrument.trading_currency,
                    unit=request.instrument.quote_unit,
                    adjustment=adjustment,
                    complete=complete,
                    quality=() if complete else ("INCOMPLETE_BAR",),
                )
            except (ValidationError, ValueError, TypeError, AtlasError):
                issue(
                    "INVALID_BAR_CONTRACT",
                    "Invalid OHLC, volume, interval, availability or unit relationship.",
                )
                continue
            bars.append(bar)
            if not request.start <= bar.start < request.end:
                issue(
                    "OUTSIDE_REQUEST",
                    "Provider returned a bar outside the requested half-open interval.",
                )
            if not bar.complete:
                issue("INCOMPLETE_BAR", "Incomplete source bar cannot be made research-ready.")
            if adjustment != request.adjustment:
                issue(
                    "MIXED_ADJUSTMENT", "Adjusted and unadjusted source values must not be mixed."
                )
            if availability == Availability.UNKNOWN:
                issue(
                    "AVAILABILITY_UNKNOWN",
                    "Original market availability is unknown; strict visibility is denied.",
                    "WARNING",
                )
            elif availability == Availability.ASSUMED:
                issue(
                    "AVAILABILITY_ASSUMED",
                    "Bar-end delay is a declared assumption, not actual provider availability.",
                    "WARNING",
                )
    bars.sort(
        key=lambda b: b.start
    )  # Stable: duplicate rows retained, raw source order preserved separately.
    expected = calendar.expected_starts(request.start, request.end)
    actual = [bar.start for bar in bars]
    issue(
        "DUPLICATE_BARS",
        "Duplicate interval starts are retained and quarantined.",
        count=len(actual) - len(set(actual)),
    )
    issue(
        "MISSING_INTERVALS",
        "Expected regular-session intervals have no valid source bar; no interpolation.",
        count=len(expected - set(actual)),
    )
    issue(
        "OUT_OF_SESSION",
        "Bars fall outside reviewed regular cash-equity sessions.",
        count=len(set(actual) - expected),
    )
    if not bars:
        issue(
            "NO_VALID_BARS" if expected else "NO_SESSION_BARS_EXPECTED",
            "No valid observations in the requested window.",
            "FAIL" if expected else "WARNING",
        )
    for session in calendar.sessions():
        if session and max(request.start, session.open) < min(request.end, session.close):
            if request.start > session.open or request.end < session.close:
                issue(
                    "PARTIAL_SESSION_REQUEST",
                    "Coverage is only the explicitly requested portion of a session.",
                    "WARNING",
                )
    for prior, current in zip(bars, bars[1:], strict=False):
        if large_gap(current.open, prior.close):
            issue(
                "POTENTIAL_ACTION_OR_PRICE_GAP",
                "Over-30% discontinuity: possible gap or corporate action; no correction.",
                "WARNING",
            )
    for action in actions:
        if action.instrument_id != request.instrument.instrument_id:
            issue(
                "CORPORATE_ACTION_IDENTITY_MISMATCH",
                "Action metadata belongs to another canonical instrument.",
            )
    if action_coverage != "PROVIDER_DECLARED_COMPLETE":
        issue(
            "CORPORATE_ACTION_COVERAGE_INCOMPLETE",
            "Split/dividend/ticker/delisting history is incomplete or unknown.",
            "WARNING",
        )
    issue(
        "SURVIVORSHIP_UNASSESSED",
        "No point-in-time universe/delisted coverage has been established.",
        "WARNING",
    )
    findings = tuple(
        Finding(code=key, severity=severity[key], count=value, detail=descriptions[key])
        for key, value in sorted(counts.items())
    )
    status = (
        "FAIL" if any(f.severity == "FAIL" for f in findings) else "WARNING" if findings else "PASS"
    )
    return tuple(bars), QualityReport(
        status=status,
        findings=findings,
        expected_bars=len(expected),
        valid_bars=len(bars),
        raw_rows=raw_count,
    )


def human_report(report):
    lines = [
        f"HISTORICAL DATA QUALITY: {report.status}",
        f"Raw rows {report.raw_rows}; valid bars {report.valid_bars}; "
        f"expected intervals {report.expected_bars}.",
    ]
    lines += [
        f"{finding.severity} {finding.code} ({finding.count}): {finding.detail}"
        for finding in report.findings
    ]
    return "\n".join(lines)
