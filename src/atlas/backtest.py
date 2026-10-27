"""Chronological, quote-driven synthetic replay using the common cost/risk/ledger modules."""

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from itertools import groupby

from atlas.config import AtlasError
from atlas.costs import CostEngine
from atlas.domain import (
    ZERO,
    Adjustment,
    AuditEvent,
    DecisionStatus,
    MarketBar,
    MarketQuote,
    PortfolioSnapshot,
    Position,
    PriceBasis,
    TradeIntent,
    floor_step,
)
from atlas.portfolio import PortfolioLedger
from atlas.research import ResearchSettings
from atlas.risk import RiskEngine, RiskPolicy
from atlas.sessions import ReplayClock, SessionCalendar
from atlas.strategy import ORBConfig, ORBStrategy
from atlas.synthetic import LABEL, SyntheticDataset, fixture_calendar, synthetic_costs


def jsonable(value):
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass(frozen=True)
class BacktestReport:
    label: str
    starting_capital_gbp: Decimal
    ending_equity_gbp: Decimal
    opportunities: int
    simulated_trades: int
    rejected_opportunities: tuple[tuple[str, tuple[str, ...]], ...]
    gross_pnl_gbp: Decimal
    net_pnl_gbp: Decimal
    total_costs_gbp: Decimal
    fx_conversion_costs_gbp: Decimal
    wins: int
    losses: int
    maximum_drawdown_gbp: Decimal
    maximum_cash_utilisation_pct: Decimal
    risk_limit_events: tuple[tuple[str, tuple[str, ...]], ...]
    quality_events: tuple[tuple[str, int], ...]
    open_positions: int
    reconciled: bool
    dataset_checksum: str
    dataset_provenance: tuple[tuple[str, str], ...]
    configuration: dict
    configuration_checksum: str
    fills: tuple
    audit: tuple
    risk_decisions: tuple

    def to_dict(self) -> dict:
        return jsonable(self)


