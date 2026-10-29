"""Separate adversarial acceptance pass: attempt favorable fills and unsafe lifecycle states."""

import json
import socket
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, DefaultContext
from decimal import Decimal as D

import pytest
from pydantic import ValidationError
from test_forex_execution import OPEN, execute, frame, order, settings, spec

from atlas.config import AtlasError
from atlas.forex.execution.calendar import window
from atlas.forex.execution.contracts import MINUTE, Annotation, Frame, Overlay
from atlas.forex.execution.market import conversion


@pytest.mark.parametrize("side", ["long", "short"])
def test_stressed_quote_triggers_stop_that_unstressed_prices_miss(side):
    # Raw bid low remains above the long stop; stressed bid low breaches it.
    # For shorts the raw ask high is below the stop, but stressed ask breaches it.
    intent = order(
        side=side,
        stop=D("1.09985") if side == "long" else D("1.10035"),
        target=D("1.2") if side == "long" else D("1.0"),
    )
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, {"EUR_USD": {"low": "1.0999", "high": "1.1001"}}),
    ]
    result = execute(
        frames, orders=(intent,), config=settings(costs={"additional_spread_pips": D(2)})
    )
    assert result.trades[0].reason == "FX_STOP"
    assert result.trades[0].net_gbp < 0


@pytest.mark.parametrize("side", ["long", "short"])
def test_stressed_target_cannot_take_profit_on_unstressed_extreme(side):
    # Quote stress makes an otherwise touched target unreachable.
    intent = order(
        side=side,
        stop=D("1.09") if side == "long" else D("1.11"),
        target=D("1.10035") if side == "long" else D("1.09985"),
    )
    changes = {"EUR_USD": {"high": "1.1004", "low": "1.0996"}}
    frames = [frame(OPEN), frame(OPEN + MINUTE), frame(OPEN + 2 * MINUTE, changes)]
    result = execute(frames, (intent,), settings(costs={"additional_spread_pips": D(2)}))
    assert result.trades[0].reason == "FX_MAX_HOLDING"


def test_unrealized_loss_cannot_fund_new_notional():
    second = order(
        identity="order2",
        instrument="GBP_USD",
        units=D(559),
        stop=D("1.24"),
        target=D("1.26"),
        signal_at=OPEN + 2 * MINUTE,
        information_cutoff=OPEN + 2 * MINUTE,
    )
    first = order(units=D(500), stop=D("1.095"))
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, {"EUR_USD": {"bid": "1.096", "ask": "1.0962"}}),
        frame(OPEN + 3 * MINUTE),
    ]
    result = execute(frames, (first, second), settings(max_holding_minutes=2, max_positions=2))
    assert len(result.trades) == 1
    assert any(
        e.identity == "order2" and e.reason == "FX_CAPITAL_EXPOSURE_OR_RISK_LIMIT"
        for e in result.events
    )


def test_daily_loss_applies_to_marked_equity_before_more_entries():
    second = order(
        identity="order2",
        instrument="GBP_USD",
        stop=D("1.24"),
        target=D("1.26"),
        signal_at=OPEN + 2 * MINUTE,
        information_cutoff=OPEN + 2 * MINUTE,
    )
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, {"EUR_USD": {"bid": "1.096", "ask": "1.0962"}}),
        frame(OPEN + 3 * MINUTE),
    ]
    result = execute(
        frames,
        (order(units=D(500), stop=D("1.095")), second),
        settings(max_holding_minutes=5, max_positions=2, daily_loss_limit_gbp=D(1)),
    )
    assert result.trades[0].reason == "FX_DAILY_EQUITY_LOSS_EXIT"
    assert len(result.trades) == 1


def test_price_drift_cannot_silently_breach_notional_limit():
    intent = order(units=D(900), stop=D("1.0995"), target=D("1.4"))
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, {"EUR_USD": {"bid": "1.2", "ask": "1.2002"}}),
    ]
    result = execute(frames, (intent,), settings(max_exposure_gbp=D(800), max_holding_minutes=5))
    assert result.trades[0].reason == "FX_EXPOSURE_LIMIT_EXIT"


