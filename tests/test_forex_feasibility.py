"""Artificial contract and funding cases; never a historical strategy evaluation."""

from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from atlas.forex.feasibility import (
    ESSENTIAL,
    RIGHTS,
    Evidence,
    InstrumentContract,
    Policy,
    Scenario,
    allocate,
    estimate,
)

D = Decimal


def contract(**updates):
    values = dict(
        identity="ARTIFICIAL_EURUSD_UNITS",
        origin="ARTIFICIAL",
        provider="ARTIFICIAL",
        account_type="ARTIFICIAL_GBP_RETAIL",
        product="ROLLING_FX_CFD",
        pair="EUR_USD",
        quantity_kind="BASE_UNITS",
        minimum_quantity="1",
        quantity_step="1",
        units_per_quantity="1",
        point_increment=".0001",
        margin_rate=".034",
        maximum_retail_leverage="30",
        minimum_stop_points="1",
        commission_per_quantity_side=".00005",
        minimum_commission_side="0",
        commission_currency="GBP",
        conversion_markup="0",
        long_allowed=True,
        short_allowed=True,
        guarantee_available=True,
        guarantee_premium_points="1.2",
        guarantee_minimum_points="3",
        adverse_annual_funding_rate=".05",
        funding_day_basis=365,
    )
    values.update(updates)
    fields = (
        tuple(ESSENTIAL)
        + tuple(RIGHTS)
        + (
            "units_per_quantity",
            "margin_rate",
            "maximum_retail_leverage",
            "guarantee_premium_points",
            "guarantee_minimum_points",
            "adverse_annual_funding_rate",
            "funding_day_basis",
        )
    )
    values.setdefault(
        "evidence",
        tuple(
            Evidence(
                field=f,
                status="VERIFIED_ARTIFICIAL",
                source="artificial://phase6b5",
                scope="Artificial contract only",
            )
            for f in fields
        ),
    )
    return InstrumentContract(**values)


def scenario(**updates):
    return Scenario(
        **(
            dict(
                identity="A",
                quantity="1000",
                reference_price="1.10",
                quote_per_gbp_bid="1.25",
                quote_per_gbp_ask="1.2502",
                stop_points="20",
                spread_points="2",
                entry_slippage_points=".5",
                exit_slippage_points=".5",
                additional_gap_points="50",
                stressed_spread_points="5",
            )
            | updates
        )
    )


def stake_contract(**updates):
    return contract(
        **(
            dict(
                identity="ARTIFICIAL_GBP_STAKE",
                product="FX_SPREAD_BET",
                quantity_kind="GBP_PER_POINT",
                minimum_quantity=".1",
                quantity_step=".1",
                units_per_quantity=None,
                commission_per_quantity_side="0",
            )
            | updates
        )
    )


@pytest.mark.parametrize("side", ["long", "short"])
def test_units_and_lots_normalize_identically(side):
    unit = estimate(contract(), scenario(side=side))
    lot = estimate(
        contract(
            quantity_kind="LOTS",
            minimum_quantity=".01",
            quantity_step=".01",
            units_per_quantity="100000",
            commission_per_quantity_side="5",
        ),
        scenario(quantity=".01", side=side),
    )
    for name in (
        "base_units",
        "point_value_gbp",
        "notional_gbp",
        "initial_margin_gbp",
        "roundtrip_commission_gbp",
        "planned_loss_gbp",
        "stress_loss_gbp",
    ):
        assert unit[name] == lot[name]
    assert unit["notional_gbp"] == 880 and unit["initial_margin_gbp"] == D("29.920")
    assert unit["planned_loss_gbp"] == D("1.94")


@pytest.mark.parametrize(
    "quantity,reason", [(".09", "BELOW_MINIMUM_QUANTITY"), (".15", "QUANTITY_STEP_VIOLATION")]
)
def test_minimum_and_step_are_distinct(quantity, reason):
    r = allocate(((stake_contract(), scenario(quantity=quantity)),), Policy(name="MARGIN_ONLY"))
    assert r["feasible_positions"] == 0 and reason in r["decisions"][0]["allocation_reasons"]


