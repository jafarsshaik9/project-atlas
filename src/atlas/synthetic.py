"""Original artificial market fixtures. No network, external trading code or hidden randomness."""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atlas.costs import CostEngine, FeeSchedule
from atlas.domain import (
    FxRate,
    Instrument,
    MarketBar,
    MarketQuote,
    QuoteUnit,
    TradingSession,
    Venue,
)
from atlas.sessions import FixtureCalendar

D = Decimal
SCENARIOS = (
    "rising",
    "falling",
    "sideways",
    "volatile",
    "low_liquidity",
    "orb",
    "false_breakout",
    "missing",
    "duplicate",
    "incomplete",
    "stale_quote",
    "gap",
    "ambiguous",
    "time_exit",
    "no_fill",
    "partial_fill",
)
LABEL = "SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE"


def fixture_calendar() -> FixtureCalendar:
    """Explicit fictional test schedule; never a production calendar."""
    entries = []
    dates = (
        ("2026-03-06", 0, -300),
        ("2026-03-09", 0, -240),
        ("2026-03-27", 0, -240),
        ("2026-03-30", 60, -240),
        ("2026-10-23", 60, -240),
        ("2026-10-26", 0, -240),
        ("2026-10-30", 0, -240),
        ("2026-11-02", 0, -300),
        ("2026-12-24", 0, -300),
    )
    for day_text, london_offset, ny_offset in dates:
        day = date.fromisoformat(day_text)
        half = day_text == "2026-12-24"
        for venue in Venue:
            london = venue == Venue.LSE
            offset = london_offset if london else ny_offset
            start_minutes = 480 if london else 570
            end_minutes = (750 if london else 780) if half else (990 if london else 960)
            midnight = datetime.combine(day, datetime.min.time(), UTC)
            session = TradingSession(
                venue,
                day,
                midnight + timedelta(minutes=start_minutes - offset),
                midnight + timedelta(minutes=end_minutes - offset),
                "Europe/London" if london else "America/New_York",
                offset,
                half,
            )
            entries.append((venue, day, session))
    for venue in Venue:
        entries.extend(
            (venue, date.fromisoformat(day), None) for day in ("2026-12-25", "2026-10-25")
        )
    return FixtureCalendar(tuple(entries))


def instrument(venue: Venue = Venue.LSE) -> Instrument:
    return Instrument(
        f"SYNTH-{venue.value}-EQUITY-001",
        venue,
        QuoteUnit.GBX if venue == Venue.LSE else QuoteUnit.USD,
        D("1") if venue == Venue.LSE else D("0.01"),
        D("1"),
        D("1"),
        (("synthetic", "ARTIFICIAL.L" if venue == Venue.LSE else "ARTIFICIAL"),),
    )


def synthetic_costs() -> CostEngine:
    return CostEngine(
        (
            FeeSchedule(
                "SYNTHETIC-FEES-v1-NOT-BROKER-RATES",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2027, 1, 1, tzinfo=UTC),
                tuple(Venue),
                D("10"),
                D("2"),
                D("0.05"),
                D("0"),
                D("5"),
                D("0"),
                D("0"),
                D("0"),
                D("2"),
            ),
        )
    )


@dataclass(frozen=True)
class SyntheticDataset:
    name: str
    instrument: Instrument
    session: TradingSession
    bars: tuple[MarketBar, ...]
    quotes: tuple[MarketQuote, ...]
    fx: FxRate
    provenance: tuple[tuple[str, str], ...]

    @property
    def checksum(self) -> str:
        payload = repr((self.name, self.instrument, self.session, self.bars, self.quotes, self.fx))
        return hashlib.sha256(payload.encode()).hexdigest()


