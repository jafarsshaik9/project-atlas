"""Offline research settings; unrelated Practice balances remain broker fixtures."""

import json
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from atlas.broker import Trading212Client
from atlas.config import AtlasError, Settings
from atlas.main import main
from atlas.research import ResearchSettings, VirtualLedger

SCENARIOS = json.loads(
    (Path(__file__).parent / "fixtures" / "research_scenarios.json").read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def clean_research_environment(monkeypatch):
    for setting in fields(ResearchSettings):
        monkeypatch.delenv(setting.metadata["env"], raising=False)


def test_default_ledger_and_risk_budgets():
    settings = ResearchSettings.from_environment()
    ledger = VirtualLedger(settings)
    assert ledger.initial_balance_gbp == Decimal("1000.00")
    assert ledger.cash_balance_gbp == Decimal("1000.00")
    assert settings.planned_risk_per_trade_pct == Decimal("0.5")
    assert settings.daily_loss_threshold_pct == Decimal("1")
    assert settings.max_concurrent_planned_risk_pct == Decimal("1")
    assert settings.planned_risk_per_trade_gbp == Decimal("5.00")
    assert settings.daily_loss_threshold_gbp == Decimal("10.00")
    assert settings.max_concurrent_planned_risk_gbp == Decimal("10.00")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item["name"])
def test_synthetic_research_scenarios(scenario, monkeypatch):
    for setting in fields(ResearchSettings):
        monkeypatch.setenv(setting.metadata["env"], scenario["settings"][setting.name])
    settings = ResearchSettings.from_environment()
    ledger = VirtualLedger(settings)
    expected = scenario["expected_gbp"]
    assert ledger.cash_balance_gbp == Decimal(expected["opening_cash"])
    assert settings.planned_risk_per_trade_gbp == Decimal(expected["planned_risk_per_trade"])
    assert settings.daily_loss_threshold_gbp == Decimal(expected["daily_loss_threshold"])
    assert settings.max_concurrent_planned_risk_gbp == Decimal(
        expected["max_concurrent_planned_risk"]
    )


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "-Infinity", "invalid", ""])
@pytest.mark.parametrize("setting", fields(ResearchSettings), ids=lambda item: item.name)
def test_invalid_environment_settings_fail_without_echoing_input(setting, value, monkeypatch):
    monkeypatch.setenv(setting.metadata["env"], value)
    with pytest.raises(AtlasError) as error:
        ResearchSettings.from_environment()
    assert setting.metadata["env"] in str(error.value)
    assert "invalid" not in str(error.value)
    assert "Infinity" not in str(error.value)


@pytest.mark.parametrize(
    "setting",
    [setting for setting in fields(ResearchSettings) if setting.name.endswith("pct")],
    ids=lambda item: item.name,
)
def test_percentage_above_100_rejected(setting, monkeypatch):
    monkeypatch.setenv(setting.metadata["env"], "100.01")
    with pytest.raises(AtlasError, match="percentage"):
        ResearchSettings.from_environment()


def test_fractional_penny_starting_capital_rejected(monkeypatch):
    monkeypatch.setenv("ATLAS_STARTING_CAPITAL_GBP", "1000.001")
    with pytest.raises(AtlasError, match="whole number of pennies"):
        ResearchSettings.from_environment()


def test_per_trade_budget_cannot_exceed_concurrent_budget(monkeypatch):
    monkeypatch.setenv("ATLAS_PLANNED_RISK_PER_TRADE_PCT", "2")
    with pytest.raises(AtlasError, match="cannot exceed"):
        ResearchSettings.from_environment()


def test_ledger_capital_is_independent_of_mocked_practice_balance():
    settings = ResearchSettings.from_environment()
    ledger = VirtualLedger(settings)
    client = Trading212Client(
        Settings(api_key="dummy-key", api_secret="dummy-secret"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"currency": "GBP", "totalValue": 25000, "cash": {"availableToTrade": 20000}},
            )
        ),
    )
    summary = client.account_summary()
    assert summary.total_value == Decimal("25000")
    assert ledger.initial_balance_gbp == Decimal("1000.00")
    assert ledger.cash_balance_gbp == Decimal("1000.00")
    assert ledger.settings.planned_risk_per_trade_gbp == Decimal("5.00")


def test_health_reports_configured_capital_and_provisional_settings_offline(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "demo")
    monkeypatch.setenv("ATLAS_STARTING_CAPITAL_GBP", "2000.00")

    def forbidden(*args, **kwargs):
        pytest.fail("Offline research health contacted the broker")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert main(["health"]) == 0
    output = capsys.readouterr().out
    assert "planned simulated capital: GBP 2,000.00" in output
    assert "Initial virtual ledger cash: GBP 2,000.00" in output
    assert "Planned risk per trade: 0.5% (GBP 10.00)" in output
    assert "daily loss threshold: 1% (GBP 20.00)" in output
    assert "maximum concurrent planned risk: 1% (GBP 20.00)" in output
    assert "not proven optimal or guaranteed loss limits" in output
    assert "Independent of the Practice account balance" in output


def test_health_rejects_invalid_research_settings_without_echoing_value(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "demo")
    monkeypatch.setenv("ATLAS_STARTING_CAPITAL_GBP", "private-input-not-a-number")
    assert main(["health"]) == 1
    output = capsys.readouterr().err
    assert "ATLAS_STARTING_CAPITAL_GBP" in output
    assert "private-input" not in output


def test_broker_check_is_independent_of_research_configuration(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "demo")
    monkeypatch.setenv("TRADING212_API_KEY", "dummy-key")
    monkeypatch.setenv("TRADING212_API_SECRET", "dummy-secret")
    monkeypatch.setenv("ATLAS_STARTING_CAPITAL_GBP", "invalid-research-only-value")

    def send(client, request, **kwargs):
        assert request.method == "GET"
        assert str(request.url) == "https://demo.trading212.com/api/v0/equity/account/summary"
        return httpx.Response(
            200,
            json={"currency": "GBP", "totalValue": 25000, "cash": {"availableToTrade": 20000}},
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    assert main(["broker-check"]) == 0
    output = capsys.readouterr().out
    assert "Total value: 25000.00; available cash: 20000.00" in output
    assert "invalid-research-only-value" not in output
