from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from atlas.config import AtlasError
from atlas.costs import CostEngine
from atlas.domain import Currency, FxRate, PriceBasis, QuoteUnit, Venue, floor_step
from atlas.sessions import ReplayClock, bar_errors, local_time
from atlas.synthetic import SCENARIOS, fixture_calendar, generate, synthetic_costs

D = Decimal


def test_gbx_and_currency_units():
    assert QuoteUnit.GBX.major(D("250")) == D("2.50")
    assert QuoteUnit.GBP.major(D("250")) == D("250")
    assert QuoteUnit.GBX.currency == Currency.GBP
    assert QuoteUnit.USD.currency == Currency.USD
    assert floor_step(D("3.999"), D("0.1")) == D("3.9")


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-1"), D("0"), 250.0, "250"])
def test_invalid_bar_prices(value):
    with pytest.raises(AtlasError):
        replace(generate().bars[0], close=value)


@pytest.mark.parametrize(
    "change",
    [
        {"high": D("248")},
        {"low": D("252")},
        {"volume": D("-1")},
        {"start": datetime(2026, 10, 26)},
        {"unit": "GBX"},
        {"complete": "true"},
        {"adjustment": "unknown"},
    ],
)
def test_malformed_bar_contract(change):
    with pytest.raises(AtlasError):
        replace(generate().bars[0], **change)


def test_quote_contract_and_timestamp_order(context):
    with pytest.raises(AtlasError):
        replace(context.quote, bid=D("999"))
    with pytest.raises(AtlasError):
        replace(context.quote, available_time=context.quote.event_time - timedelta(seconds=1))
    with pytest.raises(AtlasError):
        replace(context.quote, ask=D("NaN"))
    with pytest.raises(AtlasError):
        replace(context.quote, received_time=datetime(2026, 10, 26))


def test_identity_not_ticker(context):
    other = replace(context.data.instrument, instrument_id="SYNTH-SECOND-LISTING")
    assert other.provider_symbols == context.data.instrument.provider_symbols
    assert other.instrument_id != context.data.instrument.instrument_id
    assert other.valid_quantity(D("1"))
    assert not other.valid_quantity(D("1.1"))
    assert not other.valid_price(D("250.5"))
    with pytest.raises(AtlasError):
        replace(other, quote_unit="EUR")


@pytest.mark.parametrize(
    "day,uk_hour,us_hour",
    [
        ("2026-03-06", 8, 14),
        ("2026-03-09", 8, 13),
        ("2026-03-27", 8, 13),
        ("2026-03-30", 7, 13),
        ("2026-10-23", 7, 13),
        ("2026-10-26", 8, 13),
        ("2026-11-02", 8, 14),
    ],
)
def test_explicit_dst_and_mismatch_sessions(day, uk_hour, us_hour):
    calendar = fixture_calendar()
    uk = calendar.session(Venue.LSE, date.fromisoformat(day))
    us = calendar.session(Venue.NASDAQ, date.fromisoformat(day))
    assert uk.open.hour == uk_hour
    assert us.open.hour == us_hour and us.open.minute == 30
    assert local_time(uk, uk.open).hour == 8
    assert local_time(us, us.open).hour == 9


def test_holidays_half_days_and_unknown_dates():
    calendar = fixture_calendar()
    assert calendar.session(Venue.LSE, date(2026, 12, 25)) is None
    assert calendar.session(Venue.NYSE, date(2026, 10, 25)) is None
    half = calendar.session(Venue.LSE, date(2026, 12, 24))
    assert half.half_day and local_time(half, half.close).hour == 12
    assert half.contains(half.open) and not half.contains(half.close)
    with pytest.raises(AtlasError, match="unknown"):
        calendar.session(Venue.LSE, date(2030, 1, 2))


def test_injectable_clock_rejects_backward_time(context):
    clock = ReplayClock(context.data.session.open)
    clock.advance(context.quote.event_time)
    assert clock.now() == context.quote.event_time
    with pytest.raises(AtlasError):
        clock.advance(context.data.session.open)


@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("missing", "MISSING_BARS"),
        ("duplicate", "DUPLICATE_BARS"),
        ("incomplete", "INCOMPLETE_BARS"),
    ],
)
def test_data_quality_slots(scenario, reason):
    data = generate(scenario)
    bars = tuple(b for b in data.bars if b.end <= data.session.open + timedelta(minutes=20))
    assert reason in bar_errors(
        bars, data.session, data.quotes[0].event_time, data.instrument.instrument_id
    )


def test_no_future_or_stale_candles(context):
    assert "FUTURE_DATA" in bar_errors(
        context.bars,
        context.data.session,
        context.bars[-2].end,
        context.data.instrument.instrument_id,
    )
    assert "STALE_BARS" in bar_errors(
        context.bars,
        context.data.session,
        context.bars[-1].end + timedelta(minutes=6),
        context.data.instrument.instrument_id,
    )