def generate(
    scenario: str = "orb",
    venue: Venue = Venue.LSE,
    day: date = date(2026, 10, 26),
) -> SyntheticDataset:
    if scenario not in SCENARIOS:
        from atlas.config import AtlasError

        raise AtlasError("Unknown synthetic scenario.")
    inst = instrument(venue)
    session = fixture_calendar().session(venue, day)
    if session is None:
        from atlas.config import AtlasError

        raise AtlasError("Synthetic fixture date is closed.")
    scale = D("1") if venue == Venue.LSE else D("0.01")
    base = D("250") if venue == Venue.LSE else D("100")
    # Native units: the UK baseline is 250 GBX = GBP 2.50.
    shape = [
        ("0", "1", "-1", "0"),
        ("0", "2", "-0", "1"),
        ("1", "2", "0", "1"),
        ("1", "4", "1", "3"),
        ("3", "5", "2", "4"),
        ("4", "10", "3", "8"),
    ]
    if venue != Venue.LSE:
        scale = D("1")
    if scenario in ("sideways", "falling", "low_liquidity"):
        shape = [("0", "1", "-1", "0")] * 12
        if scenario == "falling":
            shape = [(str(-i), str(1 - i), str(-2 - i), str(-1 - i)) for i in range(12)]
    else:
        shape += [("8", "9", "7", "8")] * 6
    if scenario == "false_breakout":
        shape[5] = ("3", "4", "-2", "-1")
    if scenario == "gap":
        shape[5] = ("-15", "-10", "-20", "-18")
    if scenario == "ambiguous":
        shape[5] = ("4", "12", "-3", "1")
    if scenario == "time_exit":
        shape[4:] = [("3", "4", "2", "3")] * 8
    if scenario == "volatile":
        shape = [("0", "30", "-20", "0")] * 3 + [("0", "50", "-10", "40")] * 9
    bars = []
    for index, row in enumerate(shape):
        start = session.open + timedelta(minutes=5 * index)
        end = start + timedelta(minutes=5)
        bars.append(
            MarketBar(
                inst.instrument_id,
                "original-synthetic-generator",
                inst.quote_unit,
                start,
                end,
                end,
                end,
                end,
                *(base + D(value) * scale for value in row),
                D("2") if scenario == "low_liquidity" else D("10000"),
            )
        )
    if scenario == "missing":
        del bars[1]
    if scenario == "duplicate":
        bars.insert(1, bars[0])
    if scenario == "incomplete":
        bars[1] = replace(bars[1], complete=False)
    quote_time = session.open + timedelta(minutes=20, seconds=1)
    spread = D("1") if venue == Venue.LSE else D("0.02")
    mid = base + D("3") * scale
    quote = MarketQuote(
        inst.instrument_id,
        "original-synthetic-generator",
        inst.quote_unit,
        quote_time,
        quote_time,
        quote_time,
        mid - spread,
        mid + spread,
        D("10000"),
        D("10000"),
    )
    if scenario == "stale_quote":
        quote = replace(quote, event_time=session.open)
    if scenario == "no_fill":
        quote = replace(quote, ask_size=D("0"))
    if scenario == "partial_fill":
        quote = replace(quote, ask_size=D("1"))
    final_time = session.close - timedelta(minutes=5)
    final_mid = base + D("3" if scenario == "time_exit" else "8") * scale
    final_quote = replace(
        quote,
        event_time=final_time,
        available_time=final_time,
        received_time=final_time,
        bid=final_mid - spread,
        ask=final_mid + spread,
        ask_size=D("10000"),
    )
    fx = FxRate(
        inst.quote_unit.currency,
        D("1") if venue == Venue.LSE else D("0.8"),
        session.open,
        session.open,
        "SYNTHETIC-FX-NOT-A-MARKET-RATE",
    )
    return SyntheticDataset(
        scenario,
        inst,
        session,
        tuple(bars),
        (quote, final_quote),
        fx,
        (
            ("label", LABEL),
            ("generator", "atlas.synthetic.v1"),
            (
                "parameters",
                json.dumps(
                    {
                        "scenario": scenario,
                        "venue": venue.value,
                        "day": day.isoformat(),
                        "seed": 0,
                        "randomness": "none",
                    },
                    sort_keys=True,
                ),
            ),
        ),
    )


def write_manifest(path: Path) -> None:
    """Explicit optional fixture export; no implicit file writes during replay."""
    datasets = [generate(name, venue) for venue in (Venue.LSE, Venue.NASDAQ) for name in SCENARIOS]
    path.write_text(
        json.dumps(
            [
                {
                    "name": data.name,
                    "instrument": data.instrument.instrument_id,
                    "checksum": data.checksum,
                    "provenance": dict(data.provenance),
                }
                for data in datasets
            ],
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def write_dataset_bundle(path: Path) -> None:
    """Export original OHLCV/quote fixtures, with Decimal strings and ISO UTC times."""

    def encode(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError("Unsupported synthetic fixture serialization type")

    datasets = [generate(name, venue) for venue in (Venue.LSE, Venue.NASDAQ) for name in SCENARIOS]
    path.write_text(
        json.dumps(
            {
                "label": LABEL,
                "generator": "atlas.synthetic.v1",
                "datasets": [dict(asdict(data), checksum=data.checksum) for data in datasets],
            },
            default=encode,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
