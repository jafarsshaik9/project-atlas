"""Deterministic failure injection at the trading inbox and accounting boundaries."""

import json
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from atlas.config import AtlasError, Settings
from atlas.domain import DecisionStatus
from atlas.main import main
from atlas.market_data.calendars import HistoricalCalendar
from atlas.paper_data.contracts import Observation
from atlas.paper_data.mock import (
    EventKind,
    MockCurrentProvider,
    MockEvent,
    mock_config,
    mock_entitlement,
)
from atlas.paper_data.scheduler import SessionScheduler
from atlas.paper_data.store import OwnershipLost, SchedulerStore
from atlas.paper_execution.cli import run_session
from atlas.paper_execution.engine import ExecutionPolicy, PaperEngine
from atlas.paper_execution.ledger import load, report, rows, save_seal, write_state
from atlas.paper_execution.mock import OPEN, costs, fx, session_script
from atlas.portfolio import PortfolioLedger
from atlas.risk import RiskEngine, RiskPolicy


class Harness:
    def __init__(
        self, tmp_path, *, transform=lambda events: events, fees=None, policy=None, risk_policy=None
    ):
        self.clock = [OPEN]
        self.config = mock_config()
        self.events = tuple(transform(list(session_script(self.config).events)))
        self.store = SchedulerStore(tmp_path / "paper.sqlite3", clock=lambda: self.clock[0])
        self.scheduler = SessionScheduler(
            self.store,
            "mock-paper-execution",
            self.config,
            HistoricalCalendar(self.config.instrument.venue, OPEN.date(), OPEN.date()),
            MockCurrentProvider(self.events),
            mock_entitlement(self.config, OPEN),
        )
        self.engine = PaperEngine(self.scheduler, fees or costs(), policy, risk_policy)
        self.lease = self.store.claim(self.scheduler.stream_id, "first")

    def advance(self, at):
        while at - self.clock[0] > timedelta(seconds=20):
            self.clock[0] += timedelta(seconds=20)
            self.store.heartbeat(self.lease)
        self.clock[0] = max(self.clock[0], at)
        self.store.heartbeat(self.lease)

    def step(self, index, *, rate=True, process=True, fault=lambda stage: None):
        next_scan = self.store.snapshot(self.scheduler.stream_id)["next_scan_at"]
        due = datetime.fromisoformat(next_scan) if next_scan else self.events[index].at
        self.advance(max(due, self.events[index].at))
        self.scheduler.scan(self.lease)
        if process:
            return self.engine.process(self.lease, fx(self.clock[0]) if rate else None, fault=fault)

    def through(self, last):
        cursor = self.store.snapshot(self.scheduler.stream_id)["cursor"]
        for index in range(cursor, last + 1):
            self.step(index)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def changed(event, **values):
    item = Observation(**(event.observation.model_dump() | values))
    return MockEvent(at=item.received_at, kind=EventKind.OBSERVATION, observation=item)


def test_complete_partial_session_and_exact_cost_reconciliation(tmp_path):
    result = run_session(tmp_path / "completed.sqlite3", "completed")
    assert result["reconciled"] and result["positions"] == []
    assert result["cash_gbp"] == result["equity_gbp"] == "1002.424"
    assert result["realised_pnl_gbp"] == "2.424"
    assert result["fees_gbp"] == "0.12"
    fills = result["recent_simulated_trades"]
    assert [f["quantity"] for f in fills] == ["1", "1", "1", "3"]
    assert [f["side"] for f in fills] == ["buy", "buy", "buy", "sell"]
    assert sum(Decimal(f["spread_cost_gbp"]) for f in fills) == Decimal("0.096")
    assert all(f["risk_approved"] and f["simulated"] for f in fills)
    assert all(d["status"] == "approved" for d in result["risk_decisions"])
    assert len(result["signals"]) == 1 and len(result["orders"]) == 2


def test_no_opportunities_has_no_manufactured_signals_or_trades(tmp_path):
    result = run_session(tmp_path / "none.sqlite3", "no-opportunities")
    assert result["signals"] == result["orders"] == result["recent_simulated_trades"] == []
    assert result["cash_gbp"] == "1000.00" and result["reconciled"]