def hand_costs(context):
    fee = replace(
        synthetic_costs().schedules[0],
        spread_bps=D("20"),
        slippage_bps=D("10"),
        commission_fixed_gbp=D("1"),
        commission_bps=D("10"),
        fx_bps=D("25"),
        entry_tax_bps=D("50"),
        exit_tax_bps=D("0"),
        other_fixed_gbp=D("0.50"),
    )
    inst = replace(context.data.instrument, quote_unit=QuoteUnit.GBP, tick_size=D("0.01"))
    return CostEngine((fee,)), inst


def test_independently_calculated_round_trip_costs(context):
    engine, inst = hand_costs(context)
    estimate = engine.estimate(
        inst, D("10"), D("100"), D("110"), context.data.fx, context.quote.event_time
    )
    # Entry: spread 1 + slip 1 + commission 2 + tax 5 + other .50 = 9.50.
    # Exit: spread 1.10 + slip 1.10 + commission 2.10 + other .50 = 4.80.
    assert estimate.entry_notional_gbp == D("1000")
    assert estimate.entry_cost_gbp == D("9.50")
    assert estimate.exit_cost_gbp == D("4.80")
    assert estimate.total_cost_gbp == D("14.30")
    assert estimate.gross_pnl_gbp == D("100")
    assert estimate.net_pnl_gbp == D("85.70")
    assert estimate.stressed_cost_gbp == D("28.60")
    assert estimate.entry_cash_gbp == D("1009.50")


def test_spread_not_double_counted(context):
    engine, inst = hand_costs(context)
    estimate = engine.estimate(
        inst,
        D("10"),
        D("100"),
        D("110"),
        context.data.fx,
        context.quote.event_time,
        entry_basis=PriceBasis.EXECUTABLE,
        exit_basis=PriceBasis.EXECUTABLE,
    )
    assert dict(estimate.components)["entry_spread"] == 0
    assert dict(estimate.components)["exit_spread"] == 0
    assert estimate.total_cost_gbp == D("12.20")


def test_independent_usd_to_gbp_calculation(context):
    engine, _ = hand_costs(context)
    data = generate("orb", Venue.NASDAQ)
    estimate = engine.estimate(
        data.instrument, D("10"), D("100"), D("110"), data.fx, data.quotes[0].event_time
    )
    # 1000 USD -> 800 GBP, 1100 USD -> 880 GBP at artificial .8 rate.
    # Entry .80+.80+1.80+2.00+4.00+.50=9.90; exit .88+.88+1.88+2.20+.50=6.34.
    assert estimate.entry_notional_gbp == D("800")
    assert estimate.entry_cost_gbp == D("9.90")
    assert estimate.exit_cost_gbp == D("6.34")
    assert estimate.net_pnl_gbp == D("63.76")


def test_unknown_ambiguous_and_effective_dated_fees(context):
    fee = synthetic_costs().schedules[0]
    arguments = (
        context.data.instrument,
        D("1"),
        D("254"),
        D("250"),
        context.data.fx,
        context.quote.event_time,
    )
    for schedules in ((), (fee, fee), (replace(fee, fx_bps=None),)):
        with pytest.raises(AtlasError):
            CostEngine(schedules).estimate(*arguments)
    with pytest.raises(AtlasError):
        CostEngine((fee,)).schedule(context.data.instrument, fee.effective_until)


def test_fx_missing_future_stale_or_wrong_currency(context):
    engine = synthetic_costs()
    for rate in (
        replace(
            context.data.fx,
            event_time=context.quote.event_time + timedelta(seconds=1),
            available_time=context.quote.event_time + timedelta(seconds=1),
        ),
        replace(
            context.data.fx,
            event_time=context.quote.event_time - timedelta(days=2),
            available_time=context.quote.event_time - timedelta(days=2),
        ),
        FxRate(
            Currency.USD,
            D("0.8"),
            context.data.session.open,
            context.data.session.open,
            "synthetic",
        ),
    ):
        with pytest.raises(AtlasError):
            engine.estimate(
                context.data.instrument, D("1"), D("254"), D("250"), rate, context.quote.event_time
            )
    with pytest.raises(AtlasError):
        replace(context.data.fx, gbp_per_unit=D("100"))


@pytest.mark.parametrize("venue", [Venue.LSE, Venue.NYSE, Venue.NASDAQ])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_original_datasets_are_deterministic_and_labelled(venue, scenario):
    data = generate(scenario, venue)
    assert data.checksum == generate(scenario, venue).checksum
    assert "SYNTHETIC" in dict(data.provenance)["label"]
    assert len(data.bars) >= 11
