import json
import os
import socket
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from atlas.backtest import run_backtest
from atlas.config import AtlasError
from atlas.domain import RankedOpportunity, Venue
from atlas.main import main
from atlas.strategy import ORBConfig, ORBStrategy
from atlas.synthetic import LABEL, SCENARIOS, generate

D = Decimal


def test_explicit_opening_range_duration_is_distinct_from_five_minute_candles(context):
    strategy = ORBStrategy()
    assert (
        strategy.evaluate(
            context.data.instrument, context.bars[:3], context.data.session, context.bars[2].end
        ).signal
        is None
    )
    assert (
        strategy.evaluate(
            context.data.instrument, context.bars, context.data.session, context.bars[3].end
        ).signal
        is not None
    )
    longer = ORBStrategy(ORBConfig(opening_range_minutes=20))
    assert (
        longer.evaluate(
            context.data.instrument, context.bars, context.data.session, context.bars[3].end
        ).signal
        is None
    )
    with pytest.raises(AtlasError):
        ORBConfig(opening_range_minutes=7)
    with pytest.raises(AtlasError):
        ORBConfig(candle_minutes=1)


@pytest.mark.parametrize(
    "scenario", ["falling", "sideways", "low_liquidity", "missing", "duplicate", "incomplete"]
)
def test_no_trade_outcomes(scenario):
    report = run_backtest(generate(scenario))
    assert report.simulated_trades == 0
    assert report.ending_equity_gbp == D("1000")
    assert report.reconciled


@pytest.mark.parametrize("venue", [Venue.LSE, Venue.NYSE, Venue.NASDAQ])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_deterministic_replay_and_accounting_for_every_synthetic_scenario(venue, scenario):
    data = generate(scenario, venue)
    report = run_backtest(data)
    assert report.to_dict() == run_backtest(data).to_dict()
    assert report.label == LABEL
    assert report.reconciled
    assert report.net_pnl_gbp == report.ending_equity_gbp - D("1000")
    assert abs(report.gross_pnl_gbp - report.total_costs_gbp - report.net_pnl_gbp) < D("0.00000001")
    assert not report.open_positions
    assert report.dataset_checksum == data.checksum
    assert report.configuration["settings"]["starting_capital_gbp"] == "1000.00"


def test_next_actionable_quote_not_trigger_candle_close():
    data = generate()
    report = run_backtest(data)
    entry = next(fill for fill in report.fills if fill.side == "buy")
    assert entry.timestamp == data.quotes[0].event_time
    assert entry.timestamp > data.bars[3].end
    assert entry.price == data.quotes[0].ask
    assert entry.price != data.bars[3].close
    assert report.total_costs_gbp > 0


def test_same_time_quote_cannot_fill_a_new_signal():
    data = generate()
    timestamp = data.bars[3].end
    quote = replace(
        data.quotes[0], event_time=timestamp, available_time=timestamp, received_time=timestamp
    )
    report = run_backtest(replace(data, quotes=(quote, data.quotes[1])))
    assert report.simulated_trades == 0
    assert report.rejected_opportunities


def test_delayed_bar_cannot_be_used_before_received_time():
    data = generate()
    bars = list(data.bars)
    bars[3] = replace(bars[3], available_time=bars[7].end, received_time=bars[7].end)
    report = run_backtest(replace(data, bars=tuple(bars)))
    assert report.simulated_trades == 0


def test_future_outcomes_cannot_change_earlier_entry():
    data = generate()
    bars = list(data.bars)
    bars[5] = replace(bars[5], high=D("999"), close=D("900"))
    a = run_backtest(data)
    b = run_backtest(replace(data, bars=tuple(bars)))
    assert a.fills[0] == b.fills[0]


def test_gap_losses_can_exceed_planned_risk_and_trigger_daily_policy():
    report = run_backtest(generate("gap"))
    assert report.net_pnl_gbp < D("-5")
    assert any("DAILY_LOSS_THRESHOLD" in reasons for _, reasons in report.risk_limit_events)
    exit_fill = next(fill for fill in report.fills if fill.side == "sell")
    assert exit_fill.price == D("235")  # worse than the planned 250 GBX stop


