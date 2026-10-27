"""Acceptance invariants for the run-ownership boundary; no real network calls."""

from datetime import timedelta

import pytest

from atlas.activity import EventType, now_utc
from atlas.config import AtlasError
from atlas.history import HistoryStore
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs


def execute(store, **kwargs):
    return recorded_backtest(
        generate(),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
        **kwargs,
    )


def test_replaced_worker_cannot_publish_failure_or_enqueue_alerts_for_current_owner(
    tmp_path, monkeypatch
):
    """An actual observer lease loss must not be reported as the successor's failure."""
    store = HistoryStore(tmp_path / "takeover.sqlite3")
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    heartbeat = store.heartbeat
    replaced = []

    def takeover(run_id, old_owner):
        # Simulate a paused worker's expired lease and a successful restart/takeover.
        with store.connect(write=True) as db:
            db.execute(
                "UPDATE runs SET lease_until=? WHERE run_id=?",
                ((now_utc() - timedelta(seconds=1)).isoformat(), run_id),
            )
        metadata = store.run(run_id)["metadata"]
        assert store.claim_run(run_id, metadata, "replacement-worker")
        replaced.append(run_id)
        heartbeat(run_id, old_owner)

    monkeypatch.setattr(store, "heartbeat", takeover)
    with pytest.raises(AtlasError):
        execute(store, notify=True)

    run_id = replaced[0]
    current = store.run(run_id)
    assert current["status"] == "RUNNING" and current["owner"] == "replacement-worker"
    assert store.events(run_id, kinds=(EventType.ERROR,))[1] == 0
    assert store.notification_status()["counts"] == {}


def test_late_failure_callback_cannot_contaminate_a_completed_run(tmp_path, monkeypatch):
    """The replacement can finish before the obsolete worker reports lease loss."""
    store = HistoryStore(tmp_path / "completed.sqlite3")
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    heartbeat = store.heartbeat
    completed = []

    def finish_replacement(run_id, old_owner):
        with store.connect(write=True) as db:
            db.execute(
                "UPDATE runs SET lease_until=? WHERE run_id=?",
                ((now_utc() - timedelta(seconds=1)).isoformat(), run_id),
            )
        monkeypatch.setattr(store, "heartbeat", heartbeat)
        report, same_id = execute(store)
        assert same_id == run_id
        completed.append((report, run_id, store.events(run_id)[1]))
        heartbeat(run_id, old_owner)

    monkeypatch.setattr(store, "heartbeat", finish_replacement)
    with pytest.raises(AtlasError):
        execute(store, notify=True)
    report, run_id, before = completed[0]
    assert store.run(run_id)["status"] == "COMPLETED"
    assert store.run(run_id)["report"] == report.to_dict()
    assert store.events(run_id)[1] == before
    assert store.notification_status()["counts"] == {}