def test_jpy_pip_and_fractional_display_point_are_not_conflated():
    r = estimate(
        stake_contract(pair="USD_JPY", point_increment=".001"),
        scenario(quantity=".1", reference_price="150"),
    )
    assert r["point_value_gbp"] == D(".1") and r["pip_value_gbp"] == 1
    assert r["notional_gbp"] == 15000


def test_gbp_stake_pnl_does_not_get_usd_conversion_again():
    a = estimate(stake_contract(), scenario(quantity=".1"))
    b = estimate(
        stake_contract(),
        scenario(
            quantity=".1",
            quote_per_gbp_bid="200",
            quote_per_gbp_ask="201",
            stress_conversion_multiplier="2",
        ),
    )
    for k in ("point_value_gbp", "planned_loss_gbp", "stress_loss_gbp", "notional_gbp"):
        assert a[k] == b[k]


def test_conversion_markup_and_quote_currency_commission_are_explicit():
    r = estimate(contract(conversion_markup=".01", commission_currency="QUOTE"), scenario())
    assert r["adverse_gbp_conversion_rate"] == D(".808")
    assert r["roundtrip_commission_gbp"] == D(".0808")
    assert r["commission_conversion_markup_gbp"] == D(".0008")
    assert r["conversion_markup_on_price_costs_gbp"] == D(".0184")


def test_guarantee_premium_reserved_once_not_added_to_stress_buffer_twice():
    c = stake_contract()
    s = scenario(quantity=".1", guaranteed=True)
    a = estimate(c, s)
    r = allocate(((c, s),), Policy(name="MARGIN_STRESS"))
    assert a["guarantee_premium_reserved_gbp"] == D(".12")
    assert a["planned_loss_gbp"] == D("2.37") and a["stress_loss_gbp"] == D("2.37")
    assert D(r["reserved_capital_gbp"]) == max(a["initial_margin_gbp"], a["stress_margin_gbp"]) + D(
        "2.37"
    )
    assert not r["executable"]


def test_quote_currency_guarantee_does_not_guarantee_gbp_conversion():
    r = estimate(contract(), scenario(guaranteed=True, stress_conversion_multiplier="2"))
    assert r["stress_loss_gbp"] > r["planned_loss_gbp"]


@pytest.mark.parametrize("name", ["guarantee_premium_points", "guarantee_minimum_points"])
def test_missing_guarantee_terms_fail_closed(name):
    r = allocate(
        ((contract(**{name: None}), scenario(guaranteed=True)),), Policy(name="FULL_NOTIONAL")
    )
    assert (
        r["feasible_positions"] == 0
        and r["decisions"][0]["calculation_status"] == "BLOCKED_MISSING_SPEC"
    )


def test_ordinary_stop_gap_can_exceed_risk_budget_and_whole_account():
    c = stake_contract()
    s = scenario(quantity=".1", side="short", stop_points="5", additional_gap_points="20000")
    r = allocate(((c, s),), Policy(name="MARGIN_ONLY"))
    assert r["feasible_positions"] == 1 and D(r["planned_risk_gbp"]) < 5
    assert D(r["stress_loss_gbp"]) > 1000 and D(r["post_stress_equity_gbp"]) < 0
    assert r["account_closeout_threshold_breached"]
    assert not r["decisions"][0]["ordinary_stop_is_loss_guarantee"]


def test_stress_buffer_policy_rejects_tail_that_margin_only_can_admit():
    c = stake_contract()
    s = scenario(quantity=".1", side="short", stop_points="5", additional_gap_points="20000")
    r = allocate(((c, s),), Policy(name="MARGIN_STRESS"))
    assert r["feasible_positions"] == 0
    assert "INSUFFICIENT_UNENCUMBERED_CAPITAL" in r["decisions"][0]["allocation_reasons"]


