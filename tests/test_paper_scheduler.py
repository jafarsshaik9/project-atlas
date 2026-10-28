"""Session boundaries, crash recovery, atomic checkpoints and worker fencing."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from threading import Barrier

import pytest

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.paper_data.contracts import FreshnessPolicy, Observation
from atlas.paper_data.mock import (
    SCENARIOS,
    EventKind,
    MockCurrentProvider,
    MockEvent,
    mock_config,
    mock_entitlement,
    mock_script,
)
from atlas.paper_data.scheduler import SessionScheduler
from atlas.paper_data.store import Lease, OwnershipLost, SchedulerStore

AT = datetime(2026, 10, 28, 14, tzinfo=UTC)


@pytest.fixture(scope="module")
def calendar():
    return HistoricalCalendar(Venue.NASDAQ, AT.date(), AT.date() + timedelta(days=1))


@pytest.fixture
def runtime(tmp_path, calendar):
    clock = [AT]
    store = SchedulerStore(tmp_path / "paper.sqlite3", clock=lambda: clock[0])
    config = mock_config()

    def scheduler(scenario="ordered", provider=None, policy=None, selected_store=None):
        return SessionScheduler(
            selected_store or store,
            "test-stream",
            config,
            calendar,
            provider or mock_script(config, AT, scenario),
            mock_entitlement(config, AT),
            policy,
        )

    return clock, store, scheduler


def play(clock, store, scheduler):
    lease = store.claim("test-stream", "worker-1")
    outcomes = []
    for event in scheduler.provider.events:
        clock[0] = event.at
        outcomes.append(scheduler.scan(lease))
    return lease, outcomes


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s != "delayed"])
def test_mock_scenarios_are_deterministic_and_cursor_replayable(scenario):
    first = mock_script(mock_config(), AT, scenario)
    second = mock_script(mock_config(), AT, scenario)
    assert first.checksum == second.checksum
    for cursor, event in enumerate(first.events):
        assert first.poll(cursor, event.at) == second.poll(cursor, event.at) == event
        assert first.poll(cursor, event.at - timedelta(microseconds=1)) is None
    assert first.poll(len(first.events), AT + timedelta(days=1)) is None
    with pytest.raises(AtlasError):
        first.poll(-1, AT)


@pytest.mark.parametrize(
    ("scenario", "accepted", "reason"),
    [
        ("ordered", 2, None),
        ("duplicate", 2, "DUPLICATE_OBSERVATION"),
        ("out-of-order", 1, "OUT_OF_ORDER"),
        ("missing", 2, "MISSING"),
        ("halt", 2, "MARKET_HALTED"),
        ("connection-failure", 2, "PROVIDER_DISCONNECTED"),
        ("provider-restart", 2, None),
        ("session-transition", 1, "SESSION_TRANSITION"),
        ("stale", 0, "STALE_EVENT"),
        ("missing-availability", 0, "PROVIDER_AVAILABILITY_MISSING"),
        ("unknown-delay", 0, "FEED_DELAY_UNKNOWN_OR_MISMATCH"),
    ],
)
def test_fault_scripts_persist_results_and_never_execute(runtime, scenario, accepted, reason):
    clock, store, factory = runtime
    scheduler = factory(scenario)
    _, outcomes = play(clock, store, scheduler)
    snapshot = store.snapshot("test-stream")
    assert snapshot["cursor"] == len(scheduler.provider.events)
    assert snapshot["counts"].get("ELIGIBLE", 0) == accepted
    if reason:
        assert any(reason in result["reasons"] for result in outcomes)
    for result in outcomes:
        if "assessment" in result:
            assert result["assessment"]["eligible"] == (result["outcome"] == "ELIGIBLE")
            assert result["assessment"]["mode"] == result["mode"]
            assert result["entitlement_response"]["origin"] == "MOCK_RESPONSE"


@pytest.mark.parametrize("venue", [Venue.NYSE, Venue.NASDAQ])
@pytest.mark.parametrize(
    ("day", "opened", "closed", "half_day"),
    [
        (date(2026, 3, 6), 14, 21, False),
        (date(2026, 3, 9), 13, 20, False),
        (date(2026, 10, 30), 13, 20, False),
        (date(2026, 11, 2), 14, 21, False),
        (date(2026, 11, 27), 14, 18, True),
        (date(2026, 12, 24), 14, 18, True),
    ],
)
def test_maintained_calendar_dst_and_early_closes(venue, day, opened, closed, half_day):
    calendar = HistoricalCalendar(venue, day, day)
    session = calendar.session(venue, day)
    assert (session.open.hour, session.open.minute) == (opened, 30)
    assert session.close.hour == closed
    assert session.half_day == half_day
    assert session.contains(session.open)
    assert not session.contains(session.close)


@pytest.mark.parametrize("venue", [Venue.NYSE, Venue.NASDAQ])
@pytest.mark.parametrize(
    "day", [date(2026, 1, 1), date(2026, 7, 3), date(2026, 11, 26), date(2026, 10, 31)]
)
def test_no_polling_on_holidays_or_weekends(tmp_path, venue, day, monkeypatch):
    at = datetime.combine(day, datetime.min.time(), UTC).replace(hour=15)
    config = mock_config(venue)
    store = SchedulerStore(tmp_path / "paper.sqlite3", clock=lambda: at)
    provider = mock_script(config, at)
    scheduler = SessionScheduler(
        store,
        "s",
        config,
        HistoricalCalendar(venue, day, day),
        provider,
        mock_entitlement(config, at),
    )
    lease = store.claim("s", "w")
    monkeypatch.setattr(provider, "poll", lambda *_: pytest.fail("Polled outside a session"))
    assert scheduler.scan(lease)["outcome"] == "WAITING_SESSION"
    assert store.snapshot("s")["cursor"] == 0


@pytest.mark.parametrize("offset", [-1, 0, 1, 23400])
def test_exact_open_close_and_cadence(runtime, offset):
    clock, store, factory = runtime
    opened = AT.replace(hour=13, minute=30)
    clock[0] = opened + timedelta(seconds=offset)
    config = mock_config()
    provider = mock_script(config, opened)
    scheduler = factory(provider=provider)
    lease = store.claim("test-stream", "worker")
    result = scheduler.scan(lease)
    expected = "WAITING_SESSION" if offset in (-1, 23400) else "ELIGIBLE"
    assert result["outcome"] == expected
    if expected == "ELIGIBLE":
        assert scheduler.scan(lease)["outcome"] == "NOT_DUE"


def test_restart_continues_from_durable_cursor_with_new_fence(runtime):
    clock, store, factory = runtime
    first = factory("duplicate")
    a = store.claim("test-stream", "attempt-a")
    assert first.scan(a)["outcome"] == "ELIGIBLE"
    clock[0] += timedelta(seconds=30)
    reopened = SchedulerStore(store.path, clock=lambda: clock[0])
    restarted = factory("duplicate", selected_store=reopened)
    b = reopened.claim("test-stream", "attempt-b")
    assert b.fence == a.fence + 1
    result = restarted.scan(b)
    assert "DUPLICATE_OBSERVATION" in result["reasons"]
    assert reopened.snapshot("test-stream")["cursor"] == 2
    for operation in (lambda: first.scan(a), lambda: store.heartbeat(a), lambda: store.shutdown(a)):
        with pytest.raises(OwnershipLost):
            operation()
    assert reopened.snapshot("test-stream")["owner"] == "attempt-b"


@pytest.mark.parametrize("seconds", [30, 31])
@pytest.mark.parametrize("operation", ["scan", "heartbeat", "shutdown"])
def test_expired_workers_cannot_scan_renew_or_release(runtime, seconds, operation):
    clock, store, factory = runtime
    scheduler = factory()
    lease = store.claim("test-stream", "worker")
    clock[0] += timedelta(seconds=seconds)
    with pytest.raises(OwnershipLost):
        scheduler.scan(lease) if operation == "scan" else getattr(store, operation)(lease)
    assert store.snapshot("test-stream")["cursor"] == 0
    assert not store.snapshot("test-stream")["owner_active"]


def test_shutdown_is_durable_and_allows_immediate_new_attempt(runtime):
    _, store, factory = runtime
    scheduler = factory()
    a = store.claim("test-stream", "a")
    scheduler.scan(a)
    store.shutdown(a)
    snapshot = SchedulerStore(store.path, read_only=True).snapshot("test-stream")
    assert snapshot["status"] == "STOPPED" and snapshot["owner"] is None
    assert snapshot["cursor"] == 1
    b = store.claim("test-stream", "b")
    assert b.fence == a.fence + 1
    with pytest.raises(AtlasError, match="already used"):
        store.claim("test-stream", "a")
    with pytest.raises(OwnershipLost):
        store.shutdown(a)


def test_simultaneous_worker_claims_have_one_winner(runtime):
    clock, store, factory = runtime
    factory()
    stores = [SchedulerStore(store.path, clock=lambda: clock[0]) for _ in range(4)]
    barrier = Barrier(4)

    def claim(index):
        barrier.wait()
        return stores[index].claim("test-stream", f"worker-{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(claim, range(4)))
    assert sum(token is not None for token in tokens) == 1
    assert store.snapshot("test-stream")["fence"] == 1


def test_simultaneous_ticks_and_forged_fence_cannot_duplicate(runtime):
    _, store, factory = runtime
    scheduler = factory()
    lease = store.claim("test-stream", "worker")
    barrier = Barrier(2)

    def tick(_):
        barrier.wait()
        return scheduler.scan(lease)["outcome"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(tick, range(2))) == ["ELIGIBLE", "NOT_DUE"]
    with pytest.raises(OwnershipLost):
        scheduler.scan(Lease(lease.stream_id, lease.owner, lease.fence + 1))
    assert store.snapshot("test-stream")["counts"] == {"ELIGIBLE": 1}


@pytest.mark.parametrize("operation", ["scan", "heartbeat", "shutdown", "claim"])
def test_lease_clock_is_sampled_after_sqlite_write_lock(runtime, monkeypatch, operation):
    clock, store, factory = runtime
    scheduler = factory()
    lease = store.claim("test-stream", "a")
    original = store.connect

    @contextmanager
    def delayed(**kwargs):
        with original(**kwargs) as db:
            if kwargs.get("write"):
                clock[0] += timedelta(seconds=30)
            yield db

    monkeypatch.setattr(store, "connect", delayed)
    if operation == "claim":
        assert store.claim("test-stream", "b").fence == 2
    else:
        with pytest.raises(OwnershipLost):
            scheduler.scan(lease) if operation == "scan" else getattr(store, operation)(lease)
    assert store.snapshot("test-stream")["cursor"] == 0


@pytest.mark.parametrize("stage", ["result", "cursor"])
def test_injected_write_failure_rolls_back_seen_result_and_cursor(runtime, stage):
    _, store, factory = runtime
    scheduler = factory()
    lease = store.claim("test-stream", "w")
    with store.connect(write=True) as db:
        target = (
            "INSERT ON paper_results" if stage == "result" else "UPDATE OF cursor ON paper_streams"
        )
        db.execute(
            f"CREATE TRIGGER fail_scan BEFORE {target} BEGIN SELECT RAISE(ABORT,'fault'); END"
        )
    with pytest.raises(AtlasError, match="transaction failed"):
        scheduler.scan(lease)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_seen").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_results").fetchone()[0] == 0
    assert store.snapshot("test-stream")["cursor"] == 0
    with store.connect(write=True) as db:
        db.execute("DROP TRIGGER fail_scan")
    assert scheduler.scan(lease)["outcome"] == "ELIGIBLE"


def test_process_death_before_commit_recovers_without_lost_or_duplicate_data(runtime):
    clock, store, factory = runtime
    factory()
    script = """
