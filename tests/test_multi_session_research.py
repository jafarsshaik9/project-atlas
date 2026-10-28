"""Reproducible multi-day/multi-symbol research over original disposable fixtures."""

import json
import socket
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_historical_backtest import HistoricalFixtureProvider, configuration

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.historical_backtest import AvailabilityMode, EvaluationPlan, EvaluationRole
from atlas.main import main
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import CorporateAction, DownloadRequest, RawPage, digest
from atlas.market_data.providers import fixture_instruments
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore
from atlas.multi_session_research import (
    SessionRef,
    WalkForward,
    header,
    prepare,
    run_multi_session,
    walk_forward,
)
from atlas.strategy import ORBConfig


def build(
    tmp_path,
    days=(date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)),
    symbols=("A", "B"),
    availability="unknown",
    actions=(),
    missing=False,
    shock_from=None,
):
    store = HistoricalStore(tmp_path / "history.sqlite3")
    refs = []
    template = next(i for i in fixture_instruments() if i.venue == Venue.NASDAQ)
    for day in days:
        calendar = HistoricalCalendar(Venue.NASDAQ, day, day)
        session = calendar.sessions()[0]
        for symbol in symbols:
            mapping = type(template.mappings[0])(
                **(template.mappings[0].model_dump() | {"symbol": "FAKE-" + symbol})
            )
            instrument = type(template)(
                **(
                    template.model_dump()
                    | {
                        "instrument_id": template.instrument_id + "-" + symbol,
                        "mappings": (mapping,),
                    }
                )
            )
            request = DownloadRequest(
                provider="fixture-v1",
                instrument=instrument,
                start=session.open,
                end=session.open + timedelta(minutes=60),
            )
            selected_actions = tuple(
                a for a in actions if a.instrument_id == instrument.instrument_id
            )
            provider = HistoricalFixtureProvider(
                calendar, availability=availability, missing=missing
            )
            if shock_from is not None:
                fetch = provider.fetch_page

                def shocked(request, cursor=None, original=fetch):
                    page = original(request, cursor).model_dump()
                    for row in page["body"]["values"][shock_from:]:
                        row.update(open="1000", high="1001", low="999", close="1000")
                    page["wire_sha256"] = digest(page["body"])
                    return RawPage.model_validate(page)

                provider.fetch_page = shocked
            identity = download(
                provider,
                request,
                calendar,
                store,
                actions=selected_actions,
            )[0]
            config = replace(configuration(session), evaluation=EvaluationPlan())
            refs.append(SessionRef(str(store.path), identity, config))
    return refs


def test_multi_session_shared_account_and_reproducible_input_order(tmp_path):
    refs = build(tmp_path)
    result = run_multi_session(refs, allow_artificial=True)
    assert result == run_multi_session(list(reversed(refs)), allow_artificial=True)
    assert result["reconciled"] and result["trade_count"] == 6
    assert len(result["sessions"]) == 3
    assert (
        result["sessions"][1]["starting_equity_gbp"] == result["sessions"][0]["ending_equity_gbp"]
    )
    assert (
        result["sessions"][2]["starting_equity_gbp"] == result["sessions"][1]["ending_equity_gbp"]
    )
    assert Decimal(result["ending_equity_gbp"]) == Decimal("1000") + Decimal(
        result["realised_pnl_gbp"]
    )
    assert not result["point_in_time_verified"]
    assert "EXPLORATORY" in result["label"]
    assert result["expectancy_gbp"] is not None and result["maximum_drawdown_gbp"] is not None


def test_multiple_instruments_compete_for_capital_without_overspending(tmp_path):
    refs = build(tmp_path, days=(date(2026, 10, 19),), symbols=("A", "B", "C", "D", "E"))
    result = run_multi_session(refs, allow_artificial=True)
    assert all(Decimal(cash) >= 0 for _, _, cash in result["equity_curve"])
    assert result["reconciled"]
    assert any(d["status"] == "rejected" for d in result["risk_decisions"])
    assert 0 < result["trade_count"] <= 5