def test_repeated_runs_are_deterministic_and_restart_does_not_duplicate(tmp_path):
    first = run_session(tmp_path / "a.sqlite3", "completed")
    independent = run_session(tmp_path / "b.sqlite3", "completed")
    assert first == independent
    resumed = run_session(tmp_path / "a.sqlite3", "completed")
    for key in ("cash_gbp", "equity_gbp", "recent_simulated_trades", "orders", "signals"):
        assert resumed[key] == first[key]


def test_signal_observation_never_fills_and_strategy_state_survives_restart(h):
    h.through(10)
    result = report(h.store)
    assert len(result["signals"]) == len(result["orders"]) == 1
    assert not result["risk_decisions"] and not result["recent_simulated_trades"]
    h.engine = PaperEngine(h.scheduler, costs())
    h.step(11)
    result = report(h.store)
    assert result["recent_simulated_trades"][0]["timestamp"] > result["signals"][0]["decision_time"]
    assert len(result["risk_decisions"]) == 1
    assert result["equity_gbp"] == "999.948"
    assert result["positions"][0]["mark_value_gbp"] == "80.384"


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
@pytest.mark.parametrize("index", [10, 11, 15])
def test_crash_before_or_after_transition_commit_is_retry_safe(h, phase, index):
    h.through(index - 1)
    h.step(index, process=False)
    before = report(h.store)

    def crash(stage):
        if stage == phase:
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError):
        h.engine.process(h.lease, fx(h.clock[0]), fault=crash)
    result = report(h.store)
    assert result["trading_ack"] == before["trading_ack"] + (phase == "after_commit")
    if phase == "before_commit":
        assert result["orders"] == before["orders"]
        assert result["recent_simulated_trades"] == before["recent_simulated_trades"]
    h.engine = PaperEngine(h.scheduler, costs())
    h.engine.process(h.lease, fx(h.clock[0]))
    once = report(h.store)
    h.engine.process(h.lease, fx(h.clock[0]))
    assert report(h.store)["recent_simulated_trades"] == once["recent_simulated_trades"]
    assert once["reconciled"]


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_real_process_death_and_worker_takeover(h, phase):
    h.through(10)
    h.store.shutdown(h.lease)
    script = """
import os,sys
from datetime import timedelta
from atlas.paper_execution.cli import runtime
from atlas.paper_execution.mock import OPEN,fx
at=OPEN+timedelta(seconds=601)
clock,store,scheduler,engine=runtime(sys.argv[1],clock=lambda:at)
lease=store.claim(scheduler.stream_id,'dead-worker',timedelta(seconds=1))
scheduler.scan(lease)
def die(stage):
    if stage==sys.argv[2]: os._exit(73)
engine.process(lease,fx(at),fault=die)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(h.store.path), phase], capture_output=True, timeout=30
    )
    assert child.returncode == 73, child.stderr.decode()
    h.clock[0] = OPEN + timedelta(seconds=602)
    h.lease = h.store.claim(h.scheduler.stream_id, "takeover")
    assert h.lease.fence == 3
    h.engine = PaperEngine(h.scheduler, costs())
    h.engine.process(h.lease, fx(h.clock[0]))
    result = report(h.store)
    assert result["trading_ack"] == 11
    assert len(result["recent_simulated_trades"]) == 1
    h.engine.process(h.lease, fx(h.clock[0]))
    assert report(h.store)["recent_simulated_trades"] == result["recent_simulated_trades"]
    with h.store.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_worker_takeover_fences_old_worker(h):
    h.through(10)
    old = h.lease
    h.clock[0] += timedelta(seconds=30)
    h.lease = h.store.claim(h.scheduler.stream_id, "successor")
    with pytest.raises(OwnershipLost):
        h.engine.process(old, fx(h.clock[0]))
    assert report(h.store)["trading_ack"] == 10


def test_two_simultaneous_trading_workers_cannot_duplicate_fill(h):
    h.through(10)
    h.step(11, process=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: h.engine.process(h.lease, fx(h.clock[0])), range(2)))
    assert sorted(results) == ["ELIGIBLE", "NO_TRADING_INPUT"]
    assert len(report(h.store)["recent_simulated_trades"]) == 1


def test_duplicate_observation_signal_and_intent_are_idempotent(tmp_path):
    def transform(events):
        duplicate = changed(events[11], received_at=OPEN + timedelta(seconds=601, microseconds=1))
        return events[:12] + [duplicate] + events[12:]

    h = Harness(tmp_path, transform=transform)
    h.through(10)
    with h.store.connect(write=True) as db:
        _, saved = load(db)
        signal = rows(db, "execution_signals")[0]
        observation = h.events[10].observation
        h.engine._create(db, signal, "buy", observation, h.clock[0])
        h.engine._create(db, signal, "buy", observation, h.clock[0])
        assert len(rows(db, "execution_orders")) == 1
        write_state(db, saved)
        save_seal(db)
    h.through(len(h.events) - 1)
    result = report(h.store)
    assert len(result["signals"]) == 1
    assert len(result["orders"]) == 2
    ids = [f["fill_id"] for f in result["recent_simulated_trades"]]
    assert len(ids) == len(set(ids)) == 4


@pytest.mark.parametrize("table", ["execution_signals", "execution_orders", "execution_fills"])
def test_database_rejects_duplicate_ids(h, table):
    h.through(11)
    with pytest.raises(AtlasError, match="transaction failed"):
        with h.store.connect(write=True) as db:
            db.execute(f"INSERT INTO {table} SELECT * FROM {table}")
    assert report(h.store)["reconciled"]


@pytest.mark.parametrize(
    "kind", ["stale", "closed", "halt", "disconnect", "fx", "future-fx", "stale-fx", "nonmock-fx"]
)
def test_data_market_and_fx_controls_prevent_fills(h, kind):
    h.through(10)
    if kind in ("halt", "disconnect"):
        with h.store.connect(write=True) as db:
            column = "halted" if kind == "halt" else "connected"
            db.execute(f"UPDATE paper_streams SET {column}=?", (1 if kind == "halt" else 0,))
    h.step(11, process=False)
    rate = fx(h.clock[0])
    if kind == "stale":
        h.advance(h.clock[0] + timedelta(seconds=11))
    elif kind == "closed":
        h.store.shutdown(h.lease)
        h.clock[0] = OPEN.replace(hour=20, minute=0)
        h.lease = h.store.claim(h.scheduler.stream_id, "after-close")
    elif kind == "fx":
        rate = None
    elif kind == "future-fx":
        rate = fx(h.clock[0] + timedelta(seconds=1))
    elif kind == "stale-fx":
        rate = fx(h.clock[0] - timedelta(seconds=61))
    elif kind == "nonmock-fx":
        rate = replace(rate, source="UNVERIFIED")
    h.engine.process(h.lease, rate)
    result = report(h.store)
    assert result["recent_simulated_trades"] == []
    assert result["orders"][0]["status"] == "CANCELLED"
    assert result["reconciled"]


def test_no_new_data_staleness_cancels_pending_order(h):
    h.through(10)
    h.advance(h.clock[0] + timedelta(seconds=11))
    assert h.engine.process(h.lease, fx(h.clock[0])) == "STALE_DATA"
    assert report(h.store)["orders"][0]["status"] == "CANCELLED"


def test_independent_risk_rejection_prevents_accounting(tmp_path):
    h = Harness(tmp_path, risk_policy=RiskPolicy(max_stressed_risk_pct=Decimal("0.0001")))
    h.through(11)
    result = report(h.store)
    assert result["risk_decisions"][0]["status"] == "rejected"
    assert "STRESSED_RISK" in result["risk_decisions"][0]["reasons"]
    assert not result["recent_simulated_trades"]


def test_insufficient_cash_rejected_by_existing_risk_gate(h):
    h.through(10)
    with h.store.connect() as db:
        ledger, saved = load(db)
        signal = rows(db, "execution_signals")[0]
    now = OPEN + timedelta(seconds=601)
    quote = h.engine._quote(h.events[11].observation, now)
    from atlas.domain import TradeIntent

    decision = RiskEngine(ledger, costs(), h.scheduler.calendar).evaluate(
        TradeIntent(signal, Decimal("20")),
        h.engine.instrument,
        quote,
        saved["bars"],
        h.scheduler._session(now),
        fx(now),
        now,
    )
    assert decision.status == DecisionStatus.REJECTED and "INSUFFICIENT_CASH" in decision.reasons


def test_zero_liquidity_does_not_invent_fill(tmp_path):
    h = Harness(
        tmp_path,
        transform=lambda events: (
            events[:11] + [changed(event, volume="0") for event in events[11:]]
        ),
    )
    h.through(11)
    result = report(h.store)
    assert not result["recent_simulated_trades"]
    assert result["orders"][0]["reason"] == "INSUFFICIENT_LIQUIDITY"


def test_persistent_kill_switch_cancels_reservation_and_survives_reopen(h):
    h.through(11)
    h.engine.kill()
    h.engine = PaperEngine(h.scheduler, costs())
    h.through(15)
    result = report(h.store)
    assert result["kill_switch"] and result["status"] == "KILLED"
    assert len(result["recent_simulated_trades"]) == 1
    assert result["orders"][0]["status"] == "CANCELLED"
    assert result["positions"][0]["quantity"] == "1"


@pytest.mark.parametrize(
    "table",
    [
        "execution_state",
        "execution_inbox",
        "execution_journal",
        "execution_signals",
        "execution_orders",
        "execution_fills",
    ],
)
def test_corruption_is_detected_before_any_new_ack_or_fill(h, table):
    h.through(11)
    with h.store.connect(write=True) as db:
        if table == "execution_inbox":
            db.execute("UPDATE execution_inbox SET checksum='corrupt' WHERE ordinal=0")
        else:
            db.execute(f"UPDATE {table} SET content='{{}}'")
    with pytest.raises(AtlasError):
        h.engine.process(h.lease, fx(h.clock[0]))
    with pytest.raises(AtlasError):
        report(h.store)


def test_reconciliation_failure_rolls_back_and_remains_unacknowledged(h, monkeypatch):
    h.through(10)
    h.step(11, process=False)
    before = report(h.store)
    with monkeypatch.context() as m:
        m.setattr(PortfolioLedger, "reconcile", lambda _: False)
        with pytest.raises(AtlasError):
            h.engine.process(h.lease, fx(h.clock[0]))
    assert report(h.store) == before


def test_sql_failure_between_fill_and_ack_rolls_back_every_trading_effect(h):
    h.through(10)
    h.step(11, process=False)
    before = report(h.store)
    with h.store.connect(write=True) as db:
        db.execute("""CREATE TRIGGER fail_ack BEFORE INSERT ON execution_inbox
                   BEGIN SELECT RAISE(ABORT,'injected'); END""")
    with pytest.raises(AtlasError):
        h.engine.process(h.lease, fx(h.clock[0]))
    assert report(h.store) == before
    with h.store.connect(write=True) as db:
        db.execute("DROP TRIGGER fail_ack")
    h.engine.process(h.lease, fx(h.clock[0]))
    assert len(report(h.store)["recent_simulated_trades"]) == 1


@pytest.mark.parametrize(
    "values",
    [
        {"participation": Decimal("1.1")},
        {"participation": Decimal("NaN")},
        {"latency": timedelta(0)},
        {"volume_convention": "UNKNOWN"},
    ],
)
def test_unknown_liquidity_or_latency_inputs_are_rejected(values):
    with pytest.raises(AtlasError):
        ExecutionPolicy(**values)


def test_unknown_costs_fail_closed(tmp_path):
    fees = costs()
    fees.schedules = (replace(fees.schedules[0], fx_bps=None),)
    h = Harness(tmp_path, fees=fees)
    h.through(15)
    assert not report(h.store)["recent_simulated_trades"]


def test_missing_minute_sample_blocks_strategy(tmp_path):
    h = Harness(tmp_path, transform=lambda events: events[:2] + events[3:])
    h.through(len(h.events) - 1)
    assert report(h.store)["signals"] == []


def test_cli_is_offline_and_read_only_diagnostics_do_not_create_files(
    tmp_path, monkeypatch, capsys
):
    def forbidden(*args, **kwargs):
        pytest.fail("Network or credential access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Settings, "from_environment", forbidden)
    path = tmp_path / "cli.sqlite3"
    assert main(["paper-session", "state", "--db", str(path)]) == 1
    assert not path.exists()
    assert main(["paper-session", "run", "--db", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["cash_gbp"] == "1002.424"
    before = path.read_bytes()
    assert main(["paper-session", "state", "--db", str(path)]) == 0
    assert path.read_bytes() == before


def test_fresh_process_forbids_broker_provider_notification_and_secret_imports(tmp_path):
    script = """
