from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from atlas.config import AtlasError
from atlas.costs import CostEngine
from atlas.domain import DecisionStatus, TradeIntent
from atlas.portfolio import PortfolioLedger
from atlas.research import ResearchSettings
from atlas.risk import RiskEngine, RiskPolicy
from atlas.synthetic import fixture_calendar, synthetic_costs

D = Decimal


def reserve(context, *, intent_id="ledger-test", qty=4, cash=11, risk=4):
    qty, cash, risk = D(str(qty)), D(str(cash)), D(str(risk))
    now = context.quote.event_time
    signal = replace(context.signal, intent_id=intent_id)
    state = context.ledger.snapshot(now)
    context.ledger.reserve(
        context.data.instrument, signal, qty, cash, risk, risk * 2, now, state.version
    )
    return signal


def decision(context, qty=10, **changes):
    qty = D(str(qty))
    arguments = {
        "intent": TradeIntent(context.signal, qty),
        "instrument": context.data.instrument,
        "quote": context.quote,
        "bars": context.bars,
        "session": context.data.session,
        "rate": context.data.fx,
        "now": context.quote.event_time,
    }
    arguments.update(changes)
    return context.risk.evaluate(**arguments)


def test_initial_cash_reserved_funds_partial_fills_and_average_cost(context):
    signal = reserve(context)
    now = context.quote.event_time
    before = context.ledger.snapshot(now)
    assert before.available_cash_gbp == D("989")
    assert dict(before.cash_by_currency)["GBP"] == D("1000")
    context.ledger.entry_fill(
        signal.intent_id, "buy-1", D("2"), D("250"), context.data.fx, D(".1"), now
    )
    partial = context.ledger.snapshot(now)
    assert partial.positions[0].quantity == 2
    assert partial.reserved_cash_gbp == D("5.9")
    assert partial.concurrent_planned_risk_gbp == D("4")
    context.ledger.entry_fill(
        signal.intent_id, "buy-2", D("2"), D("260"), context.data.fx, D(".1"), now
    )
    filled = context.ledger.snapshot(now)
    assert filled.reserved_cash_gbp == 0
    assert filled.positions[0].average_cost_gbp == D("2.6")
    assert filled.available_cash_gbp == D("989.6")
    context.ledger.exit_fill(
        context.data.instrument.instrument_id,
        "sell-1",
        D("1"),
        D("300"),
        context.data.fx,
        D(".1"),
        now,
    )
    assert context.ledger.snapshot(now).realized_pnl_gbp == D(".3")
    context.ledger.exit_fill(
        context.data.instrument.instrument_id,
        "sell-2",
        D("3"),
        D("300"),
        context.data.fx,
        D(".1"),
        now,
    )
    final = context.ledger.snapshot(now)
    assert final.available_cash_gbp == D("1001.4")
    assert final.realized_pnl_gbp == D("1.4")
    assert final.daily_realized_pnl_gbp == D("1.4")
    assert final.equity_gbp == D("1001.4")
    assert context.ledger.total_fees_gbp == D(".4")
    assert not final.positions and context.ledger.reconcile()


def test_reservation_release_does_not_create_or_destroy_cash(context):
    signal = reserve(context)
    now = context.quote.event_time
    context.ledger.entry_fill(
        signal.intent_id, "partial", D("1"), D("250"), context.data.fx, D(".1"), now
    )
    context.ledger.cancel(signal.intent_id, now)
    state = context.ledger.snapshot(now)
    assert state.available_cash_gbp == D("997.4")
    assert state.reserved_cash_gbp == 0
    assert state.concurrent_planned_risk_gbp == D("4")  # retain conservative original risk
    assert context.ledger.seen(signal.intent_id)


def test_duplicates_overspending_and_failed_fills_are_atomic(context):
    signal = reserve(context)
    now = context.quote.event_time
    before = context.ledger.snapshot(now)
    with pytest.raises(AtlasError, match="DUPLICATE"):
        context.ledger.reserve(
            context.data.instrument, signal, D("1"), D("1"), D("1"), D("2"), now, before.version
        )
    with pytest.raises(AtlasError, match="FILL_EXCEEDS"):
        context.ledger.entry_fill(
            signal.intent_id, "too-large", D("4"), D("400"), context.data.fx, D("0"), now
        )
    assert context.ledger.snapshot(now) == before
    with pytest.raises(AtlasError, match="INSUFFICIENT"):
        reserve(context, intent_id="overspend", cash=D("1001"))
    assert context.ledger.snapshot(now) == before
    context.ledger.entry_fill(
        signal.intent_id, "valid", D("1"), D("250"), context.data.fx, D("0"), now
    )
    with pytest.raises(AtlasError, match="DUPLICATE"):
        context.ledger.entry_fill(
            signal.intent_id, "valid", D("1"), D("250"), context.data.fx, D("0"), now
        )