def test_verified_availability_never_promotes_unknown_bars(tmp_path):
    refs = build(tmp_path, symbols=("A",))
    strict = [
        replace(
            r,
            config=replace(
                r.config, availability_mode=AvailabilityMode.VERIFIED, assumed_delay_seconds=None
            ),
        )
        for r in refs
    ]
    result = run_multi_session(strict, allow_artificial=True)
    assert result["source_rows"] == 180 and result["selected_rows"] == 0
    assert result["trade_count"] == 0 and result["ending_equity_gbp"] == "1000.00"
    assert not result["point_in_time_verified"]


def test_known_availability_is_preserved_but_fixture_is_never_real_verified_performance(tmp_path):
    refs = build(tmp_path, symbols=("A",), availability="known")
    strict = [
        replace(
            r,
            config=replace(
                r.config, availability_mode=AvailabilityMode.VERIFIED, assumed_delay_seconds=None
            ),
        )
        for r in refs
    ]
    result = run_multi_session(strict, allow_artificial=True)
    assert result["selected_rows"] == 180 and not result["point_in_time_verified"]


def test_duplicate_partitions_and_unreviewed_artificial_data_are_refused(tmp_path):
    refs = build(tmp_path, days=(date(2026, 10, 19),), symbols=("A",))
    with pytest.raises(AtlasError, match="Duplicate"):
        run_multi_session(refs + refs, allow_artificial=True)
    with pytest.raises(AtlasError, match="Artificial"):
        run_multi_session(refs)


def test_missing_bars_cannot_enter_replay(tmp_path):
    refs = build(tmp_path, days=(date(2026, 10, 19),), symbols=("A",), missing=True)
    with pytest.raises(AtlasError, match="accepted"):
        run_multi_session(refs, allow_artificial=True)


def test_unsupported_corporate_action_day_is_not_silently_adjusted(tmp_path):
    action = CorporateAction(
        action_id="original-split",
        instrument_id="FIXTURE-XNAS-USD-001-A",
        kind="split",
        effective_date=date(2026, 10, 19),
        source="original-fixture",
        ratio=Decimal("2"),
    )
    refs = build(tmp_path, days=(date(2026, 10, 19),), symbols=("A",), actions=(action,))
    with pytest.raises(AtlasError, match="Corporate-action"):
        run_multi_session(refs, allow_artificial=True)


def test_fx_must_be_available_and_fresh_for_each_session(tmp_path):
    refs = build(tmp_path, symbols=("A",))
    stale = [replace(r, config=replace(r.config, fx=refs[0].config.fx)) for r in refs]
    with pytest.raises(AtlasError, match="FX_UNAVAILABLE_OR_STALE"):
        run_multi_session(stale, allow_artificial=True)


def test_out_of_sample_cutoff_cannot_enter_test_window(tmp_path):
    refs = build(tmp_path, symbols=("A",))
    ref = refs[0]
    opening = header(ref)[2].open
    changed = replace(
        ref,
        config=replace(
            ref.config, evaluation=EvaluationPlan(EvaluationRole.OUT_OF_SAMPLE, opening)
        ),
    )
    with pytest.raises(AtlasError, match="cutoff"):
        run_multi_session([changed], allow_artificial=True)


def test_walk_forward_splits_all_instruments_by_date_and_is_reproducible(tmp_path):
    refs = build(
        tmp_path, days=tuple(date(2026, 10, d) for d in (19, 20, 21, 22, 23)), symbols=("A",)
    )
    candidates = [ORBConfig(opening_range_minutes=5), ORBConfig(opening_range_minutes=15)]
    windows = WalkForward(2, 1, 1, 1)
    result = walk_forward(refs, candidates, windows, allow_artificial=True)
    assert result == walk_forward(refs, candidates, windows, allow_artificial=True)
    assert len(result["folds"]) == 2
    for fold in result["folds"]:
        assert max(fold["training_dates"]) < min(fold["validation_dates"])
        assert max(fold["validation_dates"]) < min(fold["test_dates"])
        assert (
            fold["test"]["configuration"]["configurations"][0]["evaluation"]["role"]
            == "OUT_OF_SAMPLE"
        )
    assert set(result["folds"][0]["test_dates"]).isdisjoint(result["folds"][1]["test_dates"])


