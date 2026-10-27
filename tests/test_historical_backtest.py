"""Phase 3B uses only disposable accepted fixtures and no external services."""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from atlas.config import AtlasError
from atlas.domain import DecisionStatus, Venue
from atlas.historical_backtest import (
    AvailabilityMode,
    EvaluationPlan,
    EvaluationRole,
    ExecutionAssumptions,
    FxPolicy,
    HistoricalBacktestConfig,
    InstrumentRules,
    run_historical_backtest,
)
from atlas.main import main
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import DownloadRequest, RawPage, digest
from atlas.market_data.providers import FixtureProvider, fixture_instruments
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore

D = Decimal
ONE = D("1")
DAY = date(2026, 10, 26)


class HistoricalFixtureProvider:
    descriptor = FixtureProvider.descriptor

    def __init__(
        self,
        calendar,
        *,
        availability="unknown",
        missing=False,
        zero_volume=False,
        sub_tick=False,
    ):
        session = calendar.sessions()[0]
        self.source = FixtureProvider(
            calendar, page_size=5000, clock=lambda: session.close + timedelta(days=1)
        )
        self.availability = availability
        self.missing = missing
        self.zero_volume = zero_volume
        self.sub_tick = sub_tick

    def fetch_page(self, request, cursor=None):
        page = self.source.fetch_page(request, cursor).model_dump()
        values = page["body"]["values"]
        for index, row in enumerate(values):
            opened = D("100") + D(index) * D("0.01")
            row.update(
                open=str(opened),
                high=str(opened + (D("0.015") if self.sub_tick and index == 40 else D("0.02"))),
                low=str(opened - D("0.01")),
                close=str(opened + D("0.01")),
                volume="0" if self.zero_volume else "1000",
            )
        if self.missing:
            values.pop(10)
        if self.availability == "unknown":
            page["original_availability"] = "unknown"
        page["wire_sha256"] = digest(page["body"])
        return RawPage.model_validate(page)


def build_dataset(
    tmp_path,
    *,
    day=DAY,
    availability="unknown",
    missing=False,
    zero=False,
    sub_tick=False,
):
    calendar = HistoricalCalendar(Venue.NASDAQ, day, day)
    session = calendar.sessions()[0]
    instrument = next(
        item
        for item in fixture_instruments()
        if item.venue == Venue.NASDAQ and item.quote_unit.value == "USD"
    )
    request = DownloadRequest(
        provider="fixture-v1",
        instrument=instrument,
        start=session.open,
        end=session.open + timedelta(minutes=60),
    )
    store = HistoricalStore(tmp_path / f"history-{day}-{availability}-{missing}-{zero}.sqlite3")
    dataset_id = download(
        HistoricalFixtureProvider(
            calendar,
            availability=availability,
            missing=missing,
            zero_volume=zero,
            sub_tick=sub_tick,
        ),
        request,
        calendar,
        store,
    )[0]
    return store, dataset_id, session


def configuration(
    session,
    *,
    mode=AvailabilityMode.ASSUMED,
    verified_fx=False,
    role=EvaluationRole.OUT_OF_SAMPLE,
    participation=ONE,
):
    return HistoricalBacktestConfig(
        mode,
        60 if mode == AvailabilityMode.ASSUMED else None,
        InstrumentRules(D("0.01")),
        ExecutionAssumptions(
            D("5"),
            D("5"),
            D("0.01"),
            D("0"),
            D("15"),
            D("0"),
            D("0"),
            D("0"),
            participation,
        ),
        FxPolicy(
            D("0.80"),
            session.open,
            session.open,
            "EXPLICIT-OFFLINE-FX-FIXTURE",
            verified_fx,
        ),
        EvaluationPlan(
            role,
            session.open - timedelta(days=1) if role == EvaluationRole.OUT_OF_SAMPLE else None,
        ),
    )