import builtins,socket,sys,os
from pathlib import Path
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith(('atlas.broker','atlas.notifications','atlas.market_data.massive',
                        'atlas.market_data.providers')): raise AssertionError(name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
def forbidden(*args,**kwargs): raise AssertionError('Forbidden access')
socket.socket.connect=forbidden
socket.create_connection=forbidden
read=Path.read_text
def read_text(path,*args,**kwargs):
    if path.name.startswith('.env'): forbidden()
    return read(path,*args,**kwargs)
Path.read_text=read_text
get=os.environ.get
def env(key,*args):
    if any(s in key.upper() for s in ('TOKEN','SECRET','PASSWORD','API_KEY')): forbidden()
    return get(key,*args)
os.environ.get=env
from atlas.main import main
assert main(['paper-session','run','--db',sys.argv[1]])==0
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "isolated.sqlite3")],
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stderr.decode()


def test_latency_requires_later_actionable_observation(tmp_path):
    h = Harness(tmp_path, policy=ExecutionPolicy(latency=timedelta(seconds=2)))
    h.through(11)
    assert not report(h.store)["recent_simulated_trades"]
    h.step(12)
    assert len(report(h.store)["recent_simulated_trades"]) == 1


def test_partial_sell_retains_position_and_conservative_risk(tmp_path):
    h = Harness(tmp_path, transform=lambda events: events[:15] + [changed(events[15], volume="10")])
    h.through(15)
    result = report(h.store)
    assert result["recent_simulated_trades"][-1]["quantity"] == "1"
    assert result["positions"][0]["quantity"] == "2"
    assert result["reconciled"]
    assert Decimal(result["positions"][0]["planned_risk_gbp"]) > 0


