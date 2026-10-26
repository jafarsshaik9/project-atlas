"""Synthetic, effective-dated costs. No broker fee or tax rate is assumed."""

from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from decimal import ROUND_UP, Decimal

from atlas.config import AtlasError
from atlas.domain import (
    CENT,
    CostEstimate,
    Currency,
    FxRate,
    Instrument,
    PriceBasis,
    Venue,
    number,
    utc,
)

BPS = Decimal("10000")


def money(value: Decimal) -> Decimal:
    """Round expenses up to pennies; conservative and deterministic."""
    return value.quantize(CENT, rounding=ROUND_UP)


@dataclass(frozen=True)
class FeeSchedule:
    schedule_id: str
    effective_from: datetime
    effective_until: datetime
    venues: tuple[Venue, ...]
    spread_bps: Decimal | None
    slippage_bps: Decimal | None
    commission_fixed_gbp: Decimal | None
    commission_bps: Decimal | None
    fx_bps: Decimal | None
    entry_tax_bps: Decimal | None
    exit_tax_bps: Decimal | None
    other_fixed_gbp: Decimal | None
    stress_multiplier: Decimal = Decimal("2")
    synthetic: bool = True

    def __post_init__(self) -> None:
        utc(self.effective_from)
        utc(self.effective_until)
        if not self.schedule_id or self.effective_from >= self.effective_until or not self.venues:
            raise AtlasError("Invalid effective-dated cost schedule.")
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name.endswith(("_bps", "_gbp")) and value is not None:
                number(value)
        if number(self.stress_multiplier, positive=True) < 1:
            raise AtlasError("Cost stress multiplier must be at least one.")
        if self.synthetic is not True:
            raise AtlasError("Only explicitly synthetic schedules are supported in Phase 2B.")


def validate_fx(rate: FxRate, instrument: Instrument, now: datetime) -> None:
    utc(now)
    if rate.source_currency != instrument.quote_unit.currency:
        raise AtlasError("FX_CURRENCY_MISMATCH")
    if rate.available_time > now or now - rate.event_time > timedelta(hours=24):
        raise AtlasError("FX_UNAVAILABLE_OR_STALE")


class CostEngine:
    def __init__(self, schedules: tuple[FeeSchedule, ...]) -> None:
        self.schedules = schedules

    def schedule(self, instrument: Instrument, now: datetime) -> FeeSchedule:
        utc(now)
        matches = [
            fee
            for fee in self.schedules
            if instrument.venue in fee.venues and fee.effective_from <= now < fee.effective_until
        ]
        if len(matches) != 1:
            raise AtlasError("UNKNOWN_OR_AMBIGUOUS_COST_SCHEDULE")
        fee = matches[0]
        if any(
            getattr(fee, field.name) is None
            for field in fields(fee)
            if field.name.endswith(("_bps", "_gbp"))
        ):
            raise AtlasError("UNKNOWN_TRANSACTION_COSTS")
        return fee

    def estimate(
        self,
        instrument: Instrument,
        quantity: Decimal,
        entry: Decimal,
        exit: Decimal,
        rate: FxRate,
        now: datetime,
        *,
        entry_basis: PriceBasis = PriceBasis.MID,
        exit_basis: PriceBasis = PriceBasis.MID,
    ) -> CostEstimate:
        if not instrument.valid_quantity(quantity):
            raise AtlasError("INVALID_QUANTITY")
        if not instrument.valid_price(entry) or not instrument.valid_price(exit):
            raise AtlasError("INVALID_TICK")
        if not isinstance(entry_basis, PriceBasis) or not isinstance(exit_basis, PriceBasis):
            raise AtlasError("UNKNOWN_PRICE_BASIS")
        validate_fx(rate, instrument, now)
        fee = self.schedule(instrument, now)
        gross = [
            instrument.quote_unit.major(price) * quantity * rate.gbp_per_unit
            for price in (entry, exit)
        ]
        components = []
        totals = []
        for label, value, basis, tax in zip(
            ("entry", "exit"),
            gross,
            (entry_basis, exit_basis),
            (fee.entry_tax_bps, fee.exit_tax_bps),
            strict=True,
        ):
            amounts = {
                "spread": ZERO
                if basis == PriceBasis.EXECUTABLE
                else value * fee.spread_bps / BPS / 2,
                "slippage": value * fee.slippage_bps / BPS,
                "commission": fee.commission_fixed_gbp + value * fee.commission_bps / BPS,
                "fx": value * fee.fx_bps / BPS if rate.source_currency != Currency.GBP else ZERO,
                "tax": value * tax / BPS,
                "other": fee.other_fixed_gbp,
            }
            rounded = {key: money(amount) for key, amount in amounts.items()}
            totals.append(sum(rounded.values(), ZERO))
            components.extend((f"{label}_{key}", amount) for key, amount in rounded.items())
        total = sum(totals, ZERO)
        pnl = gross[1] - gross[0]
        return CostEstimate(
            fee.schedule_id,
            instrument.instrument_id,
            quantity,
            entry,
            exit,
            gross[0],
            gross[1],
            totals[0],
            totals[1],
            total,
            money(total * fee.stress_multiplier),
            pnl,
            pnl - total,
            tuple(components),
        )


ZERO = Decimal("0")