def test_risk_based_size_cannot_allocate_almost_whole_equity_to_gap_stress():
    c = stake_contract()
    s = scenario(quantity=".5", side="short", stop_points="5", additional_gap_points="20000")
    r = allocate(((c, s),), Policy(name="RISK_FUNDED"))
    assert D(r["stress_loss_gbp"]) <= 100
    assert not r["stress_budget_exceeded"]


def test_multiple_ids_do_not_bypass_single_position_per_pair():
    c = contract()
    r = allocate(
        ((c, scenario(identity="A")), (c, scenario(identity="B"))), Policy(name="MARGIN_ONLY")
    )
    assert r["feasible_positions"] == 1
    assert "PAIR_POSITION_LIMIT" in r["decisions"][1]["allocation_reasons"]


def test_margin_increase_is_stressed_without_inventing_extra_cash():
    c = contract()
    s = scenario(stressed_margin_rate=".1")
    e = estimate(c, s)
    r = allocate(((c, s),), Policy(name="MARGIN_STRESS"))
    assert e["stress_margin_gbp"] > e["initial_margin_gbp"]
    assert D(r["reserved_capital_gbp"]) == e["stress_margin_gbp"] + e["stress_loss_gbp"]


def test_stressed_margin_cannot_be_lowered_to_create_capacity():
    r = allocate(((contract(), scenario(stressed_margin_rate=".01")),), Policy(name="MARGIN_ONLY"))
    assert r["feasible_positions"] == 0


def test_full_notional_funding_is_separate_from_small_stop_risk():
    c = stake_contract()
    s = scenario(quantity=".5", stop_points="5", spread_points=".6")
    full = allocate(((c, s),), Policy(name="FULL_NOTIONAL"))
    margin = allocate(((c, s),), Policy(name="MARGIN_ONLY"))
    assert full["feasible_positions"] == 0 and margin["feasible_positions"] == 1
    assert D(margin["planned_risk_gbp"]) == D("3.30")


def test_risk_sizing_remains_bounded_by_funding_and_fixed_initial_budget():
    r = allocate(((contract(), scenario(quantity="100000")),), Policy(name="RISK_FUNDED"))
    assert r["feasible_positions"] == 1 and D(r["reserved_capital_gbp"]) <= 1000
    assert D(r["planned_risk_gbp"]) <= 5 and D(r["decisions"][0]["quantity"]) < 100000
    assert D(r["decisions"][0]["quantity"]) % 1 == 0


@pytest.mark.parametrize("count", [1, 2, 3])
def test_multiple_positions_do_not_spend_each_others_reserved_cash(count):
    requests = tuple(
        (contract(), scenario(identity=chr(65 + i), quantity="100000")) for i in range(count)
    )
    r = allocate(requests, Policy(name="RISK_FUNDED"))
    assert D(r["available_capital_gbp"]) >= 0 and D(r["planned_risk_gbp"]) <= 10
    assert sum(
        D(d["capital_required_gbp"]) for d in r["decisions"] if d["admitted_to_artificial_book"]
    ) == D(r["reserved_capital_gbp"])
    assert r == allocate(tuple(reversed(requests)), Policy(name="RISK_FUNDED"))


@pytest.mark.parametrize(
    "field", ESSENTIAL + ("units_per_quantity", "margin_rate", "maximum_retail_leverage")
)
def test_essential_missing_specs_are_not_filled(field):
    r = allocate(((contract(**{field: None}), scenario()),), Policy(name="FULL_NOTIONAL"))
    assert r["feasible_positions"] == 0 and field in r["decisions"][0]["missing_fields"]


def test_unverified_margin_cannot_enable_margin_based_reservation():
    c = contract()
    c = c.model_copy(update={"evidence": tuple(e for e in c.evidence if e.field != "margin_rate")})
    r = allocate(((c, scenario()),), Policy(name="MARGIN_ONLY"))
    assert r["feasible_positions"] == 0
    assert "MARGIN_SPECIFICATION_NOT_VERIFIED" in r["decisions"][0]["allocation_reasons"]


