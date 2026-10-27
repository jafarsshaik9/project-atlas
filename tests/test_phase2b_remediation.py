"""Focused coverage of the risk, chronological exit and calendar acceptance fixes."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from atlas.backtest import run_backtest
from atlas.config import AtlasError
from atlas.domain import DecisionStatus, QuoteUnit, TradeIntent
from atlas.sessions import FixtureCalendar
from atlas.synthetic import fixture_calendar, generate

D = Decimal


def authorize(context):
    decision = context.risk.authorize(
        TradeIntent(context.signal, D("10")),
        context.data.instrument,
        context.quote,
        context.bars,
        context.data.session,
        context.data.fx,
        context.quote.event_time,
    )
    assert decision.status == DecisionStatus.APPROVED
    return decision


def test_partial_entries_and_exit_keep_envelope_cancel_remainder_and_reconcile(context):
    decision = authorize(context)
    now = context.quote.event_time
    for fill_id, quantity in (("entry-1", D("2")), ("entry-2", D("3"))):
        context.ledger.entry_fill(
            context.signal.intent_id,
            fill_id,
            quantity,
            context.quote.ask,
            context.data.fx,
            D(".01"),
            now,
        )
        snapshot = context.ledger.snapshot(now)
        assert snapshot.consistent
        assert snapshot.concurrent_planned_risk_gbp == decision.planned_risk_gbp
    context.ledger.exit_fill(
        context.data.instrument.instrument_id,
        "partial-exit",
        D("1"),
        context.quote.bid,
        context.data.fx,
        D(".01"),
        now,
    )
    remaining = context.ledger.snapshot(now)
    assert remaining.positions[0].quantity == 4
    assert not remaining.pending_intents and remaining.reserved_cash_gbp == 0
    assert remaining.concurrent_planned_risk_gbp == decision.planned_risk_gbp
    assert remaining.positions[0].stressed_risk_gbp == decision.stressed_risk_gbp
    with pytest.raises(AtlasError, match="NO_ACTIVE_RESERVATION"):
        context.ledger.entry_fill(
            context.signal.intent_id,
            "late-remainder",
            D("5"),
            context.quote.ask,
            context.data.fx,
            D(".01"),
            now,
        )
    assert context.ledger.snapshot(now) == remaining
    context.ledger.exit_fill(
        context.data.instrument.instrument_id,
        "final-exit",
        D("4"),
        context.quote.bid,
        context.data.fx,
        D(".01"),
        now,
    )
    final = context.ledger.snapshot(now)
    assert final.consistent and not final.positions
    assert final.concurrent_planned_risk_gbp == 0
    assert final.realized_pnl_gbp == D("-.14")
    assert final.equity_gbp == final.available_cash_gbp == D("999.86")
    assert context.ledger.total_fees_gbp == D(".04")


@pytest.mark.parametrize("planned,stressed", [("0", "1"), ("1", "0"), ("1", ".5")])
def test_unknown_or_inconsistent_reservation_risk_fails_before_cash_changes(
    context, planned, stressed
):
    now = context.quote.event_time
    before = context.ledger.snapshot(now)
    with pytest.raises(AtlasError, match="UNKNOWN_OR_INVALID_RISK"):
        context.ledger.reserve(
            context.data.instrument,
            context.signal,
            D("10"),
            D("26"),
            D(planned),
            D(stressed),
            now,
            before.version,
        )
    assert context.ledger.snapshot(now) == before


@pytest.mark.parametrize("state", ["position", "reservation"])
def test_missing_risk_metadata_blocks_new_risk_and_entry_fills(context, state):
    authorize(context)
    now = context.quote.event_time
    if state == "position":
        context.ledger.entry_fill(
            context.signal.intent_id,
            "entry",
            D("10"),
            context.quote.ask,
            context.data.fx,
            D("0"),
            now,
        )
        key = context.data.instrument.instrument_id
        context.ledger._positions[key] = replace(
            context.ledger._positions[key], planned_risk_gbp=D("0"), stressed_risk_gbp=D("0")
        )
    else:
        key = context.signal.intent_id
        context.ledger._reservations[key] = replace(
            context.ledger._reservations[key], planned_risk_gbp=D("0"), stressed_risk_gbp=D("0")
        )
        before = context.ledger.snapshot(now)
        with pytest.raises(AtlasError, match="SNAPSHOT_INCONSISTENT"):
            context.ledger.entry_fill(
                key, "entry", D("1"), context.quote.ask, context.data.fx, D("0"), now
            )
        assert context.ledger.snapshot(now) == before
    assert not context.ledger.snapshot(now).consistent
    decision = context.risk.evaluate(
        TradeIntent(replace(context.signal, intent_id="new-intent"), D("1")),
        context.data.instrument,
        context.quote,
        context.bars,
        context.data.session,
        context.data.fx,
        now,
    )
    assert decision.status == DecisionStatus.REJECTED
    assert "SNAPSHOT_INCONSISTENT" in decision.reasons


def with_stop_quote(*, changes=None, event_delay=timedelta(seconds=1), receipt_delay=timedelta(0)):
    data = generate()
    entry = data.quotes[0]
    event = entry.event_time + event_delay
    breach = replace(
        entry,
        event_time=event,
        available_time=event + receipt_delay,
        received_time=event + receipt_delay,
        bid=D("245"),
        ask=D("247"),
        **(changes or {}),
    )
    bars = list(data.bars)
    bars[4] = replace(bars[4], low=D("240"))
    return replace(data, bars=tuple(bars), quotes=(entry, breach, data.quotes[-1]))


def test_quote_gap_fills_at_available_bid_with_costs_and_deterministic_replay():
    data = with_stop_quote(receipt_delay=timedelta(seconds=2))
    first = run_backtest(data)
    assert first.to_dict() == run_backtest(data).to_dict()
    exit_fill = first.fills[-1]
    assert exit_fill.timestamp == data.quotes[1].visible_time
    assert exit_fill.price == D("245") < D("250")
    assert dict(exit_fill.cost_components)["exit_spread"] == 0
    assert first.net_pnl_gbp == first.gross_pnl_gbp - first.total_costs_gbp < 0
    assert first.reconciled and not first.open_positions


@pytest.mark.parametrize(
    "changes,event_delay,receipt_delay",
    [
        ({"complete": False}, 1, 0),
        ({"quality": ("bad",)}, 1, 0),
        ({"unit": QuoteUnit.GBP}, 1, 0),
        ({"instrument_id": "OTHER"}, 1, 0),
        ({}, -1, 2),  # Received after entry, but observed before the position existed.
        ({}, 1, 61),  # Stale even though publication/receipt order is valid.
    ],
)
def test_ineligible_stop_quotes_cannot_supply_exit_fills(changes, event_delay, receipt_delay):
    data = with_stop_quote(
        changes=changes,
        event_delay=timedelta(seconds=event_delay),
        receipt_delay=timedelta(seconds=receipt_delay),
    )
    report = run_backtest(data)
    assert report.fills[-1].price == D("259")
    assert "EXIT_QUOTE_STOP_OR_GAP" not in dict(report.quality_events)


def test_stop_quote_at_same_availability_as_profitable_bar_takes_precedence():
    data = generate()
    now = data.bars[5].visible_time
    breach = replace(
        data.quotes[0],
        event_time=now - timedelta(seconds=1),
        available_time=now,
        received_time=now,
        bid=D("245"),
        ask=D("247"),
    )
    bars = list(data.bars)
    bars[5] = replace(bars[5], low=D("240"))
    report = run_backtest(
        replace(data, bars=tuple(bars), quotes=(data.quotes[0], breach, data.quotes[-1]))
    )
    assert report.fills[-1].timestamp == now
    assert report.fills[-1].price == D("245")
    assert dict(report.quality_events)["EXIT_QUOTE_STOP_OR_GAP"] == 1
    assert report.net_pnl_gbp < 0


def test_illiquid_stop_stays_triggered_and_cannot_turn_into_later_target_profit():
    data = with_stop_quote(changes={"bid_size": D("0")})
    report = run_backtest(data)
    assert dict(report.quality_events)["INSUFFICIENT_STOP_EXIT_LIQUIDITY"] == 1
    assert report.fills[-1].timestamp == data.quotes[-1].visible_time
    assert report.fills[-1].price == D("250")
    assert report.net_pnl_gbp < 0
    assert "EXIT_TARGET" not in dict(report.quality_events)
    unresolved = run_backtest(replace(data, quotes=data.quotes[:2]))
    assert unresolved.open_positions == 1 and unresolved.reconciled
    assert len(unresolved.fills) == 1
    assert dict(unresolved.quality_events)["UNLIQUIDATED_POSITION_NO_VALID_EXIT_PRICE"] == 1


def test_equivalent_calendar_entry_order_has_identical_configuration_and_results():
    calendar = fixture_calendar()
    first = run_backtest(generate(), calendar=calendar)
    equivalent = run_backtest(
        generate(), calendar=replace(calendar, entries=calendar.entries[::-1])
    )
    assert first.to_dict() == equivalent.to_dict()
    assert first.configuration["calendar"]["unknown_date_policy"] == "reject"


@pytest.mark.parametrize(
    "change",
    [
        {"close": timedelta(hours=-1)},
        {"open": timedelta(minutes=1)},
        {"half_day": True},
        {"timezone_name": "Etc/UTC"},
        {"utc_offset_minutes": 60},
    ],
)
def test_calendar_session_assumptions_change_configuration_fingerprint(change):
    data = generate()
    changed = {
        name: getattr(data.session, name) + value if isinstance(value, timedelta) else value
        for name, value in change.items()
    }
    session = replace(data.session, **changed)
    baseline_calendar = FixtureCalendar(
        ((data.instrument.venue, data.session.local_date, data.session),)
    )
    changed_calendar = FixtureCalendar(((data.instrument.venue, data.session.local_date, session),))
    baseline = run_backtest(data, calendar=baseline_calendar)
    alternative = run_backtest(data, calendar=changed_calendar)
    assert baseline.configuration_checksum != alternative.configuration_checksum
    assert baseline.dataset_checksum == alternative.dataset_checksum


def test_calendar_explicit_holiday_and_unknown_date_have_different_fingerprints():
    data = generate()
    unknown = run_backtest(data, calendar=FixtureCalendar(()))
    holiday = run_backtest(
        data, calendar=FixtureCalendar(((data.instrument.venue, data.session.local_date, None),))
    )
    assert unknown.configuration_checksum != holiday.configuration_checksum
    assert unknown.simulated_trades == holiday.simulated_trades == 0


def test_calendar_without_provenance_cannot_produce_an_unreproducible_report():
    class UndocumentedCalendar:
        def session(self, venue, local_date):
            return fixture_calendar().session(venue, local_date)

    with pytest.raises(AtlasError, match="CALENDAR_PROVENANCE_UNAVAILABLE"):
        run_backtest(generate(), calendar=UndocumentedCalendar())
