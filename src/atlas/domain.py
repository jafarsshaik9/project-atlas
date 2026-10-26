"""Validated, immutable offline contracts. Monetary inputs must be Decimal."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from atlas.config import AtlasError

ZERO = Decimal("0")
CENT = Decimal("0.01")


def number(value: Decimal, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AtlasError("A finite Decimal is required.")
    if value < 0 or (positive and value == 0):
        raise AtlasError(
            "A positive amount is required." if positive else "Negative amount refused."
        )
    return value


def utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise AtlasError("Timezone-aware UTC timestamps are required.")
    return value


def identity(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AtlasError("A nonempty identity is required.")


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    number(value)
    number(step, positive=True)
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


class Currency(StrEnum):
    GBP = "GBP"
    USD = "USD"


class QuoteUnit(StrEnum):
    GBP = "GBP"
    GBX = "GBX"
    USD = "USD"

    @property
    def currency(self) -> Currency:
        return Currency.USD if self == QuoteUnit.USD else Currency.GBP

    def major(self, price: Decimal) -> Decimal:
        number(price)
        return price / Decimal("100") if self == QuoteUnit.GBX else price


class Venue(StrEnum):
    LSE = "XLON"
    NYSE = "XNYS"
    NASDAQ = "XNAS"


class Adjustment(StrEnum):
    RAW = "raw"
    SPLIT = "split_adjusted"


class PriceBasis(StrEnum):
    MID = "mid"
    EXECUTABLE = "bid_ask"


class DecisionStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    RESEARCH_ONLY = "research_only"


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    venue: Venue
    quote_unit: QuoteUnit
    tick_size: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    provider_symbols: tuple[tuple[str, str], ...]
    broker_symbol: str | None = None
    asset_class: str = "equity"

    def __post_init__(self) -> None:
        identity(self.instrument_id)
        if not isinstance(self.venue, Venue) or not isinstance(self.quote_unit, QuoteUnit):
            raise AtlasError("Unsupported venue or quote unit.")
        for value in (self.tick_size, self.quantity_step, self.minimum_quantity):
            number(value, positive=True)
        if self.minimum_quantity % self.quantity_step:
            raise AtlasError("Minimum quantity must align to the quantity step.")
        if not self.provider_symbols or len(dict(self.provider_symbols)) != len(
            self.provider_symbols
        ):
            raise AtlasError("Unique provider symbol mappings are required.")
        for provider, symbol in self.provider_symbols:
            identity(provider)
            identity(symbol)
        if self.venue == Venue.LSE and self.quote_unit == QuoteUnit.USD:
            raise AtlasError("This research scope supports GBP/GBX LSE listings only.")
        if self.venue != Venue.LSE and self.quote_unit != QuoteUnit.USD:
            raise AtlasError("US listings require USD quotes in this research scope.")

    def valid_quantity(self, quantity: Decimal) -> bool:
        number(quantity)
        return quantity >= self.minimum_quantity and quantity % self.quantity_step == 0

    def valid_price(self, price: Decimal) -> bool:
        number(price, positive=True)
        return price % self.tick_size == 0


@dataclass(frozen=True)
class MarketBar:
    instrument_id: str
    source: str
    unit: QuoteUnit
    start: datetime
    end: datetime
    event_time: datetime
    available_time: datetime
    received_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    complete: bool = True
    adjustment: Adjustment = Adjustment.RAW
    quality: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identity(self.instrument_id)
        identity(self.source)
        for timestamp in (
            self.start,
            self.end,
            self.event_time,
            self.available_time,
            self.received_time,
        ):
            utc(timestamp)
        if not self.start < self.end or self.event_time != self.end:
            raise AtlasError("Bar event time must equal its interval end.")
        if self.available_time < self.event_time or self.received_time < self.available_time:
            raise AtlasError("Malformed bar availability timestamps.")
        for price in (self.open, self.high, self.low, self.close):
            number(price, positive=True)
        number(self.volume)
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise AtlasError("Impossible OHLC relationships.")
        if not isinstance(self.unit, QuoteUnit) or not isinstance(self.adjustment, Adjustment):
            raise AtlasError("Unsupported bar unit or adjustment.")
        if not isinstance(self.complete, bool) or not isinstance(self.quality, tuple):
            raise AtlasError("Invalid bar quality indicators.")

    @property
    def visible_time(self) -> datetime:
        return self.received_time


@dataclass(frozen=True)
class MarketQuote:
    instrument_id: str
    source: str
    unit: QuoteUnit
    event_time: datetime
    available_time: datetime
    received_time: datetime
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    complete: bool = True
    adjustment: Adjustment = Adjustment.RAW
    quality: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identity(self.instrument_id)
        identity(self.source)
        for timestamp in (self.event_time, self.available_time, self.received_time):
            utc(timestamp)
        if self.available_time < self.event_time or self.received_time < self.available_time:
            raise AtlasError("Malformed quote availability timestamps.")
        number(self.bid, positive=True)
        number(self.ask, positive=True)
        number(self.bid_size)
        number(self.ask_size)
        if self.bid > self.ask:
            raise AtlasError("Crossed quote refused.")
        if not isinstance(self.unit, QuoteUnit) or not isinstance(self.adjustment, Adjustment):
            raise AtlasError("Unsupported quote unit or adjustment.")
        if not isinstance(self.complete, bool) or not isinstance(self.quality, tuple):
            raise AtlasError("Invalid quote quality indicators.")

    @property
    def visible_time(self) -> datetime:
        return self.received_time


@dataclass(frozen=True)
class TradingSession:
    venue: Venue
    local_date: date
    open: datetime
    close: datetime
    timezone_name: str
    utc_offset_minutes: int
    half_day: bool = False

    def __post_init__(self) -> None:
        utc(self.open)
        utc(self.close)
        if self.close <= self.open or not isinstance(self.venue, Venue):
            raise AtlasError("Invalid trading session.")
        if (
            not isinstance(self.local_date, date)
            or isinstance(self.local_date, datetime)
            or not isinstance(self.utc_offset_minutes, int)
            or isinstance(self.utc_offset_minutes, bool)
            or not -720 <= self.utc_offset_minutes <= 840
        ):
            raise AtlasError("Invalid exchange-local session identity.")
        identity(self.timezone_name)
        if (self.open + timedelta(minutes=self.utc_offset_minutes)).date() != self.local_date:
            raise AtlasError("Exchange-local session date does not match its opening time.")
        if not isinstance(self.half_day, bool):
            raise AtlasError("Half-day flag must be boolean.")

    def contains(self, timestamp: datetime) -> bool:
        return self.open <= utc(timestamp) < self.close


@dataclass(frozen=True)
class FxRate:
    source_currency: Currency
    gbp_per_unit: Decimal
    event_time: datetime
    available_time: datetime
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_currency, Currency):
            raise AtlasError("Unsupported FX currency.")
        number(self.gbp_per_unit, positive=True)
        utc(self.event_time)
        utc(self.available_time)
        identity(self.source)
        if self.available_time < self.event_time:
            raise AtlasError("Malformed FX timestamps.")
        if self.source_currency == Currency.GBP and self.gbp_per_unit != Decimal("1"):
            raise AtlasError("GBP-to-GBP conversion must equal one.")


@dataclass(frozen=True)
class StrategySignal:
    intent_id: str
    instrument_id: str
    strategy_version: str
    decision_time: datetime
    expires_at: datetime
    time_exit: datetime
    entry: Decimal
    stop: Decimal
    target: Decimal
    side: str = "buy"

    def __post_init__(self) -> None:
        for value in (self.intent_id, self.instrument_id, self.strategy_version):
            identity(value)
        for timestamp in (self.decision_time, self.expires_at, self.time_exit):
            utc(timestamp)
        for price in (self.entry, self.stop, self.target):
            number(price, positive=True)
        if not self.stop < self.entry < self.target:
            raise AtlasError("A long hypothesis requires stop < entry < target.")
        if not self.decision_time < self.expires_at <= self.time_exit:
            raise AtlasError("Invalid signal lifetime.")
        if self.side not in ("buy", "sell"):
            raise AtlasError("Invalid signal side.")


@dataclass(frozen=True)
class TradeIntent:
    signal: StrategySignal
    quantity: Decimal

    def __post_init__(self) -> None:
        number(self.quantity, positive=True)


@dataclass(frozen=True)
class CostEstimate:
    schedule_id: str
    instrument_id: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_notional_gbp: Decimal
    exit_notional_gbp: Decimal
    entry_cost_gbp: Decimal
    exit_cost_gbp: Decimal
    total_cost_gbp: Decimal
    stressed_cost_gbp: Decimal
    gross_pnl_gbp: Decimal
    net_pnl_gbp: Decimal
    components: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        identity(self.schedule_id)
        identity(self.instrument_id)
        for value in (self.quantity, self.entry_price, self.exit_price):
            number(value, positive=True)
        for value in (
            self.entry_notional_gbp,
            self.exit_notional_gbp,
            self.entry_cost_gbp,
            self.exit_cost_gbp,
            self.total_cost_gbp,
            self.stressed_cost_gbp,
        ):
            number(value)
        for value in (self.gross_pnl_gbp, self.net_pnl_gbp):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise AtlasError("Invalid P&L.")
        if self.total_cost_gbp != self.entry_cost_gbp + self.exit_cost_gbp:
            raise AtlasError("Cost totals do not reconcile.")
        if self.net_pnl_gbp != self.gross_pnl_gbp - self.total_cost_gbp:
            raise AtlasError("Net P&L does not reconcile.")
        if self.gross_pnl_gbp != self.exit_notional_gbp - self.entry_notional_gbp:
            raise AtlasError("Gross P&L does not reconcile.")
        for name, amount in self.components:
            identity(name)
            number(amount)
        if sum((value for _, value in self.components), ZERO) != self.total_cost_gbp:
            raise AtlasError("Cost components do not reconcile.")
        if self.stressed_cost_gbp < self.total_cost_gbp:
            raise AtlasError("Stressed costs cannot be below base costs.")

    @property
    def entry_cash_gbp(self) -> Decimal:
        return self.entry_notional_gbp + self.entry_cost_gbp


@dataclass(frozen=True)
class RankedOpportunity:
    signal: StrategySignal
    hypothetical_net_gbp: Decimal
    evidence_id: str
    status: DecisionStatus = DecisionStatus.RESEARCH_ONLY

    def __post_init__(self) -> None:
        identity(self.evidence_id)
        if (
            not isinstance(self.hypothetical_net_gbp, Decimal)
            or not self.hypothetical_net_gbp.is_finite()
        ):
            raise AtlasError("Invalid hypothetical P&L.")
        if self.status != DecisionStatus.RESEARCH_ONLY:
            raise AtlasError("Synthetic evidence cannot establish an investable ranking.")


@dataclass(frozen=True)
class Position:
    instrument: Instrument
    quantity: Decimal
    entry_notional_gbp: Decimal
    entry_cost_gbp: Decimal
    mark_value_gbp: Decimal
    mark_time: datetime
    opened_at: datetime
    planned_risk_gbp: Decimal
    stressed_risk_gbp: Decimal
    signal: StrategySignal

    def __post_init__(self) -> None:
        number(self.quantity, positive=True)
        for value in (
            self.entry_notional_gbp,
            self.entry_cost_gbp,
            self.mark_value_gbp,
            self.planned_risk_gbp,
            self.stressed_risk_gbp,
        ):
            number(value)
        utc(self.mark_time)
        utc(self.opened_at)

    @property
    def cost_basis_gbp(self) -> Decimal:
        return self.entry_notional_gbp + self.entry_cost_gbp

    @property
    def average_cost_gbp(self) -> Decimal:
        return self.cost_basis_gbp / self.quantity


@dataclass(frozen=True)
class PortfolioSnapshot:
    version: int
    timestamp: datetime
    initial_capital_gbp: Decimal
    cash_by_currency: tuple[tuple[Currency, Decimal], ...]
    reserved_cash_gbp: Decimal
    positions: tuple[Position, ...]
    realized_pnl_gbp: Decimal
    unrealized_pnl_gbp: Decimal
    daily_realized_pnl_gbp: Decimal
    equity_gbp: Decimal
    concurrent_planned_risk_gbp: Decimal
    pending_intents: tuple[str, ...]
    consistent: bool
    daily_start_equity_gbp: Decimal

    def __post_init__(self) -> None:
        utc(self.timestamp)
        number(self.initial_capital_gbp, positive=True)
        number(self.reserved_cash_gbp)
        number(self.concurrent_planned_risk_gbp)
        if self.version < 0 or len(dict(self.cash_by_currency)) != len(self.cash_by_currency):
            raise AtlasError("Invalid portfolio snapshot.")
        for currency, cash in self.cash_by_currency:
            if not isinstance(currency, Currency):
                raise AtlasError("Unsupported cash currency.")
            number(cash)
        if Currency.GBP not in dict(self.cash_by_currency):
            raise AtlasError("GBP base cash is required.")
        for value in (
            self.realized_pnl_gbp,
            self.unrealized_pnl_gbp,
            self.daily_realized_pnl_gbp,
            self.equity_gbp,
            self.daily_start_equity_gbp,
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise AtlasError("Invalid portfolio valuation.")

    @property
    def available_cash_gbp(self) -> Decimal:
        return dict(self.cash_by_currency)[Currency.GBP] - self.reserved_cash_gbp

    @property
    def daily_pnl_gbp(self) -> Decimal:
        return self.equity_gbp - self.daily_start_equity_gbp

    @property
    def daily_unrealized_pnl_gbp(self) -> Decimal:
        return self.daily_pnl_gbp - self.daily_realized_pnl_gbp


@dataclass(frozen=True)
class RiskDecision:
    status: DecisionStatus
    reasons: tuple[str, ...]
    intent_id: str
    proposed_quantity: Decimal
    planned_risk_gbp: Decimal
    stressed_risk_gbp: Decimal
    capital_usage_gbp: Decimal
    policy_version: str
    snapshot_version: int
    timestamp: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.status, DecisionStatus):
            raise AtlasError("Invalid risk status.")
        identity(self.intent_id)
        identity(self.policy_version)
        utc(self.timestamp)
        for value in (
            self.proposed_quantity,
            self.planned_risk_gbp,
            self.stressed_risk_gbp,
            self.capital_usage_gbp,
        ):
            number(value)
        if self.status == DecisionStatus.APPROVED and self.reasons:
            raise AtlasError("An approved research decision cannot contain rejection reasons.")


@dataclass(frozen=True)
class AuditEvent:
    timestamp: datetime
    component: str
    event: str
    reference: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        utc(self.timestamp)
        for value in (self.component, self.event, self.reference):
            identity(value)
