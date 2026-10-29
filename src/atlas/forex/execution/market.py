"""Market-data gates, frozen provenance adapter, exact costs and GBP conversion."""

from collections.abc import Iterable, Iterator
from decimal import Decimal

from atlas.config import AtlasError
from atlas.forex.execution.calendar import interval_reason
from atlas.forex.execution.contracts import (
    MINUTE,
    Costs,
    Frame,
    Observation,
    Overlay,
    Settings,
)
from atlas.forex.history import ForexStore, windows
from atlas.forex.models import CandlePlan, FxCandle, FxInstrument
from atlas.forex.quality import evaluate
from atlas.market_data.contracts import digest

ZERO = Decimal(0)


def reason(observation: Observation | None, overlay: Overlay) -> str | None:
    if observation is None:
        return "FX_MISSING_CANDLE"
    b = observation.candle
    if not b.complete or any(x is None for x in (b.bid, b.ask, b.mid)):
        return "FX_INCOMPLETE_OR_MISSING_BA"
    if any(getattr(b.bid, k) > getattr(b.ask, k) for k in ("o", "c")):
        return "FX_CROSSED_ENDPOINTS"
    if issue := interval_reason(b.start, b.start + MINUTE):
        return issue
    for annotation in overlay.annotations:
        if (
            annotation.instrument == observation.instrument
            and annotation.start < b.start + MINUTE
            and annotation.end > b.start
        ):
            return annotation.reason
    return None


def spread(bar: FxCandle, instrument: FxInstrument) -> Decimal:
    return (bar.ask.c - bar.bid.c) / instrument.pip


def entry_reason(o: Observation, instrument: FxInstrument, settings: Settings) -> str | None:
    indicative = spread(o.candle, instrument)
    if (
        max(indicative, settings.costs.spread_floor_pips) * settings.costs.spread_multiplier
        + settings.costs.additional_spread_pips
        > settings.costs.max_entry_spread_pips
    ):
        return "FX_HIGH_ENTRY_SPREAD"
    if (o.candle.mid.h - o.candle.mid.l) / instrument.pip > settings.max_entry_range_pips:
        return "FX_EXTREME_RANGE_REVIEW"
    return None


def adverse_price(
    raw: Decimal, side: str, entering: bool, bar: FxCandle, instrument: FxInstrument, costs: Costs
) -> tuple[Decimal, Decimal]:
    adjustment = quote_stress(bar, instrument, costs) + costs.slippage_pips * instrument.pip
    buying = (side == "long") == entering
    price = raw + adjustment if buying else raw - adjustment
    if price <= 0:
        raise AtlasError("FX_NONPOSITIVE_STRESSED_PRICE")
    return price, adjustment


def quote_stress(bar: FxCandle, instrument: FxInstrument, costs: Costs) -> Decimal:
    observed = spread(bar, instrument)
    extra = (
        max(observed, costs.spread_floor_pips) * costs.spread_multiplier
        - observed
        + costs.additional_spread_pips
    ) / 2
    return extra * instrument.pip


def conversion(currency: str, amount: Decimal, available: dict[str, Observation], *, at) -> Decimal:
    """Income buys GBP at GBP_USD ask; obligations sell GBP at bid. No cached rates."""
    if currency == "GBP":
        return Decimal(1)
    gbp = available.get("GBP_USD")
    if gbp is None:
        raise AtlasError("FX_GBP_CONVERSION_UNAVAILABLE")
    required = ("GBP_USD", "USD_JPY") if currency == "JPY" else ("GBP_USD",)
    if any(
        name not in available
        or available[name].candle.start + MINUTE != at
        or reason(available[name], Overlay())
        for name in required
    ):
        raise AtlasError("FX_CONVERSION_NOT_CURRENT_COMPLETE_CLOSE")
    field = "ask" if amount >= 0 else "bid"
    denominator = getattr(gbp.candle, field).c
    if currency == "JPY":
        jpy = available.get("USD_JPY")
        if jpy is None:
            raise AtlasError("FX_JPY_CONVERSION_UNAVAILABLE")
        denominator *= getattr(jpy.candle, field).c
    elif currency != "USD":
        raise AtlasError("FX_UNSUPPORTED_CONVERSION")
    return Decimal(1) / denominator


def frames_from_partitions(bundles: Iterable[tuple], start, end) -> Iterator[Frame]:
    """One UTC research day, <=4,320 candles. Feed only verified read-only partition bundles.

    This adapter validates proofs again and merges all three instruments by minute.
    It neither opens a store nor constructs a provider. It is never called implicitly.
    """
    from atlas.domain import utc

    utc(start)
    utc(end)
    if not start < end or end - start > 24 * 60 * MINUTE:
        raise AtlasError("FX_ADAPTER_REQUIRES_ONE_DAY")
    by_time = {}
    seen = set()
    seen_instruments = set()
    count = 0
    for identity, manifest, quality, bars in bundles:
        p = CandlePlan.model_validate(manifest["plan"])
        if p.instrument.name in seen_instruments or len(seen_instruments) == 3:
            raise AtlasError("FX_ADAPTER_OVERLAPPING_OR_EXCESS_PARTITIONS")
        seen_instruments.add(p.instrument.name)
        if not start <= p.start < p.end <= end or p.components != "MBA":
            raise AtlasError("FX_ADAPTER_SCOPE_MISMATCH")
        if identity in seen or digest(manifest) != identity:
            raise AtlasError("FX_ADAPTER_DUPLICATE_OR_MANIFEST_MISMATCH")
        seen.add(identity)
        if len(bars) > 1440 or any(
            not p.start <= b.start < p.end or (i and b.start <= bars[i - 1].start)
            for i, b in enumerate(bars)
        ):
            raise AtlasError("FX_ADAPTER_BAR_INTEGRITY")
        hashes = [
            digest([b.model_dump(mode="json") for b in bars if a <= b.start < z])
            for a, z in windows(p)
        ]
        if (
            ForexStore._manifest(p, hashes, bars) != manifest
            or evaluate(p, bars) != quality
            or quality["critical"]
        ):
            raise AtlasError("FX_ADAPTER_PROOF_MISMATCH")
        for b in bars:
            count += 1
            if count > 4320:
                raise AtlasError("FX_ADAPTER_MEMORY_BOUND")
            bucket = by_time.setdefault(b.start, {})
            if p.instrument.name in bucket:
                raise AtlasError("FX_ADAPTER_OVERLAPPING_PARTITIONS")
            bucket[p.instrument.name] = Observation(
                instrument=p.instrument.name,
                candle=b,
                dataset_id=identity,
                quality_id=quality["checksum"],
            )
    for stamp, values in sorted(by_time.items()):
        yield Frame(start=stamp, observations=tuple(values[k] for k in sorted(values)))