def test_ambiguous_intrabar_ordering_is_stop_first():
    report = run_backtest(generate("ambiguous"))
    assert report.net_pnl_gbp < 0
    assert dict(report.quality_events)["EXIT_STOP_OR_GAP"] == 1
    assert next(fill.price for fill in report.fills if fill.side == "sell") == D("250")


def test_time_exit_and_half_day():
    data = generate("time_exit")
    report = run_backtest(data)
    assert dict(report.quality_events)["EXIT_TIME"] == 1
    assert report.fills[-1].timestamp == data.session.close - timedelta(minutes=5)
    half = generate("time_exit", day=data.session.local_date.replace(month=12, day=24))
    half_report = run_backtest(half)
    assert half.session.half_day
    assert half_report.fills[-1].timestamp == half.session.close - timedelta(minutes=5)


def test_no_fill_partial_fill_and_stale_quote():
    no_fill = run_backtest(generate("no_fill"))
    assert no_fill.simulated_trades == 0 and no_fill.total_costs_gbp == 0
    assert any("NO_LIQUIDITY_NO_FILL" in reasons for _, reasons in no_fill.rejected_opportunities)
    partial = run_backtest(generate("partial_fill"))
    assert partial.fills[0].quantity == 1
    assert dict(partial.quality_events)["PARTIAL_FILL_REMAINDER_CANCELLED"] == 1
    assert not partial.open_positions
    stale = run_backtest(generate("stale_quote"))
    assert stale.simulated_trades == 0
    assert any("STALE_OR_FUTURE_QUOTE" in reasons for _, reasons in stale.rejected_opportunities)


def test_synthetic_ranking_cannot_claim_approved_economic_evidence(context):
    opportunity = RankedOpportunity(context.signal, D("10"), "artificial-fixture")
    assert opportunity.status == "research_only"
    with pytest.raises(AtlasError):
        replace(opportunity, status="approved")


@pytest.mark.parametrize("command", ["research-check", "synthetic-backtest"])
def test_research_cli_offline_without_reading_broker_credentials(command, monkeypatch, capsys):
    original_getenv = os.environ.get

    def getenv(name, *args):
        if name.startswith("TRADING212_"):
            pytest.fail("Research command attempted to load a broker secret")
        return original_getenv(name, *args)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline research attempted network access")

    monkeypatch.setattr(os.environ, "get", getenv)
    monkeypatch.setenv("ATLAS_ENV", "demo")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert main([command]) == 0
    assert LABEL in capsys.readouterr().out


