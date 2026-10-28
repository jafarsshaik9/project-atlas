"""Observation precision is separate from executable price increments."""

from dataclasses import replace
from decimal import Decimal

import pytest

from atlas.config import AtlasError
from atlas.domain import DecisionStatus, TradeIntent, Venue
from atlas.price_precision_diagnostics import precision_probe
from atlas.strategy import ORBStrategy
from atlas.synthetic import generate

D = Decimal


@pytest.mark.parametrize("fraction", [".005", ".001", ".0001"])
def test_subtick_observations_preserved_and_risk_approved(context, fraction):
    bars = tuple(
        replace(b, **{k: getattr(b, k) + D(fraction) for k in ("open", "high", "low", "close")})
        for b in context.bars
    )
    signal = (
        ORBStrategy()
        .evaluate(context.data.instrument, bars, context.data.session, bars[-1].visible_time)
        .signal
    )
    assert signal is not None
    assert signal.entry == bars[-1].close
    assert not context.data.instrument.valid_price(signal.entry)
    assert context.data.instrument.valid_price(signal.stop)
    assert context.data.instrument.valid_price(signal.target)
    result = context.risk.evaluate(
        TradeIntent(signal, D("1")),
        context.data.instrument,
        context.quote,
        bars,
        context.data.session,
        context.data.fx,
        context.quote.event_time,
    )
    assert result.status == DecisionStatus.APPROVED


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", "0"])
def test_invalid_observation_price_still_rejected(context, price):
    with pytest.raises(AtlasError):
        replace(context.bars[0], close=D(price))


def test_impossible_subtick_ohlc_still_rejected(context):
    with pytest.raises(AtlasError):
        replace(context.bars[0], close=context.bars[0].high + D(".005"))


def test_subtick_executable_quote_still_rejected(context):
    quote = replace(context.quote, ask=context.quote.ask + D(".005"))
    result = context.risk.evaluate(
        TradeIntent(context.signal, D("1")),
        context.data.instrument,
        quote,
        context.bars,
        context.data.session,
        context.data.fx,
        quote.event_time,
    )
    assert result.status == DecisionStatus.REJECTED
    assert "INVALID_TICK" in result.reasons


def test_subtick_observations_do_not_bypass_volume_filter(context):
    bars = (replace(context.bars[0], volume=D("99")), *context.bars[1:])
    result = ORBStrategy().evaluate(
        context.data.instrument, bars, context.data.session, bars[-1].visible_time
    )
    assert result.reasons == ("STRATEGY_DATA_QUALITY",)


def test_us_breakout_comparison_uses_unrounded_reported_price():
    data = generate(venue=Venue.NASDAQ)
    assert data.instrument.tick_size == D(".01")
    opening = data.bars[:3]
    price = max(b.high for b in opening) + D(".001")
    trigger = replace(data.bars[3], open=price, high=price, close=price)
    signal = (
        ORBStrategy()
        .evaluate(data.instrument, (*opening, trigger), data.session, trigger.visible_time)
        .signal
    )
    assert signal is not None and signal.entry == price


def test_paired_precision_probe_counts_gate_and_signal_separately(context):
    bars = tuple(replace(b, low=b.low - D(".005")) for b in context.bars)
    result = precision_probe(context.data.instrument, bars, context.data.session)
    assert result["evaluated_candles"] == result["off_grid_candles"] == 4
    assert result["tick_gate_rejections"] == result["sole_tick_rejections"] == 4
    assert result["opening_off_grid"]
    assert result["additional_signals"] == 1
    assert result["legacy_reasons"] == {"STRATEGY_DATA_QUALITY": 4}
    assert result["corrected_reasons"] == {"OPENING_RANGE_FORMING": 3, "SIGNAL": 1}


def test_missing_bars_preempt_precision_gate_and_invalidate_opening(context):
    bars = tuple(replace(b, low=b.low - D(".005")) for b in context.bars[1:])
    result = precision_probe(context.data.instrument, bars, context.data.session)
    assert not result["opening_complete"]
    assert not result["opening_off_grid"]
    assert result["tick_gate_rejections"] == result["additional_signals"] == 0
    assert result["legacy_reasons"] == result["corrected_reasons"] == {"MISSING_BARS": 3}


def test_precision_probe_does_not_use_future_subtick_observation(context):
    original = precision_probe(context.data.instrument, context.bars, context.data.session)
    bars = (*context.bars[:3], replace(context.bars[3], low=context.bars[3].low - D(".005")))
    changed = precision_probe(context.data.instrument, bars, context.data.session)
    assert original["transitions"][:3] == changed["transitions"][:3]
    assert changed["tick_gate_rejections"] == changed["additional_signals"] == 1