def test_test_period_prices_cannot_change_parameter_selection(tmp_path):
    refs = build(tmp_path, days=tuple(date(2026, 10, d) for d in (19, 20, 21)), symbols=("A",))
    candidates = [ORBConfig(opening_range_minutes=5), ORBConfig(opening_range_minutes=15)]
    first = walk_forward(refs, candidates, WalkForward(1, 1, 1, 1), allow_artificial=True)
    # Test-only execution costs can change test outcomes, never training/validation ranking.
    changed = refs[:-1] + [
        replace(
            refs[-1],
            config=replace(
                refs[-1].config,
                execution=replace(refs[-1].config.execution, slippage_bps=Decimal("30")),
            ),
        )
    ]
    second = walk_forward(changed, candidates, WalkForward(1, 1, 1, 1), allow_artificial=True)
    a, b = first["folds"][0], second["folds"][0]
    assert (
        a["selected_candidate"] == b["selected_candidate"] and a["comparisons"] == b["comparisons"]
    )
    assert a["test"]["result_checksum"] != b["test"]["result_checksum"]


@pytest.mark.parametrize("values", [(1, 1, 2, 1), (0, 1, 1, 1), (1, True, 1, 1)])
def test_invalid_walk_forward_windows_refused(values):
    with pytest.raises(AtlasError):
        WalkForward(*values)


def test_no_lookahead_fills_require_later_event_than_signal(tmp_path):
    refs = build(tmp_path, days=(date(2026, 10, 19),), symbols=("A",))
    result = run_multi_session(refs, allow_artificial=True)
    buys = [f for f in result["fills"] if f["side"] == "buy"]
    approvals = {d["intent_id"]: d for d in result["risk_decisions"] if d["status"] == "approved"}
    assert buys
    for fill in buys:
        assert fill["timestamp"] == approvals[fill["intent_id"]]["timestamp"]
    prepared = prepare(refs[0], allow_artificial=True)
    assert all(q.available_time >= q.event_time + timedelta(minutes=2) for q in prepared["quotes"])
    assert all(b.available_time >= b.end + timedelta(minutes=1) for b in prepared["bars"])


def test_capital_settings_cannot_change_mid_replay(tmp_path):
    refs = build(tmp_path, symbols=("A",))
    refs[1] = replace(
        refs[1],
        config=replace(
            refs[1].config,
            settings=replace(refs[1].config.settings, starting_capital_gbp=Decimal("2000")),
        ),
    )
    with pytest.raises(AtlasError, match="share one capital"):
        run_multi_session(refs, allow_artificial=True)


def test_later_price_shock_cannot_change_earlier_fills(tmp_path):
    day = date(2026, 10, 19)
    a = build(tmp_path / "a", days=(day,), symbols=("A",))
    b = build(tmp_path / "b", days=(day,), symbols=("A",), shock_from=50)
    first = run_multi_session(a, allow_artificial=True)
    second = run_multi_session(b, allow_artificial=True)
    boundary = header(a[0])[2].open + timedelta(minutes=50)

    def early(result):
        return [f for f in result["fills"] if f["timestamp"] < str(boundary)]

    assert early(first) and early(first) == early(second)


def test_offline_backtest_cli_explicit_fixed_fx_policy(tmp_path, monkeypatch, capsys):
    refs = build(tmp_path, days=(date(2026, 10, 26), date(2026, 10, 27)), symbols=("A",))
    from pathlib import Path

    config = json.loads(
        (Path(__file__).parents[1] / "examples/historical-backtest.assumed.json").read_text()
    )
    config["evaluation"] = {"role": "IN_SAMPLE", "parameter_selection_cutoff": None}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    bundle = {
        "schema_version": 1,
        "configuration": "config.json",
        "fx_policy": "FIXED_ASSUMED_PER_SESSION",
        "datasets": [{"database": r.database, "dataset_id": r.dataset_id} for r in refs],
    }
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    def forbidden(*a, **kw):
        pytest.fail("Network attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    before = HistoricalStore(refs[0].database, read_only=True).inspect(refs[0].dataset_id)
    assert (
        main(
            ["research-history", "backtest", "--bundle", str(path), "--allow-artificial", "--json"]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["reconciled"] and not result["point_in_time_verified"]
    assert len(result["sessions"]) == 2
    assert HistoricalStore(refs[0].database, read_only=True).inspect(refs[0].dataset_id) == before
