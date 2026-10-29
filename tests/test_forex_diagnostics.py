"""Independent arithmetic cases; no historical execution or provider access."""

import ast
import copy
from decimal import ROUND_FLOOR, Decimal, localcontext
from pathlib import Path

import pytest

from atlas.forex.diagnostics import CloseQuote, attribution, spread_bet, unleveraged

D = Decimal
GBP = D(".00000001")


@pytest.mark.parametrize("pair", ["EUR_USD", "GBP_USD", "USD_JPY"])
@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("outcome", [1, -1])
def test_recorded_component_bridge_reconciles_both_sides_and_gbp_conversion(pair, side, outcome):
    with localcontext() as ctx:
        ctx.prec = 34
        quote = D(outcome) * 2
        gbp = CloseQuote(D("1.25"), D("1.2502"))
        jpy = CloseQuote(D(150), D("150.02")) if pair == "USD_JPY" else None
        divisor = gbp.ask if outcome > 0 else gbp.bid
        if jpy:
            divisor *= jpy.ask if outcome > 0 else jpy.bid
        rate = 1 / divisor
        settled = (quote * rate).quantize(GBP, rounding=ROUND_FLOOR)
        direction = 1 if side == "long" else -1
        trade = {
            "position": {
                "entry": "1.10",
                "intent": {"instrument": pair, "units": "1000", "side": side},
                "entry_fee_gbp": "0.05",
            },
            "exit_price": str(D("1.10") + D(direction) * quote / 1000),
            "gross_quote": str(quote),
            "conversion_rate": str(rate),
            "gross_gbp": str(settled),
            "exit_fee_gbp": "0.05",
            "net_gbp": str(settled - D(".1")),
        }
        q = CloseQuote(D("1.1"), D("1.1002"))
        result = attribution(
            trade, {"spread_multiplier": "1.5", "slippage_pips": "0.5"}, q, q, gbp, jpy
        )
        costs = sum(
            (
                v
                for k, v in result.items()
                if k not in ("gross_reference_gbp", "recorded_price_pnl_gbp", "recorded_net_gbp")
            ),
            D(0),
        )
        assert abs(result["gross_reference_gbp"] - costs - result["recorded_net_gbp"]) < D("1e-28")
        assert result["directional_conversion_gbp"] >= 0
        assert abs(result["settlement_rounding_gbp"]) <= D("1e-8")
        assert result["assumed_slippage_gbp"] > 0
        altered = copy.deepcopy(trade)
        altered["net_gbp"] = "123"
        with pytest.raises(ValueError, match="settlement/commission"):
            attribution(altered, {"spread_multiplier": "1", "slippage_pips": "0.5"}, q, q, gbp, jpy)


@pytest.mark.parametrize("bid,ask", [("0", "1"), ("2", "1"), ("NaN", "1"), ("1", "Infinity")])
def test_invalid_quotes_are_not_replaced_with_zero_costs(bid, ask):
    with pytest.raises(ValueError):
        CloseQuote(D(bid), D(ask))


def test_point_margin_and_planned_risk_are_distinct():
    r = spread_bet(
        stake=D(".5"),
        price=D("1.1"),
        point=D(".0001"),
        margin_rate=D(1) / 30,
        stop_points=D(20),
        spread_points=D(".6"),
        slippage_per_side=D(".5"),
    )
    assert r["notional_equivalent_gbp"] == 5500
    assert abs(r["required_margin_gbp"] - D(5500) / 30) < D("1e-23")
    assert r["planned_loss_gbp"] == D("10.8") and not r["risk_feasible"]
    assert r["max_mid_reference_stop_points"] == D("8.4")
    assert r["spread_fraction_of_risk"] == D(".06")
    assert r["loss_with_additional_gap_gbp"] == D("35.8")


