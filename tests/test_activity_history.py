from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from atlas.activity import ActivityEvent, Environment, EventType, now_utc
from atlas.backtest import run_backtest
from atlas.config import AtlasError
from atlas.history import SCHEMA_VERSION, HistoryStore
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "events.sqlite3")


def record(store, scenario="orb", **kwargs):
    return recorded_backtest(
        generate(scenario),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
        **kwargs,
    )


def test_schema_idempotent_restart_and_consistent_persisted_snapshots(store):
    report, run_id = record(store)
    reopened = HistoryStore(store.path)
    assert reopened.run(run_id)["report"] == report.to_dict()
    before = reopened.events(run_id)[1]
    repeated, same_id = record(reopened)
    assert same_id == run_id and repeated == report
    assert reopened.events(run_id)[1] == before
    snapshots, _ = reopened.events(
        run_id, kinds=(EventType.VALUATION,), limit=1000, descending=False
    )
    assert len(snapshots) > 10
    for event in snapshots:
        payload = event["payload"]
        assert Decimal(payload["equity_gbp"]) == (
            Decimal(payload["initial_capital_gbp"])
            + Decimal(payload["realized_pnl_gbp"])
            + Decimal(payload["unrealized_pnl_gbp"])
        )
    with reopened.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize(
    "scenario", ["orb", "gap", "partial_fill", "no_fill", "sideways", "time_exit"]
)
def test_observation_adapter_does_not_change_replay_and_separates_runs(store, scenario):
    report, first = record(store, scenario)
    assert report == run_backtest(generate(scenario))
    independent, second = record(store, scenario, new_run=True)
    assert second != first and independent == report
    assert Decimal(store.run(second)["report"]["starting_capital_gbp"]) == 1000
    fills, _ = store.events(first, kinds=(EventType.FILL, EventType.PARTIAL), limit=1000)
    assert len(fills) == len(report.fills)
    assert all(event["payload"]["execution_status"] == "SIMULATED_CONFIRMED" for event in fills)


def test_confirmed_partial_fill_is_not_a_signal_or_unfilled_intent(store):
    _, run_id = record(store, "partial_fill")
    entries, _ = store.events(run_id, kinds=(EventType.PARTIAL,))
    assert len(entries) == 1 and Decimal(entries[0]["payload"]["quantity"]) == 1
    assert entries[0]["payload"]["side"] == "buy"
    assert store.notification_status()["counts"] == {}


def test_interrupted_transaction_rolls_back_all_events(store):
    run_id = "a" * 64
    store.claim_run(run_id, {}, "owner")
    event = ActivityEvent.create(
        run_id=run_id,
        event_type=EventType.ERROR,
        ordinal=0,
        at=now_utc(),
        payload={"code": "SYNTHETIC_FAILURE"},
    )
    with pytest.raises(RuntimeError):
        with store.connect(write=True) as db:
            store.append(db, event)
            raise RuntimeError("interrupted")
    assert store.events(run_id)[1] == 0


def test_duplicate_event_is_idempotent_and_conflicting_content_is_rejected(store):
    run_id = "b" * 64
    store.claim_run(run_id, {}, "owner")
    event = ActivityEvent.create(
        run_id=run_id,
        event_type=EventType.ERROR,
        ordinal=0,
        at=now_utc(),
        payload={"code": "ERROR_ONE"},
    )
    with store.connect(write=True) as db:
        assert store.append(db, event)
        assert not store.append(db, event)
    other = event.model_copy(update={"payload": {"code": "ERROR_TWO"}})
    with pytest.raises(AtlasError, match="identity conflict"), store.connect(write=True) as db:
        store.append(db, other)
    assert store.events(run_id)[1] == 1


def test_future_schema_is_not_silently_downgraded(store):
    with store.connect(write=True) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(AtlasError, match="newer"):
        HistoryStore(store.path)


def test_active_lease_blocks_duplicate_worker_but_expired_lease_can_restart(store):
    now = now_utc()
    run_id = "c" * 64
    assert store.claim_run(run_id, {}, "first", now)
    with pytest.raises(AtlasError, match="already active"):
        store.claim_run(run_id, {}, "second", now)
    assert store.claim_run(run_id, {}, "second", now + timedelta(seconds=31))
    with pytest.raises(AtlasError, match="lease lost"):
        store.heartbeat(run_id, "first")


def test_event_causality_and_supported_environment_fail_closed(store):
    run_id = "d" * 64
    store.claim_run(run_id, {}, "owner")
    event = ActivityEvent.create(
        run_id=run_id,
        event_type=EventType.ERROR,
        ordinal=0,
        at=now_utc(),
        cause="f" * 64,
        payload={"code": "ERROR"},
    )
    with pytest.raises(AtlasError, match="cause"), store.connect(write=True) as db:
        store.append(db, event)
    event = event.model_copy(update={"causation_id": None, "environment": Environment.LIVE})
    with pytest.raises(AtlasError, match="SYNTHETIC"), store.connect(write=True) as db:
        store.append(db, event)


@pytest.mark.parametrize("key", ["password", "api_key", "bot_token", "chat_id", "account_id"])
def test_private_fields_cannot_enter_run_metadata(store, key):
    with pytest.raises(AtlasError, match="Private configuration"):
        store.claim_run("e" * 64, {key: "private-placeholder"}, "owner")


def test_payload_schema_rejects_unknown_fields_and_naive_time():
    with pytest.raises(ValidationError):
        ActivityEvent.create(
            run_id="a" * 64,
            event_type=EventType.ERROR,
            ordinal=0,
            at=now_utc(),
            payload={"code": "ERROR", "api_secret": "placeholder"},
        )
    with pytest.raises((ValidationError, AtlasError)):
        ActivityEvent.create(
            run_id="a" * 64,
            event_type=EventType.ERROR,
            ordinal=0,
            at=now_utc().replace(tzinfo=None),
            payload={"code": "ERROR"},
        )


def test_online_backup_is_consistent_and_does_not_overwrite(store, tmp_path):
    _, run_id = record(store)
    destination = tmp_path / "backup.sqlite3"
    store.backup(destination)
    assert HistoryStore(destination).run(run_id) == store.run(run_id)
    with pytest.raises(AtlasError, match="new destination"):
        store.backup(destination)


def test_history_reads_during_writes_and_causal_sequence_is_preserved(store):
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = pool.submit(record, store)
        store.runs()
        _, run_id = result.result()
    events, _ = store.events(run_id, limit=1000, descending=False)
    previous = None
    for event in events:
        assert event["causation_id"] == previous
        previous = event["event_id"]
    assert [event["event_at"] for event in events] == sorted(event["event_at"] for event in events)


def test_failed_import_does_not_commit_partial_portfolio_history(store, monkeypatch):
    def interrupted(*args, **kwargs):
        raise OSError("private exception detail must not be printed")

    monkeypatch.setattr(store, "complete_run", interrupted)
    with pytest.raises(AtlasError, match="not committed") as error:
        record(store)
    assert "private exception" not in str(error.value)
    run = store.run()
    assert run["status"] == "ERROR" and run["report"] is None
    events, _ = store.events(run["run_id"])
    assert [x["event_type"] for x in events] == [EventType.ERROR]