@pytest.mark.parametrize("session", ["LONDON", "NEW_YORK", "OVERLAP"])
def test_mandatory_flat_and_last_entry_boundary(session):
    cfg, run = settings(max_holding_minutes=240), spec(session=session)
    w = window(run, cfg)
    signal = w.last_signal_at
    intent = order(signal_at=signal, information_cutoff=signal, evidence_start=signal - MINUTE)
    frames = [frame(signal - MINUTE), frame(signal), frame(signal + MINUTE)]
    result = execute(frames, (intent,), cfg, run)
    assert result.trades[0].exited_at == w.flatten_at
    assert result.trades[0].reason == "FX_MANDATORY_FLAT"
    assert not result.unresolved_positions
    if session == "NEW_YORK":
        assert w.flatten_at == datetime(2025, 2, 10, 21, 45, tzinfo=UTC)


def test_unexecutable_last_flat_is_invalid_not_a_favorable_terminal_liquidation():
    cfg, run = settings(max_holding_minutes=240), spec()
    signal = window(run, cfg).last_signal_at
    intent = order(signal_at=signal, information_cutoff=signal, evidence_start=signal - MINUTE)
    result = execute([frame(signal - MINUTE), frame(signal)], (intent,), cfg, run)
    assert result.net_pnl_gbp is None and len(result.unresolved_positions) == 1
    assert not result.trades


def test_session_cannot_be_carried_into_a_second_day():
    with pytest.raises(AtlasError, match="SCOPE"):
        execute([frame(OPEN), frame(OPEN + MINUTE), frame(OPEN + timedelta(days=1))])


@pytest.mark.parametrize("offset", [-1, 1])
def test_future_or_stale_conversion_is_rejected(offset):
    available = {o.instrument: o for o in frame(OPEN).observations}
    with pytest.raises(AtlasError, match="CURRENT_COMPLETE_CLOSE"):
        conversion("USD", D(1), available, at=OPEN + MINUTE + offset * MINUTE)


def test_usd_profit_and_loss_use_opposite_conversion_sides():
    available = {o.instrument: o for o in frame(OPEN).observations}
    income = conversion("USD", D(1), available, at=OPEN + MINUTE)
    expense = conversion("USD", D(-1), available, at=OPEN + MINUTE)
    assert income < expense


def test_gap_classifications_remain_in_diagnostic_trace():
    overlay = Overlay(
        annotations=(
            Annotation(
                instrument="EUR_USD",
                start=OPEN + 2 * MINUTE,
                end=OPEN + 3 * MINUTE,
                reason="HOLIDAY_CLOSURE_SUPPORTED_NOT_ACCOUNT_VERIFIED",
                evidence="phase6b1c-v2",
            ),
        )
    )
    result = execute(overlay=overlay)
    assert result.net_pnl_gbp is None
    assert any(e.reason == "HOLIDAY_CLOSURE_SUPPORTED_NOT_ACCOUNT_VERIFIED" for e in result.events)


def test_future_signal_evidence_and_nan_costs_are_rejected():
    with pytest.raises(ValidationError):
        order(information_cutoff=OPEN + 3 * MINUTE)
    with pytest.raises(ValidationError):
        settings(costs={"slippage_pips": D("NaN")})


def test_wider_costs_do_not_improve_same_price_roundtrip():
    base = execute().net_pnl_gbp
    for spread_stress in (D(".1"), D(1), D(2)):
        result = execute(config=settings(costs={"additional_spread_pips": spread_stress}))
        assert result.net_pnl_gbp < base


def test_frame_order_and_overlay_order_are_canonical():
    frames = tuple(frame(OPEN + i * MINUTE) for i in range(3))
    reversed_frames = tuple(
        Frame(start=f.start, observations=tuple(reversed(f.observations))) for f in frames
    )
    assert execute(frames) == execute(reversed_frames)


