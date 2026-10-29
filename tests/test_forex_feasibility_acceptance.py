"""Separate adversarial arithmetic checks. Inputs and expected amounts are artificial."""

import ast
import importlib.util
import json
import socket
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_forex_feasibility import contract, scenario, stake_contract

from atlas.forex.feasibility import Evidence, InstrumentContract, Policy, allocate, estimate
from atlas.market_data.contracts import digest

D = Decimal
ROOT = Path(__file__).resolve().parents[1]


def test_independent_stake_cash_equity_and_margin_bridge():
    s = scenario(quantity=".5", stop_points="5", spread_points=".6")
    r = allocate(((stake_contract(), s),), Policy(name="MARGIN_STRESS"))
    assert r["notional_exposure_gbp"] == "5500"
    assert D(r["initial_margin_gbp"]) == 187
    assert D(r["planned_risk_gbp"]) == D("3.3")
    assert D(r["reserved_capital_gbp"]) == D("217.5")
    assert D(r["available_capital_gbp"]) == D("782.5")
    assert D(r["post_stress_equity_gbp"]) == D("969.5")
    assert D(r["post_stress_margin_gbp"]) == D("186.065")
    assert D(r["stress_free_margin_gbp"]) == D("783.435")
    # Encumbered margin is still equity: it must not be subtracted from equity twice.
    assert r["legacy_notional_cap_breached_in_stress"]


def test_minimum_fee_cannot_be_rounded_away_by_tiny_size():
    r = allocate(
        ((contract(minimum_commission_side="3"), scenario(quantity="100000")),),
        Policy(name="RISK_FUNDED"),
    )
    assert r["feasible_positions"] == 0
    assert "NO_FUNDED_MINIMUM_WITHIN_RISK_BUDGET" in r["decisions"][0]["allocation_reasons"]


def test_normalization_rejects_mislabeled_lots_and_stakes():
    with pytest.raises(ValidationError):
        contract(units_per_quantity="100000")
    with pytest.raises(ValidationError):
        contract(quantity_kind="GBP_PER_POINT")
    with pytest.raises(ValidationError):
        stake_contract(units_per_quantity="1")


def test_false_public_verification_cannot_be_supplied_as_artificial_proof():
    c = contract()
    with pytest.raises(ValidationError):
        InstrumentContract(**(c.model_dump() | {"origin": "PUBLIC_DOCUMENTATION"}))


def test_duplicate_evidence_cannot_override_unknown_terms():
    c = contract()
    with pytest.raises(ValidationError):
        contract(
            evidence=c.evidence
            + (
                Evidence(
                    field="margin_rate",
                    status="UNRESOLVED",
                    source="artificial://other",
                    scope="Conflicting",
                ),
            )
        )


def test_contract_input_is_frozen_and_outputs_have_no_shared_state():
    c, s = contract(), scenario()
    p = Policy(name="MARGIN_STRESS")
    with pytest.raises(ValidationError):
        c.margin_rate = D(".01")
    a = allocate(((c, s),), p)
    original = allocate(((c, s),), p)
    a["decisions"][0]["executable"] = True
    assert allocate(((c, s),), p) == original


def test_artificial_matrix_never_opens_network_or_database(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("External/database side effect")

    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(sqlite3, "connect", fail)
    path = ROOT / "scripts/forex_capital_feasibility.py"
    spec = importlib.util.spec_from_file_location("capital_fixture_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = json.loads((ROOT / "examples/forex-capital-phase6b5.json").read_text())
    a, b = module.run(fixture), module.run(fixture)
    assert a == b and len(a["cases"]) == 192
    assert a["checksum"] == digest({k: v for k, v in a.items() if k != "checksum"})
    for result in a["cases"]:
        assert not result["executable"] and D(result["available_capital_gbp"]) >= 0
        assert D(result["planned_risk_gbp"]) <= 10
        if result["policy"]["name"] in ("MARGIN_STRESS", "RISK_FUNDED"):
            assert D(result["stress_loss_gbp"]) <= 100
        for row in result["decisions"]:
            assert not row["executable"]
            if row["admitted_to_artificial_book"]:
                assert D(row["planned_loss_gbp"]) <= 5
            if row["calculation_status"] == "BLOCKED_MISSING_SPEC":
                assert not row["admitted_to_artificial_book"]


def test_runner_has_no_hidden_historical_or_provider_imports():
    tree = ast.parse((ROOT / "scripts/forex_capital_feasibility.py").read_text())
    modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert modules == ["pathlib", "atlas.forex.feasibility", "atlas.market_data.contracts"]


def test_guarantee_not_promoted_to_unconditional_maximum_realized_loss():
    a = estimate(contract(), scenario(guaranteed=True, stress_conversion_multiplier="1.5"))
    assert a["guarantee_conditional_on_contract"]
    assert a["stress_loss_gbp"] > a["planned_loss_gbp"]
    assert a["maximum_realized_loss_gbp"] is None


def test_gbp_base_principal_is_not_reconverted_through_its_own_quote():
    c = contract(pair="GBP_USD")
    s = scenario(
        reference_price="1.3",
        quote_per_gbp_bid="1.2999",
        quote_per_gbp_ask="1.3001",
        stress_conversion_multiplier="2",
    )
    e = estimate(c, s)
    assert e["notional_gbp"] == 1000 and e["stress_notional_gbp"] == 1000
    assert e["initial_margin_gbp"] == D(34)


def test_inconsistent_gbp_underlying_and_conversion_is_not_accepted():
    r = allocate(((contract(pair="GBP_USD"), scenario()),), Policy(name="MARGIN_ONLY"))
    assert r["feasible_positions"] == 0
    assert "GBPUSD_REFERENCE_AND_CONVERSION_DISAGREE" in r["decisions"][0]["allocation_reasons"]


def test_simultaneous_positions_cannot_use_conflicting_usd_gbp_quotes():
    with pytest.raises(ValueError, match="Conflicting simultaneous"):
        allocate(
            (
                (contract(), scenario(identity="A")),
                (
                    contract(pair="GBP_USD"),
                    scenario(
                        identity="B",
                        reference_price="1.3",
                        quote_per_gbp_bid="1.2999",
                        quote_per_gbp_ask="1.3001",
                    ),
                ),
            ),
            Policy(name="MARGIN_ONLY"),
        )