import os, sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from atlas.domain import Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.paper_data.mock import mock_config, mock_script, mock_entitlement
from atlas.paper_data.scheduler import SessionScheduler
from atlas.paper_data.store import SchedulerStore
at = datetime(2026,10,28,14,tzinfo=UTC)
store = SchedulerStore(sys.argv[1], clock=lambda: at)
config = mock_config()
scheduler = SessionScheduler(store, 'test-stream', config,
    HistoricalCalendar(Venue.NASDAQ,at.date(),at.date()+timedelta(days=1)),
    mock_script(config,at),mock_entitlement(config,at))
lease = store.claim('test-stream','crashed-worker',timedelta(seconds=1))
connect = store.connect
@contextmanager
def crash_before_commit(**kwargs):
    with connect(**kwargs) as db:
        yield db
        if kwargs.get('write'):
            os._exit(73)
store.connect = crash_before_commit
scheduler.scan(lease)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(store.path)], capture_output=True, timeout=30
    )
    assert child.returncode == 73, child.stderr.decode()
    clock[0] += timedelta(seconds=1)
    reopened = SchedulerStore(store.path, clock=lambda: clock[0])
    snapshot = reopened.snapshot("test-stream")
    assert snapshot["cursor"] == 0 and snapshot["counts"] == {}
    assert not snapshot["owner_active"]
    scheduler = factory(selected_store=reopened)
    lease = reopened.claim("test-stream", "recovered-worker")
    assert lease.fence == 2
    assert scheduler.scan(lease)["outcome"] == "ELIGIBLE"
    clock[0] += timedelta(seconds=1)
    assert scheduler.scan(lease)["outcome"] == "ELIGIBLE"
    assert reopened.snapshot("test-stream")["counts"] == {"ELIGIBLE": 2}
    with reopened.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM paper_seen").fetchone()[0] == 2