def test_exit_risk_is_mandatory(h, monkeypatch):
    h.through(14)
    original = RiskEngine.evaluate_exit

    def reject(self, *args):
        return replace(
            original(self, *args),
            status=DecisionStatus.REJECTED,
            reasons=("INJECTED_EXIT_RISK_REJECTION",),
        )

    monkeypatch.setattr(RiskEngine, "evaluate_exit", reject)
    h.step(15)
    result = report(h.store)
    assert len(result["recent_simulated_trades"]) == 3
    assert result["positions"][0]["quantity"] == "3"
    assert result["risk_decisions"][-1]["status"] == "rejected"


@pytest.mark.parametrize(
    "field,value", [("spread_bps", Decimal("10000")), ("slippage_bps", Decimal("10000"))]
)
def test_inconsistent_cost_model_cannot_produce_fills(tmp_path, field, value):
    fees = costs()
    fees.schedules = (replace(fees.schedules[0], **{field: value}),)
    h = Harness(tmp_path, fees=fees)
    h.through(15)
    assert not report(h.store)["recent_simulated_trades"]


def test_changed_paper_policy_cannot_reopen_account(h):
    with pytest.raises(AtlasError, match="configuration mismatch"):
        PaperEngine(h.scheduler, costs(), ExecutionPolicy(participation=Decimal("0.2")))