def run_backtest(
    dataset: SyntheticDataset,
    settings: ResearchSettings | None = None,
    *,
    costs: CostEngine | None = None,
    orb: ORBConfig | None = None,
    policy: RiskPolicy | None = None,
    calendar: SessionCalendar | None = None,
    clock: ReplayClock | None = None,
    observer: Callable[[str, PortfolioSnapshot, object, bool], None] | None = None,
) -> BacktestReport:
    """At most one hypothesis per instrument/session, no automatic retry of a rejected intent."""
    settings = settings if settings is not None else ResearchSettings()
    orb = orb if orb is not None else ORBConfig()
    policy = policy if policy is not None else RiskPolicy()
    inst = dataset.instrument
    ledger = PortfolioLedger(settings)
    engine = costs if costs is not None else synthetic_costs()
    calendar = calendar if calendar is not None else fixture_calendar()
    provenance = getattr(calendar, "provenance", None)
    if not callable(provenance):
        raise AtlasError("CALENDAR_PROVENANCE_UNAVAILABLE")
    calendar_configuration = provenance()
    if not isinstance(calendar_configuration, dict) or not calendar_configuration:
        raise AtlasError("CALENDAR_PROVENANCE_UNAVAILABLE")
    risk = RiskEngine(ledger, engine, calendar, policy)
    strategy = ORBStrategy(orb)
    clock = clock if clock is not None else ReplayClock(dataset.session.open)
    bars = []
    pending = {}
    identified = set()
    rejected = []
    risk_events = []
    decisions = []
    quality = Counter()
    peak = settings.starting_capital_gbp
    drawdown = utilisation = ZERO
    daily_breached = False
    stop_triggered = False

    def emit(kind: str, value: object = None) -> None:
        if observer is not None:
            observer(kind, ledger.snapshot(clock.now()), value, risk.kill_switch)

    emit("portfolio.valuation")
    # Default stable ties: bars before quotes; known stop quotes take precedence.
    events = [(b.visible_time, 0, i, b) for i, b in enumerate(dataset.bars)]
    events += [(q.visible_time, 1, i, q) for i, q in enumerate(dataset.quotes)]
    events.sort(key=lambda event: event[:3])

    def eligible_exit_quote(quote: MarketQuote, position: Position, now: datetime) -> bool:
        return (
            quote.instrument_id == inst.instrument_id
            and quote.unit == inst.quote_unit
            and quote.adjustment == Adjustment.RAW
            and quote.complete
            and not quote.quality
            and inst.valid_price(quote.bid)
            and inst.valid_price(quote.ask)
            and position.opened_at < quote.event_time <= now
            and quote.visible_time <= now
            and now - quote.event_time <= policy.quote_max_age
        )

    def ordered_events():
        for timestamp, group in groupby(events, key=lambda event: event[0]):
            clock.advance(timestamp)
            simultaneous = list(group)
            state = ledger.snapshot(timestamp)
            if state.positions:
                position = state.positions[0]
                # All these observations are available now. Resolve simultaneous
                # adverse evidence before a profitable bar/time exit, without
                # consulting any later availability group.
                simultaneous.sort(
                    key=lambda event: (
                        not (
                            isinstance(event[3], MarketQuote)
                            and eligible_exit_quote(event[3], position, timestamp)
                            and event[3].bid <= position.signal.stop
                        )
                    )
                )
            yield from simultaneous

    def close_position(price: Decimal, basis: PriceBasis, now: datetime, reason: str) -> None:
        snapshot = ledger.snapshot(now)
        if not snapshot.positions:
            return
        position = snapshot.positions[0]
        # Only the exit leg is charged here; entry costs were debited at its fill.
        estimate = engine.estimate(
            inst,
            position.quantity,
            position.signal.entry,
            price,
            dataset.fx,
            now,
            entry_basis=PriceBasis.EXECUTABLE,
            exit_basis=basis,
        )
        ledger.exit_fill(
            inst.instrument_id,
            f"{position.signal.intent_id}:exit",
            position.quantity,
            price,
            dataset.fx,
            estimate.exit_cost_gbp,
            now,
            cost_components=tuple(
                (name, amount) for name, amount in estimate.components if name.startswith("exit_")
            ),
        )
        quality[f"EXIT_{reason}"] += 1
        emit("fill.confirmed", ledger.history[-1])

    for _timestamp, _, _, observation in ordered_events():
        now = clock.now()
        for intent_id, signal in list(pending.items()):
            if now >= signal.expires_at:
                rejected.append((intent_id, ("SIGNAL_EXPIRED_NO_PRICE",)))
                del pending[intent_id]
        snapshot = ledger.snapshot(now)
        if isinstance(observation, MarketBar):
            bar = observation
            bars.append(bar)
            if snapshot.positions:
                position = snapshot.positions[0]
                # A candle starting before the fill may contain pre-fill extrema.
                if (
                    not stop_triggered
                    and bar.complete
                    and not bar.quality
                    and bar.unit == inst.quote_unit
                    and bar.instrument_id == inst.instrument_id
                    and bar.adjustment == Adjustment.RAW
                    and all(inst.valid_price(p) for p in (bar.open, bar.high, bar.low, bar.close))
                    and bar.start >= position.opened_at
                    and bar.end <= position.signal.time_exit
                ):
                    signal = position.signal
                    if bar.low <= signal.stop:
                        close_position(
                            min(bar.open, signal.stop), PriceBasis.MID, now, "STOP_OR_GAP"
                        )
                    elif bar.high >= signal.target:
                        close_position(signal.target, PriceBasis.MID, now, "TARGET")
                    else:
                        ledger.mark(inst.instrument_id, bar.close, dataset.fx, now)
            result = strategy.evaluate(inst, tuple(bars), dataset.session, now)
            for reason in result.reasons:
                quality[reason] += 1
            if result.signal is not None and result.signal.intent_id not in identified:
                identified.add(result.signal.intent_id)
                pending[result.signal.intent_id] = result.signal
                emit("signal.generated", result.signal)
        elif isinstance(observation, MarketQuote):
            quote = observation
            if snapshot.positions and eligible_exit_quote(quote, snapshot.positions[0], now):
                position = snapshot.positions[0]
                ledger.mark(inst.instrument_id, quote.bid, dataset.fx, now)
                stop_triggered = stop_triggered or quote.bid <= position.signal.stop
                if stop_triggered:
                    if quote.bid_size >= position.quantity:
                        close_position(
                            min(quote.bid, position.signal.stop),
                            PriceBasis.EXECUTABLE,
                            now,
                            "QUOTE_STOP_OR_GAP",
                        )
                    else:
                        quality["INSUFFICIENT_STOP_EXIT_LIQUIDITY"] += 1
                elif now >= position.signal.time_exit:
                    if quote.bid_size >= position.quantity:
                        close_position(quote.bid, PriceBasis.EXECUTABLE, now, "TIME")
                    else:
                        quality["INSUFFICIENT_TIME_EXIT_LIQUIDITY"] += 1
            for intent_id, signal in list(pending.items()):
                if (
                    quote.event_time <= signal.decision_time
                    and quote.visible_time == quote.event_time
                ):
                    continue
                qty = risk.size(signal, inst, quote, dataset.fx, now)
                if qty == 0:
                    probe = risk.evaluate(
                        TradeIntent(signal, inst.minimum_quantity),
                        inst,
                        quote,
                        tuple(bars),
                        dataset.session,
                        dataset.fx,
                        now,
                    )
                    reasons = probe.reasons or ("NO_FEASIBLE_QUANTITY",)
                    if probe.status == DecisionStatus.APPROVED:
                        probe = replace(probe, status=DecisionStatus.RESEARCH_ONLY, reasons=reasons)
                    decisions.append(probe)
                    emit("risk.decided", probe)
                    rejected.append((intent_id, reasons))
                    risk_events.append((intent_id, reasons))
                    del pending[intent_id]
                    continue
                intent = TradeIntent(signal, qty)
                decision = risk.authorize(
                    intent,
                    inst,
                    quote,
                    tuple(bars),
                    dataset.session,
                    dataset.fx,
                    now,
                )
                decisions.append(decision)
                emit("risk.decided", decision)
                del pending[intent_id]
                if decision.status != DecisionStatus.APPROVED:
                    rejected.append((intent_id, decision.reasons))
                    risk_events.append((intent_id, decision.reasons))
                    continue
                emit("intent.created", intent)
                fill_qty = floor_step(min(qty, quote.ask_size), inst.quantity_step)
                if fill_qty < inst.minimum_quantity:
                    ledger.cancel(intent_id, now)
                    rejected.append((intent_id, ("NO_LIQUIDITY_NO_FILL",)))
                    continue
                estimate = engine.estimate(
                    inst,
                    fill_qty,
                    quote.ask,
                    signal.stop,
                    dataset.fx,
                    now,
                    entry_basis=PriceBasis.EXECUTABLE,
                )
                ledger.entry_fill(
                    intent_id,
                    f"{intent_id}:entry",
                    fill_qty,
                    quote.ask,
                    dataset.fx,
                    estimate.entry_cost_gbp,
                    now,
                    cost_components=tuple(
                        (name, amount)
                        for name, amount in estimate.components
                        if name.startswith("entry_")
                    ),
                )
                # One next-quote fill attempt; release the unfilled remainder.
                ledger.cancel(intent_id, now)
                ledger.mark(inst.instrument_id, quote.bid, dataset.fx, now)
                emit("fill.confirmed", ledger.history[-1])
                if fill_qty != qty:
                    quality["PARTIAL_FILL_REMAINDER_CANCELLED"] += 1
        snapshot = ledger.snapshot(now)
        peak = max(peak, snapshot.equity_gbp)
        drawdown = max(drawdown, peak - snapshot.equity_gbp)
        utilisation = max(
            utilisation,
            (settings.starting_capital_gbp - snapshot.available_cash_gbp)
            / settings.starting_capital_gbp
            * 100,
        )
        daily_loss = max(ZERO, -snapshot.daily_realized_pnl_gbp) + max(
            ZERO, -snapshot.daily_unrealized_pnl_gbp
        )
        if daily_loss >= settings.daily_loss_threshold_gbp and not daily_breached:
            risk_events.append((now.isoformat(), ("DAILY_LOSS_THRESHOLD",)))
            emit("risk.breached", ("DAILY_LOSS_THRESHOLD",))
            daily_breached = True
        if not snapshot.consistent:
            raise AtlasError("Synthetic ledger failed reconciliation.")
        emit("portfolio.valuation")
    for intent_id in pending:
        rejected.append((intent_id, ("NO_ACTIONABLE_PRICE",)))
    final = ledger.snapshot(clock.now())
    if final.positions:
        quality["UNLIQUIDATED_POSITION_NO_VALID_EXIT_PRICE"] += 1
    configuration = jsonable(
        {
            "settings": settings,
            "risk_policy": policy,
            "orb": orb,
            "schedules": engine.schedules,
            "calendar": calendar_configuration,
            "fill_model": (
                "next-quote-one-attempt; quote-stop-first-at-ties; latched-stop; "
                "gap-at-worse-price; v2"
            ),
            "account_base_currency": "GBP",
        }
    )
    import json

    checksum = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    exits = [fill for fill in ledger.history if fill.side == "sell"]
    net = final.equity_gbp - settings.starting_capital_gbp
    return BacktestReport(
        LABEL,
        settings.starting_capital_gbp,
        final.equity_gbp,
        len(identified),
        sum(fill.side == "buy" for fill in ledger.history),
        tuple(rejected),
        net + ledger.total_fees_gbp,
        net,
        ledger.total_fees_gbp,
        ledger.fx_conversion_costs_gbp,
        sum(fill.realized_pnl_gbp > 0 for fill in exits),
        sum(fill.realized_pnl_gbp < 0 for fill in exits),
        drawdown,
        utilisation,
        tuple(risk_events),
        tuple(sorted(quality.items())),
        len(final.positions),
        final.consistent,
        dataset.checksum,
        dataset.provenance,
        configuration,
        checksum,
        ledger.history,
        tuple(
            sorted(
                ledger.audit
                + tuple(
                    AuditEvent(
                        item.timestamp, "risk", item.status.value, item.intent_id, item.reasons
                    )
                    for item in decisions
                ),
                key=lambda event: (event.timestamp, event.component, event.reference, event.event),
            )
        ),
        tuple(decisions),
    )
