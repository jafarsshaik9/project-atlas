"""Independent deterministic research gate. No approval input or broker dispatch."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from atlas.config import AtlasError
from atlas.costs import CostEngine
from atlas.domain import (
    ZERO,
    Adjustment,
    DecisionStatus,
    FxRate,
    Instrument,
    MarketBar,
    MarketQuote,
    PortfolioSnapshot,
    PriceBasis,
    RiskDecision,
    StrategySignal,
    TradeIntent,
    TradingSession,
    floor_step,
    number,
    utc,
)
from atlas.portfolio import PortfolioLedger
from atlas.sessions import SessionCalendar, bar_errors


@dataclass(frozen=True)
class RiskPolicy:
    version: str = "synthetic-risk-v1"
    max_position_pct: Decimal = Decimal("25")
    gap_stress_pct: Decimal = Decimal("2")
    max_stressed_risk_pct: Decimal = Decimal("2")
    quote_max_age: timedelta = timedelta(seconds=60)
    leverage_enabled: bool = False
    short_selling_enabled: bool = False
    broker_execution_enabled: bool = False

    def __post_init__(self) -> None:
        for value in (self.max_position_pct, self.gap_stress_pct, self.max_stressed_risk_pct):
            if number(value, positive=True) > 100:
                raise AtlasError("Invalid provisional risk percentage.")
        if self.quote_max_age <= timedelta(0):
            raise AtlasError("Quote freshness must be positive.")
        if any(
            not isinstance(value, bool)
            for value in (
                self.leverage_enabled,
                self.short_selling_enabled,
                self.broker_execution_enabled,
            )
        ):
            raise AtlasError("Safety switches must be boolean.")
        if any((self.leverage_enabled, self.short_selling_enabled, self.broker_execution_enabled)):
            raise AtlasError("Leverage, shorts and broker execution are disabled.")


class RiskEngine:
    def __init__(
        self,
        ledger: PortfolioLedger,
        costs: CostEngine,
        calendar: SessionCalendar,
        policy: RiskPolicy | None = None,
    ) -> None:
        self.ledger = ledger
        self.costs = costs
        self.calendar = calendar
        self.policy = policy if policy is not None else RiskPolicy()
        self.kill_switch = False

    def evaluate(
        self,
        intent: TradeIntent,
        instrument: Instrument,
        quote: MarketQuote | None,
        bars: tuple[MarketBar, ...],
        session: TradingSession | None,
        rate: FxRate | None,
        now: datetime,
        *,
        snapshot: PortfolioSnapshot | None = None,
    ) -> RiskDecision:
        with self.ledger.lock:
            utc(now)
            current = self.ledger.snapshot(now)
            state = snapshot if snapshot is not None else current
            signal = intent.signal
            reasons = []
            planned = stressed = capital = ZERO
            if state != current or not state.consistent:
                reasons.append("SNAPSHOT_INCONSISTENT")
            if self.kill_switch:
                reasons.append("KILL_SWITCH")
            if instrument.asset_class != "equity":
                reasons.append("UNSUPPORTED_INSTRUMENT")
            if signal.side != "buy":
                reasons.append("SHORTS_DISABLED")
            if signal.instrument_id != instrument.instrument_id:
                reasons.append("INSTRUMENT_MISMATCH")
            if not instrument.valid_quantity(intent.quantity):
                reasons.append("INVALID_QUANTITY")
            if self.ledger.seen(signal.intent_id):
                reasons.append("DUPLICATE_INTENT")
            if self.ledger.has_pending_instrument(instrument.instrument_id):
                reasons.append("INSTRUMENT_ALREADY_PENDING")
            if any(p.instrument.instrument_id == instrument.instrument_id for p in state.positions):
                reasons.append("POSITION_ALREADY_OPEN")
            if signal.decision_time > now or now >= signal.expires_at:
                reasons.append("SIGNAL_EXPIRED_OR_FUTURE")
            if any(bar.visible_time > signal.decision_time for bar in bars):
                reasons.append("FUTURE_SIGNAL_EVIDENCE")
            if session is None or not session.contains(now):
                reasons.append("MARKET_CLOSED")
            else:
                if signal.decision_time < session.open or signal.time_exit >= session.close:
                    reasons.append("INVALID_SESSION_LIFETIME")
                try:
                    if self.calendar.session(instrument.venue, session.local_date) != session:
                        reasons.append("CALENDAR_MISMATCH")
                except AtlasError:
                    reasons.append("CALENDAR_UNKNOWN")
                reasons.extend(bar_errors(bars, session, now, instrument.instrument_id))
            if any(
                bar.unit != instrument.quote_unit
                or bar.adjustment != Adjustment.RAW
                or any(
                    not instrument.valid_price(p) for p in (bar.open, bar.high, bar.low, bar.close)
                )
                for bar in bars
            ):
                reasons.append("INVALID_BAR_UNIT_ADJUSTMENT_OR_TICK")
            if any(
                now - p.mark_time > timedelta(minutes=5) or p.mark_time > now
                for p in state.positions
            ):
                reasons.append("STALE_PORTFOLIO_MARK")
            daily_loss = max(ZERO, -state.daily_realized_pnl_gbp) + max(
                ZERO, -state.daily_unrealized_pnl_gbp
            )
            if daily_loss >= self.ledger.settings.daily_loss_threshold_gbp:
                reasons.append("DAILY_LOSS_THRESHOLD")
            if quote is None:
                reasons.append("MISSING_QUOTE")
            elif (
                quote.instrument_id != instrument.instrument_id
                or quote.unit != instrument.quote_unit
                or quote.adjustment != Adjustment.RAW
                or not quote.complete
                or quote.quality
            ):
                reasons.append("INVALID_QUOTE")
            elif quote.visible_time > now or now - quote.event_time > self.policy.quote_max_age:
                reasons.append("STALE_OR_FUTURE_QUOTE")
            elif quote.event_time <= signal.decision_time:
                reasons.append("NO_NEXT_ACTIONABLE_PRICE")
            elif quote.ask <= signal.stop or quote.ask >= signal.target:
                reasons.append("INVALID_ENTRY_PLAN")
            elif quote.bid <= signal.stop:
                reasons.append("ENTRY_BID_AT_OR_BELOW_STOP")
            elif not instrument.valid_price(quote.ask) or not instrument.valid_price(quote.bid):
                reasons.append("INVALID_TICK")
            elif rate is None:
                reasons.append("MISSING_FX")
            elif instrument.valid_quantity(intent.quantity):
                try:
                    estimate = self.costs.estimate(
                        instrument,
                        intent.quantity,
                        quote.ask,
                        signal.stop,
                        rate,
                        now,
                        entry_basis=PriceBasis.EXECUTABLE,
                    )
                    capital = estimate.entry_cash_gbp
                    distance = instrument.quote_unit.major(quote.ask - signal.stop)
                    planned = (
                        distance * intent.quantity * rate.gbp_per_unit + estimate.total_cost_gbp
                    )
                    stressed = (
                        planned
                        + estimate.entry_notional_gbp * self.policy.gap_stress_pct / 100
                        + estimate.stressed_cost_gbp
                        - estimate.total_cost_gbp
                    )
                    settings = self.ledger.settings
                    if planned > settings.planned_risk_per_trade_gbp:
                        reasons.append("PER_TRADE_RISK")
                    if (
                        planned + state.concurrent_planned_risk_gbp
                        > settings.max_concurrent_planned_risk_gbp
                    ):
                        reasons.append("AGGREGATE_RISK")
                    if daily_loss + planned > settings.daily_loss_threshold_gbp:
                        reasons.append("DAILY_RISK_BUDGET")
                    if capital > state.available_cash_gbp:
                        reasons.append("INSUFFICIENT_CASH")
                    if capital > settings.starting_capital_gbp * self.policy.max_position_pct / 100:
                        reasons.append("POSITION_SIZE")
                    if (
                        stressed
                        > settings.starting_capital_gbp * self.policy.max_stressed_risk_pct / 100
                    ):
                        reasons.append("STRESSED_RISK")
                except AtlasError as error:
                    # All messages from the local cost module are static reason codes.
                    reasons.append(str(error))
            return RiskDecision(
                DecisionStatus.REJECTED if reasons else DecisionStatus.APPROVED,
                tuple(sorted(set(reasons))),
                signal.intent_id,
                intent.quantity,
                planned,
                stressed,
                capital,
                self.policy.version,
                current.version,
                now,
            )

    def authorize(
        self,
        intent: TradeIntent,
        instrument: Instrument,
        quote: MarketQuote | None,
        bars: tuple[MarketBar, ...],
        session: TradingSession | None,
        rate: FxRate | None,
        now: datetime,
    ) -> RiskDecision:
        """Recompute then reserve atomically. Never accepts a supplied approval."""
        with self.ledger.lock:
            decision = self.evaluate(intent, instrument, quote, bars, session, rate, now)
            if decision.status == DecisionStatus.APPROVED:
                if quote is None or rate is None:
                    raise AssertionError("Approved risk requires a quote and FX")
                estimate = self.costs.estimate(
                    instrument,
                    intent.quantity,
                    quote.ask,
                    intent.signal.stop,
                    rate,
                    now,
                    entry_basis=PriceBasis.EXECUTABLE,
                )
                self.ledger.reserve(
                    instrument,
                    intent.signal,
                    intent.quantity,
                    decision.capital_usage_gbp,
                    decision.planned_risk_gbp,
                    decision.stressed_risk_gbp,
                    now,
                    decision.snapshot_version,
                    entry_price_ceiling=quote.ask,
                    fx_ceiling=rate.gbp_per_unit,
                    entry_fee_budget_gbp=estimate.entry_cost_gbp,
                )
            return decision

    def size(
        self,
        signal: StrategySignal,
        instrument: Instrument,
        quote: MarketQuote,
        rate: FxRate,
        now: datetime,
    ) -> Decimal:
        """Bounded binary search for a feasible trial size; never grants approval."""
        if quote.ask <= signal.stop:
            return ZERO
        settings = self.ledger.settings
        state = self.ledger.snapshot(now)
        entry = instrument.quote_unit.major(quote.ask) * rate.gbp_per_unit
        distance = instrument.quote_unit.major(quote.ask - signal.stop) * rate.gbp_per_unit
        bound = min(
            max(ZERO, state.available_cash_gbp) / entry,
            settings.starting_capital_gbp * self.policy.max_position_pct / 100 / entry,
            settings.planned_risk_per_trade_gbp / distance,
        )
        high = int(floor_step(bound, instrument.quantity_step) / instrument.quantity_step)
        low, best = 1, ZERO
        while low <= high:
            middle = (low + high) // 2
            qty = middle * instrument.quantity_step
            if qty < instrument.minimum_quantity:
                low = middle + 1
                continue
            try:
                estimate = self.costs.estimate(
                    instrument,
                    qty,
                    quote.ask,
                    signal.stop,
                    rate,
                    now,
                    entry_basis=PriceBasis.EXECUTABLE,
                )
            except AtlasError:
                return ZERO
            if (
                distance * qty + estimate.total_cost_gbp <= settings.planned_risk_per_trade_gbp
                and estimate.entry_cash_gbp <= state.available_cash_gbp
            ):
                best, low = qty, middle + 1
            else:
                high = middle - 1
        return best
