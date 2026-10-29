"""Bounded frozen offline matrix. Explicit CLI invocation only; no credentials or downloads."""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import time
import zlib
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from decimal import Decimal, localcontext
from itertools import groupby
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.audit_calendar import classification
from atlas.forex.collection import CollectionLedger, validate_plan
from atlas.forex.execution.calendar import market_reason, window
from atlas.forex.execution.contracts import MINUTE, Frame, arithmetic_context
from atlas.forex.execution.engine import implementation_hash
from atlas.forex.execution.market import frames_from_partitions
from atlas.forex.models import FxInstrument
from atlas.forex.research.design import (
    DESIGN_SHA,
    PAIRS,
    ROOT,
    SCENARIOS,
    SESSIONS,
    STRATEGIES,
    cell_id,
    load_design,
    run_for,
    settings_for,
    split_for,
)
from atlas.forex.research.execution import evaluate_intent
from atlas.forex.research.metrics import sealed_summary, summarize
from atlas.forex.research.strategies import candidate
from atlas.market_data.contracts import canonical, digest


def research_source_hash():
    paths = list(Path(__file__).parent.glob("*.py"))
    paths += list((Path(__file__).parent.parent / "execution").glob("*.py"))
    return digest(
        {
            str(p.relative_to(ROOT)): hashlib.sha256(
                p.read_text(encoding="utf-8").encode()
            ).hexdigest()
            for p in sorted(paths)
        }
    )


def memory_bytes():
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak_working",
                "working",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    data = Counters()
    data.cb = ctypes.sizeof(data)
    get_current = ctypes.windll.kernel32.GetCurrentProcess
    get_current.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    get_memory.restype = wintypes.BOOL
    handle = get_current()
    if not get_memory(handle, ctypes.byref(data), data.cb):
        return 0
    return data.peak_working


def offline_worker():
    import socket

    import httpx

    import atlas.forex.config as config
    import atlas.forex.provider as provider

    def forbidden(*args, **kwargs):
        raise AtlasError("FOREX_RESEARCH_FORBIDDEN_EXTERNAL_SIDE_EFFECT")

    socket.create_connection = forbidden
    httpx.HTTPTransport.handle_request = forbidden
    config.load_config = forbidden
    provider.OandaReader = forbidden
    provider.ReadOnlyTransport = forbidden


def worker(task):
    with localcontext(arithmetic_context()):
        day, pair, session, frames, instruments, states, design, full_hash, extremes = task
        outputs = []
        meta = next(i for i in instruments if i.name == pair)
        quality = {
            "missing_pair_minutes": 0,
            "conversion_missing_minutes": 0,
            "closure_restriction_minutes": 0,
        }
        absence_kinds = Counter()
        for f in frames:
            available = {o.instrument: o for o in f.observations}
            quality["missing_pair_minutes"] += pair not in available
            if pair not in available:
                absence_kinds[classification(f.start, observed=False)] += 1
            quality["conversion_missing_minutes"] += "GBP_USD" not in available or (
                pair == "USD_JPY" and "USD_JPY" not in available
            )
            quality["closure_restriction_minutes"] += bool(market_reason(f.start))
        quality["absence_classifications_v2"] = dict(absence_kinds)
        for strategy in STRATEGIES:
            for scenario in SCENARIOS[:4]:
                key = cell_id(pair, session, strategy, scenario)
                state = states[key]
                cash = Decimal(state["cash"])
                row = {
                    "cell": key,
                    "day": str(day),
                    "split": split_for(frames[0].start, design),
                    "starting_cash": str(cash),
                    "quality": quality,
                    "source_frame_hash": full_hash,
                }
                if not state["known"]:
                    row.update(
                        status="BLOCKED_UNKNOWN_CAPITAL",
                        reason_counts={"UNKNOWN_PRIOR_POSITION_CAPITAL": 1},
                    )
                    outputs.append((key, state, row))
                    continue
                if cash <= 0:
                    row.update(
                        status="BLOCKED_CAPITAL_EXHAUSTED",
                        reason_counts={"NO_UNLEVERAGED_CAPITAL": 1},
                    )
                    outputs.append((key, state, row))
                    continue
                cfg = settings_for(scenario, cash, design)
                run = run_for(day, session, design)
                win = window(run, cfg)
                intent, index, reasons = candidate(frames, pair, strategy, meta, cfg, win)
                execution = evaluate_intent(cfg, run, instruments, frames, intent, index, full_hash)
                next_state = {
                    "cash": execution["cash_partial"],
                    "known": not bool(execution["invalidated"]),
                }
                row.update(
                    status=execution["status"],
                    execution=execution,
                    reason_counts=reasons,
                    prospective_signals=int(intent is not None),
                    extreme_exposures=[],
                )
                trade = execution["trade"]
                row["risk_failure"] = Decimal(execution["cash_partial"]) < 0
                if trade:
                    opened, closed = map(
                        datetime.fromisoformat, (trade["position"]["opened_at"], trade["exited_at"])
                    )
                    if (
                        split_for(opened, design) != row["split"]
                        or split_for(closed, design) != row["split"]
                    ):
                        raise AtlasError("FOREX_RESEARCH_POSITION_CROSSES_SPLIT")
                    row["extreme_exposures"] = [
                        e
                        for e in extremes
                        if (e["instrument"] == pair or e["instrument"] == "GBP_USD")
                        and opened - MINUTE <= datetime.fromisoformat(e["time"]) < closed
                    ]
                elif execution["unresolved_positions"]:
                    opened = datetime.fromisoformat(
                        execution["unresolved_positions"][0]["opened_at"]
                    )
                    closed = next(
                        datetime.fromisoformat(e["at"])
                        for e in execution["events"]
                        if e["kind"] == "UNRESOLVED"
                    )
                    row["extreme_exposures"] = [
                        e
                        for e in extremes
                        if (e["instrument"] == pair or e["instrument"] == "GBP_USD")
                        and opened - MINUTE <= datetime.fromisoformat(e["time"]) < closed
                    ]
                outputs.append((key, next_state, row))
        return outputs, memory_bytes()


