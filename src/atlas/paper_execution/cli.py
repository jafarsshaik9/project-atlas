"""Offline finite mock sessions and read-only account diagnostics; no settings loading."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from atlas.config import AtlasError
from atlas.market_data.calendars import HistoricalCalendar
from atlas.paper_data.mock import mock_config, mock_entitlement
from atlas.paper_data.scheduler import SessionScheduler
from atlas.paper_data.store import SchedulerStore
from atlas.paper_execution.engine import PaperEngine
from atlas.paper_execution.ledger import report
from atlas.paper_execution.mock import OPEN, costs, fx, session_script


def runtime(path, scenario="completed", clock=None):
    current = [OPEN]
    clock = clock or (lambda: current[0])
    store = SchedulerStore(path, clock=clock)
    config = mock_config()
    scheduler = SessionScheduler(
        store,
        "mock-paper-execution",
        config,
        HistoricalCalendar(config.instrument.venue, OPEN.date(), OPEN.date()),
        session_script(config, scenario),
        mock_entitlement(config, OPEN),
    )
    before = store.snapshot(scheduler.stream_id)
    if before["last_clock"]:
        current[0] = max(current[0], datetime.fromisoformat(before["last_clock"]))
    return current, store, scheduler, PaperEngine(scheduler, costs())


def run_session(path, scenario):
    current, store, scheduler, engine = runtime(path, scenario)
    lease = store.claim(scheduler.stream_id, str(uuid4()))
    if lease is None:
        raise AtlasError("Mock session already has a worker.")
    try:
        # Drain any committed scheduler records that survived an earlier worker crash first.
        while True:
            before = report(store)["trading_ack"]
            engine.process(lease, fx(current[0]))
            if report(store)["trading_ack"] == before:
                break
        cursor = store.snapshot(scheduler.stream_id)["cursor"]
        for event in scheduler.provider.events[cursor:]:
            while event.at - current[0] > timedelta(seconds=20):
                current[0] += timedelta(seconds=20)
                store.heartbeat(lease)
            current[0] = max(current[0], event.at)
            store.heartbeat(lease)
            scheduler.scan(lease)
            engine.process(lease, fx(current[0]))
    finally:
        store.shutdown(lease)
    return report(store)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Artificial offline paper trading only")
    parser.add_argument("command", choices=("run", "state", "kill"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--scenario", choices=("completed", "no-opportunities"), default="completed"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = run_session(args.db, args.scenario)
        elif args.command == "state":
            result = report(SchedulerStore(args.db, read_only=True))
        else:
            if not args.db.exists():
                raise AtlasError("No existing paper account.")
            current, store, _, engine = runtime(args.db, args.scenario)
            engine.kill()
            result = report(store)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AtlasError, ValueError, KeyError, TypeError, OSError):
        print(
            json.dumps({"error": "PAPER_EXECUTION_FAILED", "scope": "MOCK_ONLY"}), file=sys.stderr
        )
        return 1
