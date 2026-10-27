"""Research attempt fencing: isolated SQLite files, controlled clocks, no network."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest

from atlas.activity import ActivityEvent, EventType
from atlas.config import AtlasError
from atlas.history import SCHEMA_VERSION, HistoryStore
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs

RUN_ID = "a" * 64


@pytest.fixture
def clock(monkeypatch):
    value = [datetime(2026, 10, 27, 12, tzinfo=UTC)]
    monkeypatch.setattr("atlas.history.now_utc", lambda: value[0])
    return value


@pytest.fixture
def store(tmp_path, clock):
    result = HistoryStore(tmp_path / "ownership.sqlite3")
    assert result.claim_run(RUN_ID, {}, "attempt-A")
    return result


def completion(at, run_id=RUN_ID):
    return ActivityEvent.create(
        run_id=run_id,
        event_type=EventType.RUN_COMPLETED,
        ordinal="complete",
        at=at,
        payload={
            "dataset": "artificial-transaction-test",
            "venue": "XLON",
            "dataset_checksum": "b" * 64,
            "configuration_checksum": "c" * 64,
            "initial_capital_gbp": "1000",
        },
    )


def finish(store, owner, at):
    store.complete_run(
        RUN_ID, owner, [completion(at)], {"test": "complete"}, notify=True, notify_summary=True
    )


def assert_pristine(store, owner="attempt-A", status="RUNNING"):
    run = store.run(RUN_ID)
    assert (run["status"], run["owner"], run["report"]) == (status, owner, None)
    assert store.events(RUN_ID)[1] == 0
    assert store.notification_status()["counts"] == {}


def test_current_owner_failure_is_recorded_once_even_after_reopen(store, clock):
    assert store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    reopened = HistoryStore(store.path)
    assert not reopened.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    assert reopened.run(RUN_ID)["status"] == "ERROR"
    assert reopened.run(RUN_ID)["error_code"] == "RESEARCH_FAILED"
    events, count = reopened.events(RUN_ID)
    assert count == 1 and events[0]["payload"]["code"] == "RESEARCH_FAILED"
    assert reopened.notification_status()["counts"] == {"PENDING": 1}
    with pytest.raises(AtlasError, match="lease lost"):
        finish(reopened, "attempt-A", clock[0])
    assert reopened.events(RUN_ID)[1] == 1


@pytest.mark.parametrize("successor_completed", [False, True])
def test_stale_completion_and_failure_have_no_effect_after_transfer(
    store, clock, successor_completed
):
    clock[0] += timedelta(seconds=31)
    restarted = HistoryStore(store.path)
    assert restarted.claim_run(RUN_ID, {}, "attempt-B")
    if successor_completed:
        finish(restarted, "attempt-B", clock[0])
    before = (restarted.run(RUN_ID), restarted.events(RUN_ID), restarted.notification_status())
    with pytest.raises(AtlasError, match="lease lost"):
        finish(store, "attempt-A", clock[0])
    assert not store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    with pytest.raises(AtlasError, match="lease lost"):
        store.heartbeat(RUN_ID, "attempt-A")
    assert before == (
        restarted.run(RUN_ID),
        restarted.events(RUN_ID),
        restarted.notification_status(),
    )


@pytest.mark.parametrize("seconds", [30, 31])
def test_expired_owner_cannot_renew_or_write_terminal_events(store, clock, seconds):
    clock[0] += timedelta(seconds=seconds)
    with pytest.raises(AtlasError, match="lease lost"):
        store.heartbeat(RUN_ID, "attempt-A")
    with pytest.raises(AtlasError, match="lease lost"):
        finish(store, "attempt-A", clock[0])
    assert not store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    assert_pristine(store, status="INTERRUPTED")


def test_attempt_identity_cannot_be_reused_across_restart_or_failure(store, clock):
    clock[0] += timedelta(seconds=31)
    restarted = HistoryStore(store.path)
    with pytest.raises(AtlasError, match="already used"):
        restarted.claim_run(RUN_ID, {}, "attempt-A")
    assert restarted.claim_run(RUN_ID, {}, "attempt-B")
    assert restarted.fail_run(RUN_ID, "attempt-B", clock[0])
    with pytest.raises(AtlasError, match="already used"):
        HistoryStore(store.path).claim_run(RUN_ID, {}, "attempt-A")
    with pytest.raises(AtlasError, match="already used"):
        restarted.claim_run(RUN_ID, {}, "attempt-B")
    assert restarted.claim_run(RUN_ID, {}, "attempt-C")
    assert not store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    assert restarted.run(RUN_ID)["owner"] == "attempt-C"
    assert restarted.events(RUN_ID)[1] == 1  # Only B's genuine earlier failure.
    assert restarted.notification_status()["counts"] == {}


@pytest.mark.parametrize("operation", ["heartbeat", "complete", "fail"])
def test_lease_time_is_sampled_after_acquiring_write_transaction(
    store, clock, monkeypatch, operation
):
    connect = store.connect

    @contextmanager
    def delayed_connect(**kwargs):
        with connect(**kwargs) as db:
            if kwargs.get("write"):
                clock[0] += timedelta(seconds=31)
            yield db

    monkeypatch.setattr(store, "connect", delayed_connect)
    if operation == "fail":
        assert not store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    else:
        with pytest.raises(AtlasError, match="lease lost"):
            if operation == "complete":
                finish(store, "attempt-A", clock[0])
            else:
                store.heartbeat(RUN_ID, "attempt-A")
    assert_pristine(store, status="INTERRUPTED")


@pytest.mark.parametrize("terminal", ["complete", "fail"])
@pytest.mark.parametrize("failure_stage", ["outbox", "status"])
def test_terminal_event_outbox_and_status_roll_back_together(store, clock, terminal, failure_stage):
    with store.connect(write=True) as db:
        if failure_stage == "outbox":
            db.execute("""CREATE TRIGGER interrupt_terminal BEFORE INSERT ON outbox
                BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END""")
        else:
            db.execute("""CREATE TRIGGER interrupt_terminal BEFORE UPDATE OF status ON runs
                BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END""")

    def attempt():
        if terminal == "complete":
            return finish(store, "attempt-A", clock[0])
        return store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)

    with pytest.raises(AtlasError, match="database operation failed"):
        attempt()
    assert_pristine(store)
    with store.connect(write=True) as db:
        db.execute("DROP TRIGGER interrupt_terminal")
    attempt()
    assert store.run(RUN_ID)["status"] == ("COMPLETED" if terminal == "complete" else "ERROR")
    assert store.events(RUN_ID)[1] == 1
    assert store.notification_status()["counts"] == {"PENDING": 1}


@pytest.mark.parametrize(
    "kinds", [("complete", "fail"), ("fail", "fail"), ("complete", "complete")]
)
def test_concurrent_terminal_callbacks_commit_exactly_one_outcome(store, clock, kinds):
    # Each thread opens its own SQLite connection; the barrier schedules simultaneous attempts.
    second = HistoryStore(store.path)
    barrier = Barrier(2)

    def attempt(target, kind):
        barrier.wait(timeout=5)
        if kind == "fail":
            return target.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
        try:
            finish(target, "attempt-A", clock[0])
            return True
        except AtlasError as error:
            assert "lease lost" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, store, kinds[0])
        other = pool.submit(attempt, second, kinds[1])
        assert sum([first.result(timeout=10), other.result(timeout=10)]) == 1
    run = store.run(RUN_ID)
    events, count = store.events(RUN_ID)
    assert count == 1 and store.notification_status()["counts"] == {"PENDING": 1}
    if run["status"] == "COMPLETED":
        assert events[0]["event_type"] == EventType.RUN_COMPLETED
        assert run["report"] == {"test": "complete"}
    else:
        assert run["status"] == "ERROR" and run["report"] is None
        assert events[0]["event_type"] == EventType.ERROR


def test_concurrent_takeover_has_only_one_new_owner(store, clock):
    clock[0] += timedelta(seconds=31)
    second = HistoryStore(store.path)
    barrier = Barrier(2)

    def claim(target, owner):
        barrier.wait(timeout=5)
        try:
            return target.claim_run(RUN_ID, {}, owner)
        except AtlasError as error:
            assert "already active" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim, store, "attempt-B")
        other = pool.submit(claim, second, "attempt-C")
        assert sum([first.result(timeout=10), other.result(timeout=10)]) == 1
    assert not store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    assert store.run(RUN_ID)["owner"] in ("attempt-B", "attempt-C")
    assert store.events(RUN_ID)[1] == 0
    assert store.notification_status()["counts"] == {}


def test_schema_two_migration_preserves_events_outbox_and_fences_active_owner(store, clock):
    assert store.fail_run(RUN_ID, "attempt-A", clock[0], notify=True)
    assert store.claim_run(RUN_ID, {}, "attempt-B")
    before = (store.run(RUN_ID), store.events(RUN_ID), store.notification_status())
    with store.connect(write=True) as db:
        db.execute("DROP TABLE run_attempts")
        db.execute("PRAGMA user_version=2")
    migrated = HistoryStore(store.path)
    assert before == (migrated.run(RUN_ID), migrated.events(RUN_ID), migrated.notification_status())
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT owner FROM run_attempts").fetchone()[0] == "attempt-B"
    migrated.heartbeat(RUN_ID, "attempt-B")
    clock[0] += timedelta(seconds=31)
    with pytest.raises(AtlasError, match="already used"):
        HistoryStore(store.path).claim_run(RUN_ID, {}, "attempt-B")
    assert migrated.claim_run(RUN_ID, {}, "attempt-C")


def test_current_owner_cannot_commit_another_runs_event(store, clock):
    assert store.claim_run("d" * 64, {}, "other-attempt")
    with pytest.raises(AtlasError, match="cross-run"):
        store.complete_run(RUN_ID, "attempt-A", [completion(clock[0], "d" * 64)], {})
    assert_pristine(store)
    assert store.events("d" * 64)[1] == 0


@pytest.mark.parametrize("kind", ["missing", "duplicate", "conflicting"])
def test_completion_batch_cannot_contain_invalid_terminal_events(store, clock, kind):
    events = [completion(clock[0])]
    if kind == "missing":
        events = []
    elif kind == "duplicate":
        events.append(
            ActivityEvent.create(
                run_id=RUN_ID,
                event_type=EventType.RUN_COMPLETED,
                ordinal="second-completion",
                at=clock[0],
                payload=events[0].payload,
            )
        )
    else:
        events.append(
            ActivityEvent.create(
                run_id=RUN_ID,
                event_type=EventType.ERROR,
                ordinal="contradictory-failure",
                at=clock[0],
                payload={"code": "RESEARCH_FAILED"},
            )
        )
    with pytest.raises(AtlasError, match="consistent terminal"):
        store.complete_run(RUN_ID, "attempt-A", events, {}, notify=True, notify_summary=True)
    assert_pristine(store)


def test_completed_real_portfolio_is_unchanged_by_late_terminal_callbacks(store, clock):
    report, run_id = recorded_backtest(
        generate("partial_fill"),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
    )
    with store.connect() as db:
        owner = db.execute("SELECT owner FROM run_attempts WHERE run_id=?", (run_id,)).fetchone()[0]
    before = (store.run(run_id), store.events(run_id, limit=1000))
    restarted = HistoryStore(store.path)
    assert not restarted.fail_run(run_id, owner, clock[0], notify=True)
    with pytest.raises(AtlasError, match="lease lost"):
        restarted.complete_run(run_id, owner, [], {}, notify=True)
    assert before == (restarted.run(run_id), restarted.events(run_id, limit=1000))
    assert restarted.run(run_id)["report"] == report.to_dict()
    valuations, _ = restarted.events(run_id, kinds=(EventType.VALUATION,), limit=1000)
    for event in valuations:
        payload = event["payload"]
        assert Decimal(payload["cash_gbp"]) >= 0
        assert Decimal(payload["equity_gbp"]) == (
            Decimal(payload["cash_gbp"]) + Decimal(payload["invested_value_gbp"])
        )
        assert Decimal(payload["equity_gbp"]) == (
            Decimal("1000")
            + Decimal(payload["realized_pnl_gbp"])
            + Decimal(payload["unrealized_pnl_gbp"])
        )
    assert restarted.notification_status()["counts"] == {}