def test_configuration_and_calendar_or_script_drift_cannot_reuse_checkpoint(runtime):
    _, _, factory = runtime
    factory()
    with pytest.raises(AtlasError, match="mismatch"):
        factory("duplicate")
    with pytest.raises(AtlasError, match="mismatch"):
        factory(policy=FreshnessPolicy(max_event_age_seconds=60))


def test_clock_regression_fails_closed_without_writes(runtime):
    clock, store, factory = runtime
    scheduler = factory()
    lease = store.claim("test-stream", "w")
    clock[0] -= timedelta(seconds=1)
    for operation in (
        lambda: scheduler.scan(lease),
        lambda: store.heartbeat(lease),
        lambda: store.claim("test-stream", "b"),
    ):
        with pytest.raises(AtlasError, match="regressed"):
            operation()
    assert store.snapshot("test-stream")["cursor"] == 0
    assert not store.snapshot("test-stream")["owner_active"]


def test_observation_id_collision_and_epoch_mismatch_are_fail_closed(runtime):
    clock, store, factory = runtime
    events = list(mock_script(mock_config(), AT).events)
    item = events[1].observation.model_dump()
    item["observation_id"] = events[0].observation.observation_id
    item["provider_epoch"] = "unannounced-process"
    events[1] = MockEvent(
        at=events[1].at,
        kind=EventKind.OBSERVATION,
        epoch="unannounced-process",
        observation=Observation(**item),
    )
    scheduler = factory(provider=MockCurrentProvider(tuple(events)))
    _, results = play(clock, store, scheduler)
    assert {"OBSERVATION_ID_CONFLICT", "PROVIDER_EPOCH_MISMATCH"} <= set(results[1]["reasons"])
    assert store.snapshot("test-stream")["counts"] == {"ELIGIBLE": 1, "REJECTED": 1}


