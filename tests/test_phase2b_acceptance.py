"""Acceptance regressions: desired invariants, intentionally exposing audit findings.

These are not expected-failure/skip tests. Production changes require a separate
remediation task; the acceptance audit must report their actual failing results.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from atlas.backtest import run_backtest
from atlas.config import AtlasError
from atlas.domain import DecisionStatus, TradeIntent
from atlas.sessions import FixtureCalendar
from atlas.synthetic import generate

D = Decimal


def test_remaining_entry_fill_retains_planned_risk_after_first_partial_is_closed(context):
    """F-01: risk cannot disappear while an authorized entry remainder is pending."""
    now = context.quote.event_time
    decision = context.risk.authorize(
        TradeIntent(context.signal, D("10")),
        context.data.instrument,
        context.quote,
        context.bars,
        context.data.session,
        context.data.fx,
        now,
    )
    assert decision.status == DecisionStatus.APPROVED
    context.ledger.entry_fill(
        context.signal.intent_id,
        "audit-first-entry",
        D("5"),
        context.quote.ask,
        context.data.fx,
        D("0"),
        now,
    )
    context.ledger.exit_fill(
        context.data.instrument.instrument_id,
        "audit-first-exit",
        D("5"),
        context.quote.bid,
        context.data.fx,
        D("0"),
        now,
    )
    try:
        context.ledger.entry_fill(
            context.signal.intent_id,
            "audit-remaining-entry",
            D("5"),
            context.quote.ask,
            context.data.fx,
            D("0"),
            now,
        )
    except AtlasError:
        # A safe remediation may invalidate/cancel the remainder instead.
        snapshot = context.ledger.snapshot(now)
        assert not snapshot.positions and not snapshot.pending_intents
        return
    snapshot = context.ledger.snapshot(now)
    assert snapshot.consistent and snapshot.positions[0].quantity == D("5")
    bare_stop_risk = (
        context.data.instrument.quote_unit.major(context.quote.ask - context.signal.stop)
        * D("5")
        * context.data.fx.gbp_per_unit
    )
    assert snapshot.concurrent_planned_risk_gbp >= bare_stop_risk


def test_observed_post_entry_quote_stop_breach_cannot_become_a_target_win():
    """F-02: known post-entry bid below stop is not an unknowable pre-fill OHLC extreme."""
    data = generate()
    entry_quote = data.quotes[0]
    breach_time = entry_quote.event_time + timedelta(seconds=1)
    breach_quote = replace(
        entry_quote,
        event_time=breach_time,
        available_time=breach_time,
        received_time=breach_time,
        bid=D("245"),
        ask=D("247"),
    )
    bars = list(data.bars)
    # Keep OHLC consistent with the additional observed price inside the fill candle.
    bars[4] = replace(bars[4], low=D("240"))
    report = run_backtest(
        replace(data, bars=tuple(bars), quotes=(entry_quote, breach_quote, data.quotes[-1]))
    )
    exits = [fill for fill in report.fills if fill.side == "sell"]
    assert report.simulated_trades == 1 and len(exits) == 1
    assert exits[0].timestamp == breach_time
    assert exits[0].price <= D("250")
    assert report.net_pnl_gbp < 0


def test_effective_calendar_is_part_of_replay_configuration_provenance():
    """F-03: outcome-changing injected inputs must be distinguishable in the report."""
    data = generate()
    baseline = run_backtest(data)
    closed = FixtureCalendar(((data.instrument.venue, data.session.local_date, None),))
    alternative = run_backtest(data, calendar=closed)
    assert baseline.simulated_trades == 1 and alternative.simulated_trades == 0
    assert baseline.dataset_checksum == alternative.dataset_checksum
    assert baseline.configuration_checksum != alternative.configuration_checksum