def test_jpy_point_is_a_pip_not_a_fractional_display_tick():
    r = spread_bet(
        stake=D(".3"),
        price=D(150),
        point=D(".01"),
        margin_rate=D(".0334"),
        stop_points=D(15),
        spread_points=D(".7"),
        slippage_per_side=D(".5"),
    )
    assert r["required_margin_gbp"] == D("150.3000")
    assert r["planned_loss_gbp"] == D("5.01") and not r["risk_feasible"]


def test_guarantee_includes_premium_but_does_not_claim_availability():
    r = spread_bet(
        stake=D(1),
        price=D("1.3"),
        point=D(".0001"),
        margin_rate=D(".0334"),
        stop_points=D(3),
        spread_points=D(".9"),
        slippage_per_side=D(".5"),
        guaranteed_premium_points=D(2),
        gap_points=D(2000),
    )
    assert r["planned_loss_gbp"] == D("4.9")
    assert r["conditional_guaranteed_loss_gbp"] == D("6.4")
    assert (
        r["loss_with_additional_gap_gbp"] > 1000
    )  # do not silently cap market loss at risk/margin


@pytest.mark.parametrize("bad", ["NaN", "-1", "0", "Infinity"])
def test_invalid_capital_specifications_rejected(bad):
    with pytest.raises(ValueError):
        spread_bet(
            stake=D(bad),
            price=D(1),
            point=D(".0001"),
            margin_rate=D(".05"),
            stop_points=D(20),
            spread_points=D(1),
            slippage_per_side=D(1),
        )


def test_units_and_lot_minimums_do_not_create_borrowed_capital():
    args = dict(
        price=D("1.3"),
        quote_per_gbp=D("1.3"),
        pip=D(".0001"),
        stop_pips=D(20),
        spread_pips=D(2),
        slip_per_side=D(".5"),
    )
    native = unleveraged(**args, minimum_units=D(1))
    lots = unleveraged(**args, minimum_units=D(1000))
    assert native["whole_units"] < 1000 and native["feasible"] and not lots["feasible"]
    assert native["collateral_gbp"] + native["roundtrip_cost_gbp"] <= 1000
    assert native["planned_loss_gbp"] <= 5


def test_diagnostics_do_not_import_execution_or_strategy_runners():
    root = Path(__file__).resolve().parents[1]
    for path in (
        root / "src/atlas/forex/diagnostics.py",
        root / "scripts/forex_profitability_diagnostics.py",
    ):
        tree = ast.parse(path.read_text())
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(
            m and (".execution" in m or ".research" in m or ".provider" in m) for m in modules
        )


def test_capital_calculations_are_deterministic_and_inputs_unchanged():
    args = dict(
        stake=D(".3"),
        price=D("1.1"),
        point=D(".0001"),
        margin_rate=D(".0334"),
        stop_points=D(15),
        spread_points=D(".6"),
        slippage_per_side=D(".5"),
    )
    saved = copy.deepcopy(args)
    assert spread_bet(**args) == spread_bet(**args) and args == saved


def test_lot_step_is_enforced_in_addition_to_minimum_size():
    r = unleveraged(
        price=D("1.1"),
        quote_per_gbp=D("1.3"),
        pip=D(".0001"),
        minimum_units=D(1000),
        unit_step=D(1000),
        stop_pips=D(20),
        spread_pips=D(2),
        slip_per_side=D(".5"),
    )
    assert r["whole_units"] == 1000 and r["feasible"]
    assert r["whole_units"] % r["unit_step"] == 0


def test_guaranteed_premium_reservation_cannot_be_spent_as_free_cash():
    r = spread_bet(
        stake=D(1),
        price=D(1),
        point=D(".001"),
        margin_rate=D(".99"),
        stop_points=D(1),
        spread_points=D(1),
        slippage_per_side=D(".5"),
        guaranteed_premium_points=D(10),
    )
    assert r["conditional_guaranteed_premium_reserve_gbp"] == 10
    assert not r["margin_feasible_with_cost_buffer"]
