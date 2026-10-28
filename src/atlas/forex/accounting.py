"""Unleveraged synthetic Forex contracts; no broker or stock accounting dependency."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from atlas.config import AtlasError
from atlas.domain import floor_step, number, utc
from atlas.forex.models import FxInstrument
from atlas.forex.sessions import crosses_rollover
from atlas.research import ResearchSettings

ZERO = Decimal(0)


@dataclass(frozen=True)
class Conversion:
    currency: str
    gbp_per_unit: Decimal
    event_time: datetime
    available_time: datetime
    basis: str  # explicit ASSUMED or VERIFIED input evidence, not a verification claim

    def __post_init__(self):
        number(self.gbp_per_unit, positive=True)
        utc(self.event_time)
        utc(self.available_time)
        if (
            not re.fullmatch(r"[A-Z]{3}", self.currency)
            or self.basis not in ("ASSUMED", "VERIFIED")
            or self.available_time < self.event_time
        ):
            raise AtlasError("FOREX_CONVERSION_CONTRACT_INVALID")

    def value(self, currency, now):
        number(self.gbp_per_unit, positive=True)
        utc(now)
        utc(self.event_time)
        utc(self.available_time)
        if (
            currency != self.currency
            or self.basis not in ("ASSUMED", "VERIFIED")
            or not self.event_time <= self.available_time <= now
            or now - self.event_time > timedelta(minutes=5)
        ):
            raise AtlasError("FOREX_CONVERSION_UNAVAILABLE_OR_STALE")
        if currency == "GBP" and self.gbp_per_unit != 1:
            raise AtlasError("GBP conversion must be identity.")
        return self.gbp_per_unit


@dataclass(frozen=True)
class FxQuote:
    instrument: str
    bid: Decimal
    ask: Decimal
    event_time: datetime
    available_time: datetime

    def __post_init__(self):
        number(self.bid, positive=True)
        number(self.ask, positive=True)
        utc(self.event_time)
        utc(self.available_time)
        if self.bid > self.ask or self.event_time > self.available_time:
            raise AtlasError("FOREX_QUOTE_CONTRACT_INVALID")

    def validate(self, instrument, now):
        number(self.bid, positive=True)
        number(self.ask, positive=True)
        utc(now)
        utc(self.event_time)
        utc(self.available_time)
        if (
            self.instrument != instrument.name
            or self.bid > self.ask
            or not self.event_time <= self.available_time <= now
            or now - self.event_time > timedelta(seconds=60)
        ):
            raise AtlasError("FOREX_QUOTE_INVALID_OR_STALE")

    @property
    def spread(self):
        return self.ask - self.bid


@dataclass(frozen=True)
class FxCosts:
    # Both must be supplied explicitly; no inherited equity commission assumption.
    slippage_quote_per_unit: Decimal
    commission_gbp_per_unit_per_side: Decimal

    def __post_init__(self):
        number(self.slippage_quote_per_unit)
        number(self.commission_gbp_per_unit_per_side)


@dataclass(frozen=True)
class Position:
    identity: str
    instrument: FxInstrument
    side: str
    units: Decimal
    entry: Decimal
    opened_at: datetime
    stop: Decimal
    target: Decimal
    collateral_gbp: Decimal
    planned_risk_gbp: Decimal
    entry_fee_gbp: Decimal


def require_currency(instrument):
    if instrument.kind != "CURRENCY":
        raise AtlasError("Non-Forex contract accounting is unsupported.")


def pip_value(instrument, units, conversion, now):
    require_currency(instrument)
    number(units, positive=True)
    return units * instrument.pip * conversion.value(instrument.quote, now)


def lot_equivalent(units, *, units_per_lot):
    # An explicit convention, never inferred for spread bets, metals or CFDs.
    number(units, positive=True)
    number(units_per_lot, positive=True)
    return units / units_per_lot


def broker_margin_estimate(instrument, units, price, conversion, now):
    require_currency(instrument)
    number(units, positive=True)
    number(price, positive=True)
    if instrument.margin_rate is None:
        raise AtlasError("Broker margin rate unavailable.")
    return units * price * conversion.value(instrument.quote, now) * instrument.margin_rate


class ForexPortfolio:
    """Cash-collateralized P/L simulation, not a CFD/spread-bet product replica.

    Full quote-notional collateral is reserved for both directions; shorts do
    not create spendable proceeds. Leverage, live balances and order routing
    cannot be enabled by this class. No persistent execution engine is claimed.
    """

    def __init__(self, costs: FxCosts):
        self.settings = ResearchSettings()
        self.costs = costs
        self.cash = self.settings.starting_capital_gbp
        self.positions = {}
        self.used_ids = set()
        self.realised = ZERO
        self.fees = ZERO
        self.events = []
        self.last_time = None
        self.daily_realised = {}

    @property
    def available_cash(self):
        return self.cash - sum((p.collateral_gbp for p in self.positions.values()), ZERO)

    def _time(self, now):
        utc(now)
        if self.last_time is not None and now < self.last_time:
            raise AtlasError("FOREX_REPLAY_TIME_REGRESSION")

    def size(self, instrument, quote, conversion, stop, now):
        if not isinstance(quote, FxQuote) or not isinstance(conversion, Conversion):
            raise AtlasError("FOREX_EXECUTABLE_QUOTE_AND_CONVERSION_REQUIRED")
        require_currency(instrument)
        quote.validate(instrument, now)
        rate = conversion.value(instrument.quote, now)
        number(stop, positive=True)
        distance = max(abs(quote.ask - stop), abs(quote.bid - stop))
        risk_per = (
            distance + 2 * self.costs.slippage_quote_per_unit
        ) * rate + 2 * self.costs.commission_gbp_per_unit_per_side
        if risk_per <= 0:
            return ZERO
        cash_per = (
            quote.ask + self.costs.slippage_quote_per_unit
        ) * rate + self.costs.commission_gbp_per_unit_per_side
        qty = floor_step(
            min(
                self.available_cash / cash_per, self.settings.planned_risk_per_trade_gbp / risk_per
            ),
            instrument.unit_step,
        )
        if instrument.maximum_units is not None:
            qty = min(qty, floor_step(instrument.maximum_units, instrument.unit_step))
        return qty if qty >= instrument.minimum_units else ZERO

    def open(self, identity, instrument, side, units, quote, conversion, stop, target, now):
        self._time(now)
        if not isinstance(quote, FxQuote) or not isinstance(conversion, Conversion):
            raise AtlasError("FOREX_EXECUTABLE_QUOTE_AND_CONVERSION_REQUIRED")
        if self.positions:
            raise AtlasError("FOREX_SINGLE_POSITION_FOUNDATION")
        require_currency(instrument)
        quote.validate(instrument, now)
        rate = conversion.value(instrument.quote, now)
        for value in (units, stop, target):
            number(value, positive=True)
        if (
            side not in ("long", "short")
            or identity in self.used_ids
            or not identity
            or instrument.name in self.positions
            or units < instrument.minimum_units
            or units % instrument.unit_step
            or (instrument.maximum_units is not None and units > instrument.maximum_units)
        ):
            raise AtlasError("FOREX_POSITION_CONTRACT_REJECTED")
        price = (
            quote.ask + self.costs.slippage_quote_per_unit
            if side == "long"
            else quote.bid - self.costs.slippage_quote_per_unit
        )
        if price <= 0 or not (stop < price < target if side == "long" else target < price < stop):
            raise AtlasError("FOREX_STOP_TARGET_INVALID")
        fee = units * self.costs.commission_gbp_per_unit_per_side
        collateral = units * (quote.ask + self.costs.slippage_quote_per_unit) * rate
        risk = units * (abs(price - stop) + self.costs.slippage_quote_per_unit) * rate + 2 * fee
        total_risk = sum((p.planned_risk_gbp for p in self.positions.values()), ZERO) + risk
        if (
            risk > self.settings.planned_risk_per_trade_gbp
            or total_risk > self.settings.max_concurrent_planned_risk_gbp
            or -self.daily_realised.get(now.date(), ZERO) >= self.settings.daily_loss_threshold_gbp
            or collateral + fee > self.available_cash
        ):
            raise AtlasError("FOREX_UNLEVERAGED_CASH_OR_RISK_LIMIT")
        position = Position(
            identity, instrument, side, units, price, now, stop, target, collateral, risk, fee
        )
        self.positions[instrument.name] = position
        self.used_ids.add(identity)
        self.cash -= fee
        self.fees += fee
        self.last_time = now
        self.events.append((identity, "open", units, price, fee, now))
        return position

    def unrealised(self, instrument_name, quote, conversion, now):
        self._time(now)
        p = self.positions[instrument_name]
        quote.validate(p.instrument, now)
        rate = conversion.value(p.instrument.quote, now)
        exit_price = quote.bid if p.side == "long" else quote.ask
        return (exit_price - p.entry) * p.units * (1 if p.side == "long" else -1) * rate

    def close(self, instrument_name, quote, conversion, now, *, financing_gbp=None):
        self._time(now)
        p = self.positions[instrument_name]
        quote.validate(p.instrument, now)
        rate = conversion.value(p.instrument.quote, now)
        if crosses_rollover(p.opened_at, now) and financing_gbp is None:
            raise AtlasError("FOREX_OVERNIGHT_FINANCING_UNSUPPORTED_WITHOUT_EXPLICIT_CHARGE")
        financing = ZERO if financing_gbp is None else financing_gbp
        number(financing)  # This conservative foundation accepts charges, not invented credits.
        price = (
            quote.bid - self.costs.slippage_quote_per_unit
            if p.side == "long"
            else quote.ask + self.costs.slippage_quote_per_unit
        )
        number(price, positive=True)
        gross = (price - p.entry) * p.units * (1 if p.side == "long" else -1) * rate
        fee = p.units * self.costs.commission_gbp_per_unit_per_side
        net = gross - p.entry_fee_gbp - fee - financing
        self.cash += gross - fee - financing
        self.realised += net
        self.daily_realised[now.date()] = self.daily_realised.get(now.date(), ZERO) + net
        self.fees += fee + financing
        del self.positions[instrument_name]
        self.last_time = now
        self.events.append((p.identity, "close", p.units, price, fee + financing, now))
        if not self.positions and self.cash != self.settings.starting_capital_gbp + self.realised:
            raise AtlasError("FOREX_RECONCILIATION_FAILED")
        return net