def test_no_unconfirmed_sale_proceeds_or_short_fills(context):
    now = context.quote.event_time
    state = context.ledger.snapshot(now)
    with pytest.raises(AtlasError, match="UNSUPPORTED_SHORT"):
        context.ledger.exit_fill(
            context.data.instrument.instrument_id,
            "short",
            D("1"),
            D("250"),
            context.data.fx,
            D("0"),
            now,
        )
    assert context.ledger.snapshot(now) == state


def test_daily_realized_resets_but_losses_still_reconcile(context):
    signal = reserve(context, qty=D("1"), cash=D("100"))
    now = context.quote.event_time
    context.ledger.entry_fill(
        signal.intent_id, "buy", D("1"), D("10000"), context.data.fx, D("0"), now
    )
    context.ledger.exit_fill(
        context.data.instrument.instrument_id,
        "sell",
        D("1"),
        D("8900"),
        context.data.fx,
        D("0"),
        now,
    )
    assert context.ledger.snapshot(now).daily_realized_pnl_gbp == D("-11")
    next_day = context.ledger.snapshot(now + timedelta(days=1))
    assert next_day.daily_realized_pnl_gbp == 0
    assert next_day.realized_pnl_gbp == D("-11")
    assert next_day.equity_gbp == D("989")


def test_risk_approves_local_research_and_reports_stress_separately(context):
    result = decision(context)
    assert result.status == DecisionStatus.APPROVED
    assert result.stressed_risk_gbp > result.planned_risk_gbp > 0
    assert result.planned_risk_gbp <= 5
    assert result.policy_version == "synthetic-risk-v1"
    assert result.snapshot_version == context.ledger.snapshot(result.timestamp).version


@pytest.mark.parametrize(
    "quantity,reason",
    [
        (D("200"), "PER_TRADE_RISK"),
        (D("500"), "INSUFFICIENT_CASH"),
        (D("110"), "POSITION_SIZE"),
        (D("1.5"), "INVALID_QUANTITY"),
    ],
)
def test_risk_limits(context, quantity, reason):
    result = decision(context, quantity)
    assert result.status == DecisionStatus.REJECTED and reason in result.reasons


def test_aggregate_concurrent_risk_includes_reservations(context):
    reserve(context, cash=D("20"), risk=D("9"))
    assert "AGGREGATE_RISK" in decision(context, D("50")).reasons


@pytest.mark.parametrize("realize", [True, False])
def test_daily_realized_and_unrealized_losses_prevent_new_risk(context, realize):
    signal = reserve(context, qty=D("1"), cash=D("100"))
    now = context.quote.event_time
    context.ledger.entry_fill(
        signal.intent_id, "buy", D("1"), D("10000"), context.data.fx, D("0"), now
    )
    if realize:
        context.ledger.exit_fill(
            context.data.instrument.instrument_id,
            "sell",
            D("1"),
            D("8900"),
            context.data.fx,
            D("0"),
            now,
        )
    else:
        context.ledger.mark(context.data.instrument.instrument_id, D("8900"), context.data.fx, now)
    assert "DAILY_LOSS_THRESHOLD" in decision(context).reasons


def test_missing_stale_future_or_incomplete_data(context):
    assert "MISSING_QUOTE" in decision(context, quote=None).reasons
    assert "MISSING_FX" in decision(context, rate=None).reasons
    assert "MISSING_BARS" in decision(context, bars=()).reasons
    stale = replace(context.quote, event_time=context.data.session.open)
    assert "STALE_OR_FUTURE_QUOTE" in decision(context, quote=stale).reasons
    future = replace(
        context.quote, received_time=context.quote.received_time + timedelta(seconds=1)
    )
    assert "STALE_OR_FUTURE_QUOTE" in decision(context, quote=future).reasons
    incomplete = context.bars[:-1] + (replace(context.bars[-1], complete=False),)
    assert "INCOMPLETE_BARS" in decision(context, bars=incomplete).reasons


def test_unknown_costs_and_fx_fail_closed(context):
    context.risk.costs = CostEngine(())
    assert "UNKNOWN_OR_AMBIGUOUS_COST_SCHEDULE" in decision(context).reasons
    context.risk.costs = synthetic_costs()
    rate = replace(context.data.fx, event_time=context.data.session.open - timedelta(days=2))
    assert "FX_UNAVAILABLE_OR_STALE" in decision(context, rate=rate).reasons


def test_unsupported_assets_shorts_closed_market_and_kill_switch(context):
    assert (
        "UNSUPPORTED_INSTRUMENT"
        in decision(
            context, instrument=replace(context.data.instrument, asset_class="forex")
        ).reasons
    )
    assert (
        "SHORTS_DISABLED"
        in decision(
            context, intent=TradeIntent(replace(context.signal, side="sell"), D("1"))
        ).reasons
    )
    assert "MARKET_CLOSED" in decision(context, session=None).reasons
    context.risk.kill_switch = True
    assert "KILL_SWITCH" in decision(context).reasons
    with pytest.raises(AtlasError):
        RiskPolicy(broker_execution_enabled=True)