def write_row(db, row):
    text = canonical(row)
    db.execute(
        "INSERT INTO sessions VALUES(?,?,?,?,?,?)",
        (
            row["cell"],
            row["day"],
            row["split"],
            row["status"],
            zlib.compress(text.encode(), 3),
            digest(row),
        ),
    )


def read_rows(db, cell):
    for payload, checksum in db.execute(
        "SELECT payload,checksum FROM sessions WHERE cell=? ORDER BY day", (cell,)
    ):
        row = json.loads(zlib.decompress(payload))
        if digest(row) != checksum:
            raise AtlasError("FOREX_RESEARCH_ROW_CHECKSUM_MISMATCH")
        yield row


def execute(plan, output, *, workers=4):
    design = load_design()
    validate_plan(plan)
    if plan["plan_hash"] != design["source_plan_hash"]:
        raise AtlasError("FOREX_RESEARCH_SOURCE_PLAN_CHANGED")
    if not 1 <= workers <= 4:
        raise AtlasError("FOREX_RESEARCH_WORKER_BOUND")
    if shutil.disk_usage(output.parent).free < design["resource_limits"]["minimum_free_disk_bytes"]:
        raise AtlasError("FOREX_RESEARCH_DISK_CAPACITY")
    output.mkdir(exist_ok=False)
    source_hash = research_source_hash()
    evidence_dir = ROOT / "local/verification/phase6b3-v1"
    smoke_path = evidence_dir / ("smoke-" + source_hash + ".json")
    prereq_path = evidence_dir / "prerequisites.json"
    if not smoke_path.is_file() or not prereq_path.is_file():
        raise AtlasError("FOREX_RESEARCH_PREHISTORICAL_VALIDATION_REQUIRED")
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    prereq = json.loads(prereq_path.read_text(encoding="utf-8"))
    if (
        smoke["implementation_source_hash"] != source_hash
        or not smoke["identical_cross_process_results"]
        or prereq["source_hash"] != source_hash
        or prereq["failed"]
        or prereq["skipped"]
    ):
        raise AtlasError("FOREX_RESEARCH_PREHISTORICAL_VALIDATION_MISMATCH")
    with (evidence_dir / "evaluation-started.json").open("x", encoding="utf-8") as handle:
        json.dump(
            {
                "design_sha": DESIGN_SHA,
                "source_hash": source_hash,
                "output": str(output),
                "single_frozen_matrix": True,
            },
            handle,
            indent=2,
        )
    (output / "run-seal.json").write_text(
        json.dumps(
            {
                "design_sha": DESIGN_SHA,
                "source_hash": source_hash,
                "execution_hash": implementation_hash(),
                "workers": workers,
                "pid": os.getpid(),
                "holdout_seen_before_invocation": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    started = time.monotonic()
    states = {
        cell_id(p, s, t, c): {"cash": "1000", "known": True}
        for p in PAIRS
        for s in SESSIONS
        for t in STRATEGIES
        for c in SCENARIOS[:4]
    }
    instruments = tuple(FxInstrument.model_validate(i) for i in plan["instruments"])
    ledger = CollectionLedger(plan["output"], read_only=True)
    with ledger.store.connect() as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AtlasError("FOREX_RESEARCH_SOURCE_INTEGRITY")
    details = json.loads(
        (ROOT / "local/verification/phase6b1c-run2/details.json").read_text(encoding="utf-8")
    )
    if digest({k: v for k, v in details.items() if k != "checksum"}) != details["checksum"]:
        raise AtlasError("FOREX_RESEARCH_AUDIT_DETAILS_CHANGED")
    extremes = [
        {"instrument": c["instrument"], "time": c["candle"]["start"]}
        for c in details["large_range_case_studies"]
    ]
    db = sqlite3.connect(output / "results.sqlite3")
    db.execute(
        "CREATE TABLE sessions(cell TEXT,day TEXT,split TEXT,status TEXT,"
        "payload BLOB,checksum TEXT,PRIMARY KEY(cell,day))"
    )
    db.execute("CREATE INDEX by_cell_split ON sessions(cell,split,day)")
    peak_memory = memory_bytes()
    day_count = 0
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=offline_worker) as pool:
            for day_text, rows_iter in groupby(
                plan["partitions"], key=lambda r: r["candle_plan"]["start"][:10]
            ):
                rows = list(rows_iter)
                start = datetime.fromisoformat(rows[0]["candle_plan"]["start"])
                end = datetime.fromisoformat(rows[0]["candle_plan"]["end"])
                day = start.date()
                if (
                    split_for(start, design) == "holdout"
                    and not (output / "holdout-access.json").exists()
                ):
                    with (output / "holdout-access.json").open("x", encoding="utf-8") as f:
                        json.dump(
                            {
                                "design_sha": DESIGN_SHA,
                                "source_hash": source_hash,
                                "first_holdout_day": day_text,
                                "single_frozen_evaluation": True,
                            },
                            f,
                            indent=2,
                        )
                bundles = []
                for row in rows:
                    value = ledger.get(row["key"])
                    if not value or value["status"] not in (
                        "ACCEPTED",
                        "OBSERVED_EMPTY_EXPECTED_CLOSED",
                    ):
                        raise AtlasError("FOREX_RESEARCH_PARTITION_UNAVAILABLE")
                    manifest, bars = ledger.store.inspect(value["dataset"], check_database=False)
                    if manifest["plan"] != row["candle_plan"]:
                        raise AtlasError("FOREX_RESEARCH_PARTITION_SCOPE_CHANGED")
                    bundles.append((value["dataset"], manifest, value["quality"], bars))
                all_frames = list(frames_from_partitions(bundles, start, end))
                frame_map = {f.start: f for f in all_frames}
                jobs = []
                for session in SESSIONS:
                    try:
                        win = window(
                            run_for(day, session, design), settings_for("C0", design=design)
                        )
                    except AtlasError as exc:
                        for pair in PAIRS:
                            for strategy in STRATEGIES:
                                for scenario in SCENARIOS[:4]:
                                    key = cell_id(pair, session, strategy, scenario)
                                    write_row(
                                        db,
                                        {
                                            "cell": key,
                                            "day": day_text,
                                            "split": split_for(start, design),
                                            "starting_cash": states[key]["cash"],
                                            "status": "EXCLUDED_SESSION",
                                            "reason_counts": {str(exc): 1},
                                        },
                                    )
                        continue
                    selected = []
                    cursor = win.opening
                    while cursor < win.flatten_at:
                        selected.append(
                            frame_map.get(cursor) or Frame(start=cursor, observations=())
                        )
                        cursor += MINUTE
                    frame_hash = digest([f.model_dump(mode="json") for f in selected])
                    for pair in PAIRS:
                        jobs.append(
                            (
                                day,
                                pair,
                                session,
                                selected,
                                instruments,
                                states,
                                design,
                                frame_hash,
                                extremes,
                            )
                        )
                for outputs, worker_memory in pool.map(worker, jobs):
                    peak_memory = max(peak_memory, worker_memory, memory_bytes())
                    for key, state, row in outputs:
                        states[key] = state
                        write_row(db, row)
                db.commit()
                day_count += 1
                if day_count % 25 == 0:
                    print(
                        json.dumps(
                            {
                                "verified_utc_dates": day_count,
                                "total_utc_dates": 731,
                                "elapsed_seconds": round(time.monotonic() - started, 2),
                                "peak_single_process_bytes": peak_memory,
                            }
                        ),
                        flush=True,
                    )
                if (
                    source_hash != research_source_hash()
                    or DESIGN_SHA
                    != hashlib.sha256(
                        (ROOT / "examples/forex-research-phase6b3-v1.json").read_bytes()
                    ).hexdigest()
                ):
                    raise AtlasError("FOREX_RESEARCH_FROZEN_IMPLEMENTATION_CHANGED_DURING_RUN")
        cells = []
        for pair in PAIRS:
            for session in SESSIONS:
                for strategy in STRATEGIES:
                    for scenario in SCENARIOS:
                        key = cell_id(pair, session, strategy, scenario)
                        cell = {
                            "cell": key,
                            "pair": pair,
                            "session": session,
                            "strategy": strategy,
                            "scenario": scenario,
                        }
                        if scenario == "C4":
                            unavailable = {
                                "status": "BLOCKED",
                                "evaluated": False,
                                "trade_count": 0,
                                "gross_model_pnl_gbp": None,
                                "total_modeled_costs_gbp": None,
                                "complete_case_net_pnl_gbp": None,
                                "portfolio_net_pnl_gbp": None,
                                "expectancy_gbp": None,
                                "profit_factor": None,
                                "maximum_marked_drawdown_gbp": None,
                                "invalidated_positions": 0,
                                "blocked_sessions": None,
                                "reason": design["costs"]["C4"]["reason"],
                            }
                            cell.update(
                                status="BLOCKED",
                                reason=design["costs"]["C4"]["reason"],
                                overall=unavailable,
                                splits={s["name"]: dict(unavailable) for s in design["splits"]},
                            )
                        else:
                            records = list(read_rows(db, key))
                            cell.update(
                                status="INCONCLUSIVE_CAPITAL_PATH"
                                if not states[key]["known"]
                                else "EVALUATED",
                                final_known_cash=states[key]["cash"],
                                overall=summarize(records, "all", design),
                                splits={
                                    s["name"]: summarize(records, s["name"], design)
                                    for s in design["splits"]
                                },
                            )
                        cells.append(cell)
        summary = sealed_summary(cells, design)
        resources = {
            "seconds": round(time.monotonic() - started, 2),
            "utc_dates": day_count,
            "peak_single_process_bytes": peak_memory,
            "workers": workers,
            "database_bytes": (output / "results.sqlite3").stat().st_size,
        }
        (output / "runtime.json").write_text(json.dumps(resources, indent=2), encoding="utf-8")
        summary["runtime_evidence"] = "runtime.json"
        summary["design_sha"] = DESIGN_SHA
        summary["source_hash"] = source_hash
        summary["checksum"] = digest({k: v for k, v in summary.items() if k != "checksum"})
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        write_csv(summary, output / "cell-matrix.csv")
        return summary
    except BaseException as exc:
        db.commit()
        (output / "failure.json").write_text(
            json.dumps(
                {
                    "type": type(exc).__name__,
                    "reason": str(exc),
                    "verified_dates": day_count,
                    "source_hash": source_hash,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
    finally:
        db.close()


def write_csv(summary, path):
    with Path(path).open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "cell",
                "status",
                "split",
                "trades",
                "complete_case_net_gbp",
                "portfolio_net_gbp",
                "expectancy_gbp",
                "profit_factor",
                "drawdown_gbp",
                "invalidated",
                "blocked_sessions",
            )
        )
        for cell in summary["cells"]:
            for split in ("development", "validation", "holdout"):
                metrics = cell["splits"].get(split, {})
                writer.writerow(
                    (
                        cell["cell"],
                        cell["status"],
                        split,
                        metrics.get("trade_count"),
                        metrics.get("complete_case_net_pnl_gbp"),
                        metrics.get("portfolio_net_pnl_gbp"),
                        metrics.get("expectancy_gbp"),
                        metrics.get("profit_factor"),
                        metrics.get("maximum_marked_drawdown_gbp"),
                        metrics.get("invalidated_positions"),
                        metrics.get("blocked_sessions"),
                    )
                )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / "local/verification"):
        raise AtlasError("FOREX_RESEARCH_OUTPUT_MUST_BE_LOCAL_VERIFICATION")
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    execute(plan, output, workers=args.workers)


if __name__ == "__main__":
    main()