def test_existing_accounting_price_ceiling_is_not_weakened(tmp_path):
    h = Harness(
        tmp_path,
        transform=lambda events: events[:12] + [changed(events[12], price="100.60")] + events[13:],
    )
    h.through(11)
    before = report(h.store)
    h.step(12, process=False)
    h.engine.process(h.lease, fx(h.clock[0]))
    after = report(h.store)
    assert after["recent_simulated_trades"] == before["recent_simulated_trades"]
    assert after["orders"][0]["status"] == "CANCELLED"
    assert after["orders"][0]["reason"] == "FILL_REQUIRES_NEW_RISK_CHECK"
    assert after["trading_ack"] == before["trading_ack"] + 1 and after["reconciled"]


def test_same_source_identity_with_changed_content_is_rejected(tmp_path):
    def transform(events):
        conflict = changed(events[11], price="100.60", received_at=OPEN + timedelta(seconds=602))
        return events[:12] + [conflict] + events[13:]

    h = Harness(tmp_path, transform=transform)
    h.through(12)
    assert len(report(h.store)["recent_simulated_trades"]) == 1
    assert report(h.store)["orders"][0]["status"] == "CANCELLED"


def test_partial_fill_fee_budget_cannot_be_overspent(tmp_path):
    fees = costs()
    fees.schedules = (replace(fees.schedules[0], commission_fixed_gbp=Decimal("0.01")),)
    h = Harness(tmp_path, fees=fees)
    h.through(13)
    result = report(h.store)
    assert len(result["recent_simulated_trades"]) == 2
    assert result["orders"][0]["status"] == "CANCELLED"
    assert result["orders"][0]["reason"] == "FILL_REQUIRES_NEW_RISK_CHECK"
    assert result["positions"][0]["quantity"] == "2" and result["reconciled"]