def test_unknown_availability_strict_mode_exposes_no_bars(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    report = run_historical_backtest(
        store,
        dataset,
        configuration(session, mode=AvailabilityMode.VERIFIED, verified_fx=True),
        allow_artificial_fixture=True,
    )
    assert report.selected_rows == 0
    assert report.result.simulated_trades == 0
    assert not report.point_in_time_verified


def test_assumed_mode_is_explicit_and_does_not_mutate_dataset(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    before = store.inspect(dataset)
    report = run_historical_backtest(
        store, dataset, configuration(session), allow_artificial_fixture=True
    )
    assert report.selected_rows == report.source_rows == 60
    assert "ASSUMED" in report.availability_mode
    assert "BAR_END_PLUS_DELAY_NOT_PROVIDER_PUBLICATION_TIME" in report.assumptions
    assert not report.point_in_time_verified
    assert store.inspect(dataset) == before


def test_verified_bars_and_fx_can_be_marked_point_in_time(tmp_path):
    store, dataset, session = build_dataset(tmp_path, availability="known")
    report = run_historical_backtest(
        store,
        dataset,
        configuration(session, mode=AvailabilityMode.VERIFIED, verified_fx=True),
        allow_artificial_fixture=True,
    )
    assert report.selected_rows == 60 and report.point_in_time_verified


def test_next_bar_fill_occurs_after_signal_and_uses_reported_volume(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    report = run_historical_backtest(
        store, dataset, configuration(session), allow_artificial_fixture=True
    )
    buys = [fill for fill in report.result.fills if fill.side == "buy"]
    approvals = [
        decision
        for decision in report.result.risk_decisions
        if decision.status == DecisionStatus.APPROVED
    ]
    assert len(buys) == len(approvals) == 1
    assert buys[0].timestamp == approvals[0].timestamp
    assert buys[0].timestamp > session.open + timedelta(minutes=20)
    assert buys[0].price > D("100.20")  # The breakout bar close; fill uses a later bar open.
    assert buys[0].quantity <= D("10")  # 1% of the reported 1,000-share minute volume.


def test_costs_fx_risk_and_portfolio_reconcile(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    report = run_historical_backtest(
        store, dataset, configuration(session), allow_artificial_fixture=True
    ).result
    assert report.starting_capital_gbp == D("1000.00")
    assert report.total_costs_gbp > 0 and report.fx_conversion_costs_gbp > 0
    assert report.reconciled and report.open_positions == 0
    assert all(
        decision.policy_version == "historical-risk-v1" for decision in report.risk_decisions
    )


def test_equity_and_drawdown_curves_use_authoritative_ledger_snapshots(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    report = run_historical_backtest(
        store, dataset, configuration(session), allow_artificial_fixture=True
    )
    assert report.equity_curve
    assert report.equity_curve[0][1:] == (D("1000.00"), D("1000.00"))
    assert max(value for _, value in report.drawdown_curve) == report.result.maximum_drawdown_gbp


def test_zero_reported_volume_never_creates_liquidity(tmp_path):
    store, dataset, session = build_dataset(tmp_path, zero=True)
    report = run_historical_backtest(
        store, dataset, configuration(session), allow_artificial_fixture=True
    )
    assert report.result.simulated_trades == 0


def test_source_aggregates_may_be_sub_tick_but_modeled_fills_are_tick_aligned(tmp_path):
    store, dataset, session = build_dataset(tmp_path, sub_tick=True)
    report = run_historical_backtest(
        store, dataset, configuration(session), allow_artificial_fixture=True
    )
    assert report.selected_rows == 60
    assert report.result.fills
    assert all(fill.price % D("0.01") == 0 for fill in report.result.fills)


def test_missing_source_bar_is_quarantined_before_replay(tmp_path):
    store, dataset, session = build_dataset(tmp_path, missing=True)
    assert store.inspect(dataset)[0]["state"] == "QUARANTINED"
    with pytest.raises(AtlasError, match="accepted historical"):
        run_historical_backtest(
            store, dataset, configuration(session), allow_artificial_fixture=True
        )


@pytest.mark.parametrize("day,open_hour", [(date(2026, 3, 9), 13), (date(2025, 11, 3), 14)])
def test_dst_session_boundaries_are_recorded(day, open_hour, tmp_path):
    store, dataset, session = build_dataset(tmp_path, day=day)
    report = run_historical_backtest(
        store, dataset, configuration(session), allow_artificial_fixture=True
    )
    assert session.open.hour == open_hour
    assert report.derived_strategy_bars == 12


def test_replay_is_deterministic_and_reproducible(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    config = configuration(session)
    first = run_historical_backtest(store, dataset, config, allow_artificial_fixture=True)
    second = run_historical_backtest(store, dataset, config, allow_artificial_fixture=True)
    assert first == second
    assert (
        first.run_id == second.run_id
        and first.configuration_checksum == second.configuration_checksum
    )


def test_out_of_sample_cutoff_must_precede_replay(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    config = configuration(session)
    config = HistoricalBacktestConfig(
        config.availability_mode,
        config.assumed_delay_seconds,
        config.instrument,
        config.execution,
        config.fx,
        EvaluationPlan(EvaluationRole.OUT_OF_SAMPLE, session.open),
    )
    with pytest.raises(AtlasError, match="OUT_OF_SAMPLE"):
        run_historical_backtest(store, dataset, config, allow_artificial_fixture=True)


def test_artificial_data_requires_explicit_opt_in(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    with pytest.raises(AtlasError, match="test-only opt-in"):
        run_historical_backtest(store, dataset, configuration(session))


def test_invalid_or_missing_fx_is_not_assumed(tmp_path):
    store, dataset, session = build_dataset(tmp_path)
    config = configuration(session)
    with pytest.raises(AtlasError):
        FxPolicy(D("0"), session.open, session.open, "INVALID")
    stale = HistoricalBacktestConfig(
        config.availability_mode,
        config.assumed_delay_seconds,
        config.instrument,
        config.execution,
        FxPolicy(
            D("0.8"), session.open - timedelta(days=2), session.open - timedelta(days=2), "STALE"
        ),
        config.evaluation,
    )
    report = run_historical_backtest(store, dataset, stale, allow_artificial_fixture=True)
    assert report.result.simulated_trades == 0
    assert any(
        "FX_UNAVAILABLE_OR_STALE" in reasons for _, reasons in report.result.rejected_opportunities
    )


def config_payload(dataset, session):
    return {
        "schema_version": 1,
        "dataset_id": dataset,
        "availability": {"mode": "ASSUMED", "delay_seconds": 60},
        "instrument": {"tick_size": "0.01", "quantity_step": "1", "minimum_quantity": "1"},
        "execution": {
            "spread_bps": "5",
            "slippage_bps": "5",
            "commission_fixed_gbp": "0.01",
            "commission_bps": "0",
            "fx_bps": "15",
            "entry_tax_bps": "0",
            "exit_tax_bps": "0",
            "other_fixed_gbp": "0",
            "max_volume_participation_pct": "1",
            "stress_multiplier": "2",
        },
        "fx": {
            "gbp_per_usd": "0.8",
            "event_time": session.open.isoformat(),
            "available_time": session.open.isoformat(),
            "source": "EXPLICIT-OFFLINE-FX-FIXTURE",
            "point_in_time_verified": False,
        },
        "evaluation": {
            "role": "OUT_OF_SAMPLE",
            "parameter_selection_cutoff": (session.open - timedelta(days=1)).isoformat(),
        },
        "orb": {
            "opening_range_minutes": 15,
            "candle_minutes": 5,
            "target_r": "2",
            "signal_lifetime_minutes": 5,
            "exit_buffer_minutes": 5,
            "minimum_bar_volume": "100",
        },
        "research": {
            "starting_capital_gbp": "1000.00",
            "planned_risk_per_trade_pct": "0.5",
            "daily_loss_threshold_pct": "1",
            "max_concurrent_planned_risk_pct": "1",
        },
    }


def test_cli_offline_json_and_no_side_effects(tmp_path, capsys):
    store, dataset, session = build_dataset(tmp_path)
    config = tmp_path / "backtest.json"
    config.write_text(json.dumps(config_payload(dataset, session)), encoding="utf-8")
    assert (
        main(
            [
                "historical-backtest",
                "--historical-config",
                str(config),
                "--historical-db",
                str(store.path),
                "--allow-artificial-historical",
                "--json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["availability_mode"] == "ASSUMED"
    assert "NOT MARKET PERFORMANCE" in output["label"]


def test_cli_example_refuses_placeholder_without_database_creation(tmp_path):
    missing = tmp_path / "must-not-exist.sqlite3"
    assert (
        main(
            [
                "historical-backtest",
                "--historical-config",
                "examples/historical-backtest.assumed.json",
                "--historical-db",
                str(missing),
            ]
        )
        == 1
    )
    assert not missing.exists()


def test_historical_module_has_no_broker_notification_or_network_imports():
    source = (__import__("pathlib").Path("src/atlas/historical_backtest.py")).read_text()
    for forbidden in ("atlas.broker", "atlas.notifications", "httpx", "requests", "socket"):
        assert forbidden not in source