def test_inconsistent_snapshot_and_duplicate_intents(context):
    state = context.ledger.snapshot(context.quote.event_time)
    assert (
        "SNAPSHOT_INCONSISTENT"
        in decision(context, snapshot=replace(state, equity_gbp=state.equity_gbp + D("1"))).reasons
    )
    result = context.risk.authorize(
        TradeIntent(context.signal, D("10")),
        context.data.instrument,
        context.quote,
        context.bars,
        context.data.session,
        context.data.fx,
        context.quote.event_time,
    )
    assert result.status == DecisionStatus.APPROVED
    assert "DUPLICATE_INTENT" in decision(context).reasons


def test_approval_flags_are_not_an_authorization_interface(context):
    with pytest.raises(TypeError):
        TradeIntent(context.signal, D("1"), approved=True)
    with pytest.raises(TypeError):
        context.risk.authorize(approval=decision(context))
    assert not context.ledger.snapshot(context.quote.event_time).pending_intents


def test_concurrent_authorizations_cannot_overspend(context):
    settings = ResearchSettings(
        planned_risk_per_trade_pct=D("50"),
        daily_loss_threshold_pct=D("50"),
        max_concurrent_planned_risk_pct=D("50"),
    )
    ledger = PortfolioLedger(settings)
    risk = RiskEngine(
        ledger,
        synthetic_costs(),
        fixture_calendar(),
        RiskPolicy(max_position_pct=D("100"), max_stressed_risk_pct=D("100")),
    )

    def authorize(index):
        return risk.authorize(
            TradeIntent(replace(context.signal, intent_id=f"concurrent-{index}"), D("250")),
            context.data.instrument,
            context.quote,
            context.bars,
            context.data.session,
            context.data.fx,
            context.quote.event_time,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(authorize, range(2)))
    assert sum(result.status == DecisionStatus.APPROVED for result in results) == 1
    assert any("INSUFFICIENT_CASH" in result.reasons for result in results)
    state = ledger.snapshot(context.quote.event_time)
    assert state.available_cash_gbp >= 0 and state.reserved_cash_gbp <= 1000
    assert state.consistent


@pytest.mark.parametrize("change", ["price", "fees"])
def test_authorized_fills_cannot_bypass_price_or_fee_bounds(context, change):
    now = context.quote.event_time
    result = context.risk.authorize(
        TradeIntent(context.signal, D("10")),
        context.data.instrument,
        context.quote,
        context.bars,
        context.data.session,
        context.data.fx,
        now,
    )
    assert result.status == DecisionStatus.APPROVED
    before = context.ledger.snapshot(now)
    with pytest.raises(AtlasError, match="NEW_RISK_CHECK"):
        context.ledger.entry_fill(
            context.signal.intent_id,
            "adverse-fill",
            D("1"),
            D("300") if change == "price" else context.quote.ask,
            context.data.fx,
            D("2") if change == "fees" else D("0"),
            now,
        )
    assert context.ledger.snapshot(now) == before


def test_stress_caps_and_lookahead_evidence_are_independent(context):
    context.risk.policy = RiskPolicy(max_stressed_risk_pct=D("0.01"))
    assert "STRESSED_RISK" in decision(context).reasons
    context.risk.policy = RiskPolicy()
    bars = context.bars[:-1] + (replace(context.bars[-1], received_time=context.quote.event_time),)
    assert "FUTURE_SIGNAL_EVIDENCE" in decision(context, bars=bars).reasons


def test_pending_reservation_cannot_fill_after_expiry(context):
    signal = reserve(context)
    with pytest.raises(AtlasError, match="NO_ACTIVE"):
        context.ledger.entry_fill(
            signal.intent_id,
            "late",
            D("1"),
            D("250"),
            context.data.fx,
            D("0"),
            signal.expires_at,
        )
    assert not context.ledger.history


def test_daily_equity_pnl_and_monotonic_ledger_time(context):
    signal = reserve(context, qty=D("1"), cash=D("3"))
    now = context.quote.event_time
    context.ledger.entry_fill(
        signal.intent_id, "buy", D("1"), D("250"), context.data.fx, D(".1"), now
    )
    assert context.ledger.snapshot(now).daily_pnl_gbp == D("-.1")
    context.ledger.mark(context.data.instrument.instrument_id, D("240"), context.data.fx, now)
    assert context.ledger.snapshot(now).daily_unrealized_pnl_gbp == D("-.2")
    next_day = now + timedelta(days=1)
    assert context.ledger.snapshot(next_day).daily_pnl_gbp == 0
    with pytest.raises(AtlasError, match="backwards"):
        context.ledger.snapshot(now)


def test_stale_snapshot_version_cannot_reserve(context):
    now = context.quote.event_time
    state = context.ledger.snapshot(now)
    reserve(context, intent_id="changes-state")
    with pytest.raises(AtlasError, match="SNAPSHOT"):
        context.ledger.reserve(
            context.data.instrument,
            context.signal,
            D("1"),
            D("3"),
            D("1"),
            D("2"),
            now,
            state.version,
        )


def test_bid_already_below_stop_prevents_new_entry(context):
    quote = replace(context.quote, bid=context.signal.stop)
    assert "ENTRY_BID_AT_OR_BELOW_STOP" in decision(context, quote=quote).reasons