def test_partial_fill_fx_ceiling_cannot_be_relaxed(h):
    h.through(11)
    h.step(12, process=False)
    h.engine.process(h.lease, replace(fx(h.clock[0]), gbp_per_unit=Decimal("0.9")))
    result = report(h.store)
    assert len(result["recent_simulated_trades"]) == 1
    assert result["orders"][0]["status"] == "CANCELLED" and result["reconciled"]


def test_inbox_detects_corrupted_scheduler_source(h):
    h.through(11)
    with h.store.connect(write=True) as db:
        db.execute("UPDATE paper_results SET content='{}' WHERE ordinal=0")
    with pytest.raises(AtlasError, match="inbox mismatch"):
        report(h.store)


def test_mock_halt_event_cancels_order_before_next_price(tmp_path):
    h = Harness(
        tmp_path,
        transform=lambda events: (
            events[:11]
            + [MockEvent(at=OPEN + timedelta(seconds=601), kind=EventKind.HALT)]
            + events[12:]
        ),
    )
    h.through(12)
    result = report(h.store)
    assert result["orders"][0]["status"] == "CANCELLED"
    assert result["recent_simulated_trades"] == [] and result["reconciled"]


def test_cancelled_exit_can_recover_on_fresh_evidence_without_duplicate_order(tmp_path):
    def transform(events):
        return events[:15] + [
            MockEvent(at=events[15].at, kind=EventKind.MISSING),
            changed(
                events[15],
                observation_id="recovered-1",
                event_at=OPEN + timedelta(seconds=662),
                available_at=OPEN + timedelta(seconds=662),
                received_at=OPEN + timedelta(seconds=662),
            ),
            changed(
                events[15],
                observation_id="recovered-2",
                event_at=OPEN + timedelta(seconds=663),
                available_at=OPEN + timedelta(seconds=663),
                received_at=OPEN + timedelta(seconds=663),
            ),
        ]

    h = Harness(tmp_path, transform=transform)
    h.through(15)
    result = report(h.store)
    sell_id = next(o["id"] for o in result["orders"] if o["side"] == "sell")
    assert len(result["recent_simulated_trades"]) == 3
    h.step(16)
    assert len(report(h.store)["recent_simulated_trades"]) == 3
    h.step(17)
    result = report(h.store)
    assert len(result["orders"]) == 2 and len(result["recent_simulated_trades"]) == 4
    assert result["recent_simulated_trades"][-1]["order_id"] == sell_id
    assert result["positions"] == [] and result["reconciled"]
