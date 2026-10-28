"""Read-only explanations, explicit availability and isolated service integration."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from atlas import local_environment
from atlas.activity import ActivityEvent, EventType, now_utc
from atlas.dashboard_security import DashboardAuth
from atlas.historical_backtest import AvailabilityMode
from atlas.history import HistoryStore
from atlas.multi_session_research import SessionRef, prepare, run_multi_session
from atlas.notifications import TelegramProvider, TelegramSettings, deliver_one, format_message
from atlas.research import ResearchSettings
from atlas.research_diagnostics import diagnose, replay_dataset, reporting
from atlas.research_history import recorded_backtest
from atlas.telegram_cards import render_card
from atlas.web.app import create_app
from tests.test_historical_backtest import build_dataset, configuration
from tests.test_multi_session_research import build


def test_service_env_root_allowlist_and_process_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(local_environment, "ROOT", tmp_path)
    for name in local_environment.SERVICE_NAMES:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "ATLAS_ENV=demo\nATLAS_TELEGRAM_ENABLED=false\n"
        "TRADING212_API_KEY=must-not-load\nALPACA_API_KEY=must-not-load\n"
        "ATLAS_HISTORY_DB=local/history.sqlite3\n"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    values = local_environment.service_values()
    assert values["ATLAS_TELEGRAM_ENABLED"] == "true"
    assert values["ATLAS_HISTORY_DB"] == str(tmp_path / "local/history.sqlite3")
    assert "TRADING212_API_KEY" not in values and "ALPACA_API_KEY" not in values
    import os

    with local_environment.service_environment():
        assert os.environ["ATLAS_ENV"] == "demo"
    assert "ATLAS_ENV" not in os.environ
    assert os.environ["ATLAS_TELEGRAM_ENABLED"] == "true"


def test_diagnostics_replay_has_no_source_writes_and_matches_engine(tmp_path):
    refs = build(tmp_path)
    before = (tmp_path / "history.sqlite3").read_bytes()
    report = diagnose(refs[0], allow_artificial=True)
    assert report["categories"]["OPENING_RANGE_FORMING"] > 0
    assert report["categories"]["SIGNAL"] > 0
    assert report["trade_count"] == 1
    assert report["risk_and_execution"]
    assert all("available_cash_gbp" in x for x in report["risk_and_execution"])
    assert (tmp_path / "history.sqlite3").read_bytes() == before


def test_verified_unknown_availability_is_missing_data_not_no_breakout(tmp_path):
    ref = build(tmp_path)[0]
    ref = replace(
        ref,
        config=replace(
            ref.config, availability_mode=AvailabilityMode.VERIFIED, assumed_delay_seconds=None
        ),
    )
    result = diagnose(ref, allow_artificial=True)
    assert result["available_rows"] == 0
    assert result["categories"] == {"MISSING_OR_UNAVAILABLE_DATA": 1}
    assert result["trade_count"] == 0 and not result["point_in_time_verified"]


def test_risk_rejection_does_not_become_a_simulated_trade(tmp_path):
    ref = build(tmp_path)[0]
    ref = replace(
        ref,
        config=replace(ref.config, settings=ResearchSettings(starting_capital_gbp=Decimal("1.00"))),
    )
    result = diagnose(ref, allow_artificial=True)
    assert result["categories"]["RISK_REJECTION"] > 0
    assert result["trade_count"] == 0 and result["risk_rejections"]


def test_strategy_filter_rejection_is_distinct(tmp_path):
    store, dataset, session = build_dataset(tmp_path, zero=True)
    ref = SessionRef(str(store.path), dataset, configuration(session))
    result = diagnose(ref, allow_artificial=True)
    assert any("STRATEGY_DATA_QUALITY" in t["reasons"] for t in result["opening_range_and_filters"])
    assert result["trade_count"] == 0


def test_no_breakout_is_distinct_from_missing_data(tmp_path):
    from atlas.market_data.contracts import RawPage, digest
    from atlas.market_data.service import download
    from atlas.market_data.store import HistoricalStore
    from tests.test_historical_backtest import HistoricalFixtureProvider

    ref = build(tmp_path / "original")[0]
    d = prepare(ref, allow_artificial=True)
    from atlas.market_data.contracts import DownloadRequest

    request = DownloadRequest.model_validate(d["manifest"]["request"])
    provider = HistoricalFixtureProvider(d["calendar"])
    original = provider.fetch_page

    def flat(request, cursor):
        page = original(request, cursor).model_dump()
        for row in page["body"]["values"]:
            row.update(open="100", high="101", low="99", close="100")
        page["wire_sha256"] = digest(page["body"])
        return RawPage.model_validate(page)

    provider.fetch_page = flat
    store = HistoricalStore(tmp_path / "flat.sqlite3")
    dataset = download(provider, request, d["calendar"], store)[0]
    result = diagnose(SessionRef(str(store.path), dataset, ref.config), allow_artificial=True)
    assert result["available_rows"] == 60
    assert result["categories"]["NO_BREAKOUT"] > 0
    assert "MISSING_OR_UNAVAILABLE_DATA" not in result["categories"]
    assert result["trade_count"] == 0


def test_reporting_groups_partial_exit_intents_by_position(tmp_path):
    base = run_multi_session(build(tmp_path), allow_artificial=True)
    fill = next(f for f in base["fills"] if f["side"] == "sell")
    original = dict(fill)
    qty = Decimal(fill["quantity"])
    pnl = Decimal(fill["realized_pnl_gbp"])
    index = base["fills"].index(fill)
    first = dict(
        fill, quantity=str(qty / 2), realized_pnl_gbp=str(pnl / 2), intent_id="partial-one"
    )
    second = dict(
        fill, quantity=str(qty / 2), realized_pnl_gbp=str(pnl / 2), intent_id="partial-two"
    )
    base["fills"][index : index + 1] = [first, second]
    projected = reporting(base)
    assert projected["total_trades"] == 6
    assert sum(m["trades"] for m in projected["by_month"].values()) == 6
    assert sum(Decimal(m["realised_pnl_gbp"]) for m in projected["by_month"].values()) == Decimal(
        base["realised_pnl_gbp"]
    )
    assert original["side"] == "sell"


def test_historical_replay_dashboard_formatter_and_mock_delivery_restart(tmp_path, monkeypatch):
    ref = build(tmp_path)[0]
    d = prepare(ref, allow_artificial=True)
    data = replay_dataset(d)
    history = HistoryStore(tmp_path / "activity.sqlite3")
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    args = dict(
        costs=d["costs"],
        orb=ref.config.orb,
        policy=d["policy"],
        store=history,
        calendar=d["calendar"],
        ignore_predecision_quotes=True,
        notify=True,
    )
    result, identity = recorded_backtest(data, ref.config.settings, **args)
    assert result.simulated_trades == 1 and result.reconciled
    password = "isolated-fictional-dashboard-password"
    DashboardAuth(history).set_password(password)
    app = create_app(history)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/portfolio").status_code == 401
        assert (
            client.post(
                "/auth/login",
                json={"password": password},
                headers={"Origin": "http://127.0.0.1:8000"},
            ).status_code
            == 200
        )
        summary = client.get("/api/portfolio").json()["data"]
        assert Decimal(summary["equity_gbp"]) == result.ending_equity_gbp
        assert Decimal(summary["realized_pnl_gbp"]) == result.net_pnl_gbp
        assert client.get("/api/trades").json()["data"]["total"] == 2
        assert client.get("/api/equity").json()["data"]["points"]
        assert client.get("/api/risk").json()["data"]["kill_switch"] is False
        assert (
            client.post(
                "/api/positions", json={}, headers={"Origin": "http://127.0.0.1:8000"}
            ).status_code
            == 405
        )
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(payloads)}})

    provider = TelegramProvider(
        TelegramSettings(True, "123456:" + "x" * 32, "123", True),
        transport=httpx.MockTransport(handler),
    )
    from datetime import timedelta

    for i in range(10):
        deliver_one(history, provider, enabled=True, now=now_utc() + timedelta(seconds=i * 2))
    assert len(payloads) == 2
    assert all(p["parse_mode"] == "HTML" for p in payloads)
    assert any("P/L" in p["text"] or "P&amp;L" in p["text"] for p in payloads)
    args["store"] = HistoryStore(history.path)
    assert recorded_backtest(data, ref.config.settings, **args)[1] == identity
    deliver_one(args["store"], provider, enabled=True, now=now_utc() + timedelta(seconds=30))
    assert len(payloads) == 2
    with history.connect() as db:
        rows = db.execute(
            "SELECT content FROM events WHERE event_type=?", (EventType.FILL.value,)
        ).fetchall()
    event = ActivityEvent.model_validate_json(rows[-1][0])
    assert [p["text"] for p in payloads] == [
        format_message(ActivityEvent.model_validate_json(row[0])) for row in rows
    ]
    assert Decimal(event.payload["realized_pnl_gbp"]) == result.net_pnl_gbp
    assert render_card(event, fictional=True).startswith(b"\x89PNG")


def test_diagnostics_cli_reports_walk_forward_and_explicit_assumptions(tmp_path, capsys):
    from atlas.research_history_cli import main

    refs = build(tmp_path)
    config = json.loads(
        (Path(__file__).parents[1] / "examples/historical-backtest.assumed.json").read_text()
    )
    config["evaluation"] = {"role": "IN_SAMPLE", "parameter_selection_cutoff": None}
    (tmp_path / "config.json").write_text(json.dumps(config))
    bundle = {
        "schema_version": 1,
        "configuration": "config.json",
        "fx_policy": "FIXED_ASSUMED_PER_SESSION",
        "datasets": [{"database": r.database, "dataset_id": r.dataset_id} for r in refs],
        "walk_forward": {
            "training_sessions": 1,
            "validation_sessions": 1,
            "test_sessions": 1,
            "step_sessions": 1,
        },
    }
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    output = tmp_path / "diagnostics.json"
    assert (
        main(
            [
                "diagnose",
                "--bundle",
                str(tmp_path / "bundle.json"),
                "--output",
                str(output),
                "--allow-artificial",
            ]
        )
        == 0
    )
    value = json.loads(output.read_text())
    assert value["reporting"]["total_trades"] == 6
    assert value["reporting"]["availability_modes"] == ["ASSUMED"]
    assert not value["reporting"]["point_in_time_verified"]
    assert len(value["walk_forward"]["folds"]) == 1
    assert value["shared_portfolio"]["scope"] == "ACTUAL_SHARED_ACCOUNT_REPLAY"
    assert value["shared_portfolio"]["risk_decisions"]
    assert json.loads(capsys.readouterr().out)["network_enabled"] is False