def test_empty_feed_reports_no_data_and_no_watermark(runtime):
    _, store, factory = runtime
    scheduler = factory(provider=MockCurrentProvider(()))
    lease = store.claim("test-stream", "w")
    assert scheduler.scan(lease)["outcome"] == "NO_DATA"
    assert store.snapshot("test-stream")["last_event_at"] is None


def test_store_refuses_foreign_files_and_read_only_diagnostics_never_create(tmp_path):
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(AtlasError, match="migration refused"):
        SchedulerStore(path)
    assert path.read_bytes() == before
    absent = tmp_path / "absent.sqlite3"
    with pytest.raises(AtlasError):
        SchedulerStore(absent, read_only=True)
    assert not absent.exists()
    with pytest.raises(AtlasError):
        SchedulerStore(tmp_path / ".env")


def test_read_only_diagnostics_do_not_mutate_checkpoint(runtime):
    _, store, factory = runtime
    factory()
    before = store.path.read_bytes()
    reader = SchedulerStore(store.path, read_only=True)
    assert reader.snapshot("test-stream")["cursor"] == 0
    with pytest.raises(AtlasError, match="read-only"):
        reader.claim("test-stream", "w")
    assert before == store.path.read_bytes()


def test_nonmock_provider_is_refused_before_registering(runtime):
    _, store, factory = runtime
    with pytest.raises(AtlasError, match="offline mock"):
        factory(provider=object())
    with pytest.raises(AtlasError, match="Unknown"):
        store.snapshot("test-stream")


def test_bad_schema_invalid_lease_and_wrong_stream_fail_closed(runtime):
    _, store, factory = runtime
    scheduler = factory()
    for ttl in (timedelta(0), timedelta(seconds=-1), timedelta(hours=2)):
        with pytest.raises(AtlasError):
            store.claim("test-stream", "w", ttl)
    with pytest.raises(AtlasError):
        scheduler.scan(Lease("wrong-stream", "w", 1))
    with store.connect(write=True) as db:
        db.execute("UPDATE paper_meta SET version=999")
    with pytest.raises(AtlasError, match="Unsupported"):
        SchedulerStore(store.path)