def test_marketing_or_inferred_values_never_become_executable_contracts():
    c = contract()
    e = tuple(Evidence(**(v.model_dump() | {"status": "ILLUSTRATIVE"})) for v in c.evidence)
    public = InstrumentContract(
        **(c.model_dump() | {"origin": "PUBLIC_DOCUMENTATION", "evidence": e})
    )
    r = allocate(((public, scenario()),), Policy(name="FULL_NOTIONAL"))
    assert not r["executable"] and not r["decisions"][0]["executable"]
    assert r["decisions"][0]["calculation_status"] == "ILLUSTRATIVE_ONLY"


def test_overnight_charge_is_calculated_but_position_is_not_admitted():
    c = contract()
    s = scenario(overnight_days=3)
    e = estimate(c, s)
    with localcontext() as ctx:
        ctx.prec = 40
        assert e["financing_gbp"] == D(880) * D(".05") * 3 / 365
    r = allocate(((c, s),), Policy(name="MARGIN_ONLY"))
    assert (
        r["feasible_positions"] == 0
        and "OVERNIGHT_POSITION_FORBIDDEN" in r["decisions"][0]["allocation_reasons"]
    )


def test_deliverable_spot_cannot_imply_borrowed_short_currency():
    r = allocate(
        ((contract(product="DELIVERABLE_SPOT"), scenario(side="short")),),
        Policy(name="FULL_NOTIONAL"),
    )
    assert r["feasible_positions"] == 0
    assert "DELIVERABLE_CURRENCY_INVENTORY_NOT_MODELED" in r["decisions"][0]["allocation_reasons"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "NaN"),
        ("reference_price", "Infinity"),
        ("quote_per_gbp_bid", "0"),
        ("stress_conversion_multiplier", ".9"),
    ],
)
def test_nonfinite_or_optimistic_stress_values_rejected(field, value):
    with pytest.raises(ValidationError):
        scenario(**{field: value})


@pytest.mark.parametrize(
    "changes",
    [{"trade_risk_gbp": "6"}, {"initial_capital_gbp": "1001"}, {"aggregate_risk_gbp": "11"}],
)
def test_profit_cannot_ratchet_fixed_budgets(changes):
    with pytest.raises(ValidationError):
        Policy(name="RISK_FUNDED", **changes)


def test_duplicate_request_identity_is_not_netted_or_silently_dropped():
    with pytest.raises(ValueError, match="Duplicate"):
        allocate(((contract(), scenario()), (contract(), scenario())), Policy(name="MARGIN_ONLY"))


def test_determinism_is_independent_of_callers_decimal_context():
    from decimal import ROUND_UP

    c, s = (
        contract(),
        scenario(quantity="100000", quote_per_gbp_bid="1.3", quote_per_gbp_ask="1.31"),
    )
    p = Policy(name="RISK_FUNDED")
    expected = allocate(((c, s),), p)
    with localcontext() as ctx:
        ctx.prec = 8
        ctx.rounding = ROUND_UP
        ctx.capitals = 0
        assert allocate(((c, s),), p) == expected


@pytest.mark.parametrize("field", ["adverse_annual_funding_rate", "funding_day_basis"])
def test_missing_overnight_cost_is_unknown_not_free(field):
    r = estimate(contract(**{field: None}), scenario(overnight_days=1))
    assert r["calculation_status"] == "BLOCKED_MISSING_SPEC"
    assert field in r["missing_fields"] and "financing_gbp" not in r


def test_fca_leverage_ceiling_is_independent_of_rounded_margin_percentage():
    r = estimate(contract(margin_rate=".0333"), scenario())
    with localcontext() as ctx:
        ctx.prec = 40
        assert r["initial_margin_gbp"] >= r["notional_gbp"] / 30


def test_no_historical_or_external_side_effect_imports():
    import ast
    from pathlib import Path

    file = Path(__file__).parents[1] / "src/atlas/forex/feasibility.py"
    tree = ast.parse(file.read_text())
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert imports == ["decimal", "typing", "pydantic", "atlas.market_data.contracts"]
