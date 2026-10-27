from dataclasses import replace
from decimal import Decimal

import pytest

from atlas.activity import EventType
from atlas.dashboard_security import DashboardAuth
from atlas.history import SCHEMA_VERSION, HistoryStore
from atlas.main import main
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest, valuation
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs


def test_research_cli_persists_once_without_changing_report_json(tmp_path, capsys):
    path = tmp_path / "cli.sqlite3"
    args = ["synthetic-backtest", "--history-db", str(path), "--json"]
    assert main(args) == 0
    first = capsys.readouterr().out
    assert main(args) == 0
    assert capsys.readouterr().out == first
    assert HistoryStore(path).runs()[1] == 1
    assert main([*args, "--new-run"]) == 0
    assert HistoryStore(path).runs()[1] == 2


def test_no_persist_escape_hatch_does_not_create_database(tmp_path):
    path = tmp_path / "unused.sqlite3"
    assert main(["synthetic-backtest", "--no-persist", "--history-db", str(path)]) == 0
    assert not path.exists()
    assert main(["synthetic-backtest", "--no-persist", "--notify-synthetic"]) == 1


def test_dashboard_setup_requires_local_terminal_then_password_and_invalidates_sessions(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "setup.sqlite3"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["dashboard-setup", "--history-db", str(path)]) == 1
    assert "interactive" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    password = "local-test-" + "password-value"
    monkeypatch.setattr("getpass.getpass", lambda prompt: password)
    assert main(["dashboard-setup", "--history-db", str(path)]) == 0
    assert password not in capsys.readouterr().out
    assert DashboardAuth(HistoryStore(path)).configured()


def test_dashboard_check_and_backup_are_offline_and_do_not_start_senders(tmp_path, capsys):
    path = tmp_path / "history.sqlite3"
    assert main(["dashboard-check", "--history-db", str(path)]) == 0
    assert "setup required" in capsys.readouterr().out
    assert main(["dashboard", "--history-db", str(path)]) == 1
    assert (
        main(
            [
                "history-backup",
                "--history-db",
                str(path),
                "--backup-path",
                str(tmp_path / "backup.sqlite3"),
            ]
        )
        == 0
    )


def test_schema_one_migrates_atomically_without_losing_research_history(tmp_path):
    path = tmp_path / "old.sqlite3"
    store = HistoryStore(path)
    _, run_id = recorded_backtest(
        generate(),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
    )
    count = store.events(run_id)[1]
    with store.connect(write=True) as db:
        db.execute("DROP TABLE notification_configuration")
        db.execute("PRAGMA user_version=1")
    upgraded = HistoryStore(path)
    assert upgraded.events(run_id)[1] == count
    with upgraded.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert upgraded.notification_status()["last_sender_configuration"] is None


def test_gap_loss_above_planned_risk_is_explicitly_highlighted(tmp_path):
    store = HistoryStore(tmp_path / "gap.sqlite3")
    _, run_id = recorded_backtest(
        generate("gap"),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
    )
    alerts, _ = store.events(run_id, kinds=(EventType.BREACH,))
    gap = next(a for a in alerts if a["payload"]["code"] == "LOSS_EXCEEDED_PLANNED_RISK")
    assert Decimal(gap["payload"]["actual_loss_gbp"]) > Decimal(gap["payload"]["planned_risk_gbp"])


def test_missing_fx_for_native_position_display_is_na_without_invented_conversion(context):
    data = context.data
    observations = []
    from atlas.backtest import run_backtest

    run_backtest(data, observer=lambda kind, snapshot, value, kill: observations.append(snapshot))
    snapshot = next(s for s in observations if s.positions)
    displayed = valuation(snapshot, replace(data, fx=None), False)
    assert displayed["positions"][0]["mark_native"] is None
    assert displayed["positions"][0]["average_entry_native"] is None
    assert displayed["positions"][0]["unavailable_reason"]


def test_position_values_cannot_disagree_with_a_reconciled_summary(context):
    from pydantic import ValidationError

    from atlas.activity import Valuation
    from atlas.backtest import run_backtest

    snapshots = []
    run_backtest(
        context.data, observer=lambda kind, snapshot, value, kill: snapshots.append(snapshot)
    )
    snapshot = next(s for s in snapshots if s.positions)
    payload = valuation(snapshot, context.data, False)
    payload["positions"][0]["market_value_gbp"] = str(
        Decimal(payload["positions"][0]["market_value_gbp"]) + 1
    )
    with pytest.raises(ValidationError, match="Positions do not reconcile"):
        Valuation.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [("quantity", "NaN"), ("price", "Infinity"), ("fees_gbp", "NaN"), ("quantity", "0")],
)
def test_invalid_native_price_or_nan_activity_values_are_rejected(tmp_path, field, value):
    from pydantic import ValidationError

    from atlas.activity import FillPayload

    store = HistoryStore(tmp_path / "validation.sqlite3")
    _, run_id = recorded_backtest(
        generate(),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
    )
    payload = store.events(run_id, kinds=(EventType.FILL,), limit=1)[0][0]["payload"]
    payload[field] = value
    with pytest.raises(ValidationError) as error:
        FillPayload.model_validate(payload)
    assert error.value.errors()[0]["loc"] == (field,)