def test_cli_json_provenance_and_config_examples(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "demo")
    assert (
        main(
            [
                "synthetic-backtest",
                "--json",
                "--research-config",
                "examples/research.json",
                "--risk-config",
                "examples/risk.synthetic.json",
                "--cost-config",
                "examples/costs.synthetic.json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["label"] == LABEL and report["reconciled"]
    assert report["starting_capital_gbp"] == "1000.00"
    assert len(report["dataset_checksum"]) == 64


def test_cli_invalid_input_is_a_safe_error(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "demo")
    assert main(["synthetic-backtest", "--scenario", "not-a-scenario"]) == 1
    assert "Unknown synthetic scenario" in capsys.readouterr().err
    monkeypatch.setenv("ATLAS_ENV", "live")
    assert main(["research-check"]) == 1
    assert "must be demo" in capsys.readouterr().err


def test_missing_or_illiquid_time_exit_is_reported_not_invented():
    data = generate("time_exit")
    no_quote = run_backtest(replace(data, quotes=data.quotes[:1]))
    assert no_quote.open_positions == 1 and no_quote.reconciled
    assert dict(no_quote.quality_events)["UNLIQUIDATED_POSITION_NO_VALID_EXIT_PRICE"] == 1
    quotes = (data.quotes[0], replace(data.quotes[1], bid_size=D("0")))
    no_liquidity = run_backtest(replace(data, quotes=quotes))
    assert no_liquidity.open_positions == 1
    assert dict(no_liquidity.quality_events)["INSUFFICIENT_TIME_EXIT_LIQUIDITY"] == 1


def test_immediate_liquidation_mark_includes_observed_bid_ask_spread():
    report = run_backtest(generate())
    assert report.maximum_drawdown_gbp > report.total_costs_gbp
    assert report.maximum_drawdown_gbp == D("2.06")


def test_saved_original_manifest_matches_generated_datasets():
    from pathlib import Path

    rows = json.loads(
        (Path(__file__).parent / "fixtures" / "synthetic_manifest.json").read_text(encoding="utf-8")
    )
    assert len(rows) == 32
    for row in rows:
        params = json.loads(row["provenance"]["parameters"])
        data = generate(row["name"], Venue(params["venue"]))
        assert row["checksum"] == data.checksum


def test_injected_calendar_and_clock_drive_replay():
    from atlas.sessions import FixtureCalendar, ReplayClock

    data = generate()
    clock = ReplayClock(data.session.open)
    closed = FixtureCalendar(((data.instrument.venue, data.session.local_date, None),))
    report = run_backtest(data, calendar=closed, clock=clock)
    assert report.simulated_trades == 0
    assert any("CALENDAR_MISMATCH" in reasons for _, reasons in report.rejected_opportunities)
    assert clock.now() == data.quotes[-1].visible_time


def test_json_float_fee_inputs_are_rejected(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    data = json.loads(Path("examples/costs.synthetic.json").read_text())
    data["commission_fixed_gbp"] = 0.1
    path = tmp_path / "float-fees.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("ATLAS_ENV", "demo")
    assert main(["research-check", "--cost-config", str(path)]) == 1
    assert "decimal strings" in capsys.readouterr().err


def test_exported_fixture_bundle_is_original_and_complete():
    from pathlib import Path

    bundle = json.loads(
        (Path(__file__).parent / "fixtures" / "synthetic_data.json").read_text(encoding="utf-8")
    )
    assert bundle["label"] == LABEL and len(bundle["datasets"]) == 32
    first = bundle["datasets"][0]
    assert first["instrument"]["quote_unit"] == "GBX"
    assert first["bars"][0]["open"] == "250"
    assert first["bars"][0]["received_time"].endswith("+00:00")
    assert first["checksum"] == generate(first["name"]).checksum


def test_missing_costs_are_explicit_rejections_not_zero_fees():
    from atlas.costs import CostEngine

    report = run_backtest(generate(), costs=CostEngine(()))
    assert report.simulated_trades == 0 and report.ending_equity_gbp == D("1000")
    assert any(
        "UNKNOWN_OR_AMBIGUOUS_COST_SCHEDULE" in reasons
        for _, reasons in report.rejected_opportunities
    )


def test_replay_capital_and_risk_budgets_are_parameterized():
    from atlas.research import ResearchSettings

    report = run_backtest(generate(), ResearchSettings(starting_capital_gbp=D("2000")))
    assert report.starting_capital_gbp == D("2000")
    assert report.risk_decisions
    assert max(item.planned_risk_gbp for item in report.risk_decisions) <= D("10")
    assert report.net_pnl_gbp == report.ending_equity_gbp - D("2000")


def test_wrong_unit_time_exit_cannot_fabricate_a_fill():
    from atlas.domain import QuoteUnit

    data = generate("time_exit")
    quotes = (data.quotes[0], replace(data.quotes[1], unit=QuoteUnit.GBP))
    report = run_backtest(replace(data, quotes=quotes))
    assert report.open_positions == 1
    assert all(fill.side != "sell" for fill in report.fills)


def test_fx_conversion_expenses_are_attributed_separately_from_total_fees():
    report = run_backtest(generate("orb", Venue.NASDAQ))
    # Two shares: entry USD206.04*.8*5bps rounds up to .09 GBP.
    # Exit USD216*.8*5bps also rounds up to .09 GBP.
    assert report.fx_conversion_costs_gbp == D(".18")
    assert report.total_costs_gbp == D(".45")
    assert sum(
        amount
        for fill in report.fills
        for name, amount in fill.cost_components
        if name.endswith("_fx")
    ) == D(".18")