def test_execution_does_not_read_credentials_or_construct_external_clients(monkeypatch):
    import atlas.forex.config as config
    import atlas.forex.provider as provider

    def forbidden(*args, **kwargs):
        raise AssertionError("External side effect attempted")

    monkeypatch.setattr(config, "load_config", forbidden)
    monkeypatch.setattr(provider, "OandaReader", forbidden)
    monkeypatch.setattr(provider, "ReadOnlyTransport", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert execute().trades


def test_economic_input_and_provenance_changes_bind_result_hash():
    base = execute()
    changed = execute(config=settings(costs={"slippage_pips": D(".1")}))
    versioned = execute(run=spec(code_version="different-version"))
    assert len({base.checksum, changed.checksum, versioned.checksum}) == 3


def test_no_favorable_target_recovery_after_an_unresolved_gap():
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, absent=("EUR_USD",)),
        frame(OPEN + 3 * MINUTE, {"EUR_USD": {"bid": "1.2", "ask": "1.2002"}}),
    ]
    result = execute(frames)
    assert not result.trades and result.net_pnl_gbp is None
    stress = [json.loads(e.details) for e in result.events if e.kind == "RESUMPTION_STRESS_ONLY"]
    assert stress and stress[0]["unknown_path"]


def test_recurring_conversion_fractions_reconcile_exactly_across_positions():
    second = order(identity="order2", instrument="GBP_USD", stop=D("1.24"), target=D("1.26"))
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(
            OPEN + 2 * MINUTE,
            {
                "EUR_USD": {"bid": "1.101", "ask": "1.1012"},
                "GBP_USD": {"bid": "1.251", "ask": "1.2512"},
            },
        ),
    ]
    result = execute(
        frames,
        (order(), second),
        settings(max_positions=2, costs={"commission_gbp_per_unit_per_side": D(".0012")}),
    )
    assert len(result.trades) == 2
    assert result.cash_gbp == D(1000) + sum(t.net_gbp for t in result.trades)
    assert all(t.gross_gbp.as_tuple().exponent == -8 for t in result.trades)


@pytest.mark.parametrize("side,stop", [("long", "1.1001"), ("short", "1.1001")])
def test_stop_inside_entry_spread_cannot_create_artificially_low_risk(side, stop):
    intent = order(side=side, stop=D(stop), target=D("1.11") if side == "long" else D("1.09"))
    result = execute(orders=(intent,))
    assert not result.trades
    assert any(e.reason == "FX_STOP_ALREADY_BREACHED_AT_ENTRY" for e in result.events)


@pytest.mark.parametrize("name,base", [("EUR_USD", "1.1"), ("GBP_USD", "1.25"), ("USD_JPY", "150")])
@pytest.mark.parametrize("side", ["long", "short"])
def test_every_supported_pair_and_direction_has_adverse_flat_roundtrip(name, base, side):
    price = D(base)
    distance = D(1) if name == "USD_JPY" else D(".01")
    intent = order(
        instrument=name,
        side=side,
        stop=price - distance if side == "long" else price + distance,
        target=price + distance if side == "long" else price - distance,
    )
    result = execute(orders=(intent,))
    assert len(result.trades) == 1 and result.trades[0].net_gbp < 0


def test_concurrent_planned_risk_is_enforced_separately_from_position_count():
    intents = (
        order(units=D(500)),
        order(
            identity="order2", instrument="GBP_USD", units=D(500), stop=D("1.24"), target=D("1.26")
        ),
    )
    result = execute(orders=intents, config=settings(max_positions=2, max_concurrent_risk_gbp=D(5)))
    assert len(result.trades) == 1


def test_decimal_default_context_cannot_change_fills_or_hashes():
    baseline = execute()
    rounding = DefaultContext.rounding
    try:
        DefaultContext.rounding = ROUND_DOWN
        assert execute() == baseline
    finally:
        DefaultContext.rounding = rounding


def test_result_contains_reproducible_configuration_and_overlay_evidence():
    result = execute()
    config = json.loads(result.configuration)
    assert config["settings"]["version"] == "FOREX_M1_CONSERVATIVE_REPLAY_V1"
    assert config["overlay"]["version"] == Overlay().version
    assert config["gbp_settlement"] == "FLOOR_PNL_CEILING_FEES_1E_MINUS_8_V1"


def test_incomplete_candle_during_exposure_invalidates_even_if_target_looks_hit():
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, {"EUR_USD": {"complete": False, "high": "1.2"}}),
    ]
    result = execute(frames)
    assert result.net_pnl_gbp is None and not result.trades