def test_mock_rejects_malformed_control_and_delivery_order():
    with pytest.raises(ValueError):
        MockEvent(at=AT, kind=EventKind.OBSERVATION)
    events = mock_script(mock_config(), AT).events
    with pytest.raises(AtlasError, match="ordered"):
        MockCurrentProvider(tuple(reversed(events)))
    assert (
        json.loads(events[0].model_dump_json())["observation"]["provenance"]
        == "ARTIFICIAL_OFFLINE_MOCK"
    )


def test_session_transition_and_restart_resume_next_eligible_day(runtime):
    clock, store, factory = runtime
    config = mock_config()
    close = AT.replace(hour=19, minute=59, second=59)
    tomorrow = (AT + timedelta(days=1)).replace(hour=13, minute=30)
    provider = MockCurrentProvider(
        (mock_script(config, close).events[0], mock_script(config, tomorrow).events[1])
    )
    scheduler = factory(provider=provider)
    clock[0] = close
    a = store.claim("test-stream", "day-one")
    assert scheduler.scan(a)["outcome"] == "ELIGIBLE"
    clock[0] += timedelta(seconds=1)
    assert scheduler.scan(a)["outcome"] == "WAITING_SESSION"
    assert store.snapshot("test-stream")["cursor"] == 1
    clock[0] = tomorrow + timedelta(seconds=1)
    reopened = SchedulerStore(store.path, clock=lambda: clock[0])
    scheduler = factory(provider=provider, selected_store=reopened)
    b = reopened.claim("test-stream", "day-two")
    assert scheduler.scan(b)["outcome"] == "ELIGIBLE"
    assert reopened.snapshot("test-stream")["cursor"] == 2


@pytest.mark.parametrize("at", ["2026-11-27T17:59:59+00:00", "2026-11-27T18:00:00+00:00"])
def test_early_close_is_enforced_by_scheduler(tmp_path, at):
    now = datetime.fromisoformat(at)
    config = mock_config(Venue.NYSE)
    store = SchedulerStore(tmp_path / "early.sqlite3", clock=lambda: now)
    scheduler = SessionScheduler(
        store,
        "early",
        config,
        HistoricalCalendar(Venue.NYSE, now.date(), now.date()),
        mock_script(config, now),
        mock_entitlement(config, now),
    )
    lease = store.claim("early", "worker")
    assert scheduler.scan(lease)["outcome"] == (
        "ELIGIBLE" if now.minute == 59 else "WAITING_SESSION"
    )


def test_session_close_during_poll_does_not_advance_cursor(runtime, monkeypatch):
    clock, store, factory = runtime
    scheduler = factory()
    clock[0] = AT.replace(hour=19, minute=59, second=59)
    lease = store.claim("test-stream", "worker")
    poll = scheduler.provider.poll

    def cross_close(cursor, now):
        clock[0] += timedelta(seconds=1)
        return poll(cursor, now)

    monkeypatch.setattr(scheduler.provider, "poll", cross_close)
    assert scheduler.scan(lease)["outcome"] == "WAITING_SESSION"
    assert store.snapshot("test-stream")["cursor"] == 0


def test_provider_restart_does_not_reset_deduplication_or_release_halt(runtime):
    clock, store, factory = runtime
    first = mock_script(mock_config(), AT).events[0]
    duplicate = first.observation.model_dump() | {
        "received_at": AT + timedelta(seconds=3),
        "provider_epoch": "mock-process-2",
    }
    events = (
        first,
        MockEvent(at=AT + timedelta(seconds=1), kind=EventKind.HALT),
        MockEvent(at=AT + timedelta(seconds=2), kind=EventKind.RESTART, epoch="mock-process-2"),
        MockEvent(
            at=AT + timedelta(seconds=3),
            kind=EventKind.OBSERVATION,
            epoch="mock-process-2",
            observation=Observation(**duplicate),
        ),
        MockEvent(at=AT + timedelta(seconds=4), kind=EventKind.RESUME, epoch="wrong-process"),
    )
    scheduler = factory(provider=MockCurrentProvider(events))
    lease, results = play(clock, store, scheduler)
    assert {"MARKET_HALTED", "DUPLICATE_OBSERVATION"} <= set(results[3]["reasons"])
    assert results[4]["reasons"] == ["PROVIDER_EPOCH_MISMATCH"]
    clock[0] += timedelta(seconds=1)
    assert scheduler.scan(lease)["outcome"] == "NO_DATA"
    assert store.snapshot("test-stream")["status"] == "HALTED"
