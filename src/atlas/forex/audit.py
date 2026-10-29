"""Offline, read-only full-corpus audit; never repairs or fabricates source candles.

Run with python -m atlas.forex.audit --plan PATH --output NEW_DIRECTORY.
Quality-v1 remains immutable provenance. Additional diagnostics live in this audit.
"""

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.collection import CollectionLedger, report, validate_plan
from atlas.forex.models import CandlePlan
from atlas.forex.quality import NY
from atlas.market_data.contracts import digest


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inventory(root):
    """Protect every existing artifact in data/research trees, plus local databases."""
    local = root / "local"
    paths = set(local.glob("*.sqlite3"))
    for name in ("oanda", "market-data", "research"):
        paths.update(p for p in (local / name).rglob("*") if p.is_file())
    result = {}
    for path in sorted(paths):
        try:
            value = file_hash(path)
        except PermissionError:
            value = "UNREADABLE_PERMISSION_DENIED"
        result[str(path.relative_to(root))] = value
    return result


def quantiles(counts):
    total = sum(counts.values())
    if not total:
        return {}
    ordered = sorted((Decimal(v), n) for v, n in counts.items())
    result = {"count": total, "min": str(ordered[0][0]), "max": str(ordered[-1][0])}
    result["mean"] = str(sum(v * n for v, n in ordered) / total)
    for label, fraction in (("p50", ".5"), ("p90", ".90"), ("p95", ".95"), ("p99", ".99")):
        rank = int((Decimal(total) * Decimal(fraction)).to_integral_value(rounding="ROUND_CEILING"))
        seen = 0
        for value, count in ordered:
            seen += count
            if seen >= rank:
                result[label] = str(value)
                break
    return result


def gap_candidate(start, end):
    """Hypotheses only: neither labels nor shared absences prove a closure."""
    local = start.astimezone(NY)
    if (local.month, local.day) in ((12, 24), (12, 25), (12, 31), (1, 1)):
        return "HOLIDAY_CANDIDATE_UNVERIFIED"
    if local.hour == 17 and local.minute <= 15:
        return "ROLLOVER_REOPEN_CANDIDATE_UNVERIFIED"
    if end - start <= timedelta(minutes=2):
        return "SHORT_NO_UPDATE_OR_OUTAGE_UNRESOLVED"
    return "UNRESOLVED"


def scan(plan, output, *, progress=lambda n: None):
    validate_plan(plan)
    if plan["stage"] != "B":
        raise AtlasError("FULL_AUDIT_REQUIRES_STAGE_B")
    ledger = CollectionLedger(plan["output"], read_only=True)
    integrity = {}
    for name, store in (("historical", ledger.store), ("collection", ledger)):
        with store.connect() as db:
            integrity[name] = [r[0] for r in db.execute("PRAGMA integrity_check")]
            if integrity[name] != ["ok"]:
                raise AtlasError("FOREX_AUDIT_DATABASE_INTEGRITY_FAILED")
    expected = {r["key"] for r in plan["partitions"]}
    with ledger.connect() as db:
        ledger_keys = {r[0] for r in db.execute("SELECT key FROM partitions")}
    with ledger.store.connect() as db:
        jobs = {r[0]: r[1] for r in db.execute("SELECT id,status FROM fx_jobs")}
        datasets = {r[0] for r in db.execute("SELECT job FROM fx_datasets")}
        orphan_pages = db.execute(
            "SELECT count(*) FROM fx_pages WHERE job NOT IN (SELECT id FROM fx_jobs)"
        ).fetchone()[0]
        pages = db.execute(
            "SELECT count(*),min(fetched_at),max(fetched_at) FROM fx_pages"
        ).fetchone()
    symbols, previous, spread_counts, shared_gaps = {}, {}, {}, {}
    daily, anomalies, proofs = [], [], []
    counts = Counter()
    with (output / "partitions.jsonl").open("x", encoding="utf-8") as details:
        for number, row in enumerate(plan["partitions"], 1):
            value, manifest, bars = ledger.verified(row)
            p = CandlePlan.model_validate(manifest["plan"])
            name = p.instrument.name
            stats = symbols.setdefault(
                name,
                {
                    "bars": 0,
                    "partitions": 0,
                    "first": None,
                    "last": None,
                    "missing": Counter(),
                    "warnings": Counter(),
                    "gap_candidates": Counter(),
                    "missing_by_ny_clock": Counter(),
                    "monthly_bars": Counter(),
                    "zero_activity": 0,
                    "flat_mid_ohlc": 0,
                    "mid_close_arithmetic_residual_gt_one_display_unit": 0,
                    "longest_identical_close_run": 0,
                },
            )
            stats["partitions"] += 1
            stats["bars"] += len(bars)
            stats["monthly_bars"][p.start.strftime("%Y-%m")] += len(bars)
            quality = value["quality"]
            stats["missing"].update(quality["counts"])
            stats["warnings"].update(quality["warnings"])
            counts[value["status"]] += 1
            gaps = []
            for gap in quality["missing_intervals"]:
                if gap["kind"] != "UNEXPLAINED_MISSING_MINUTE":
                    continue
                a, z = datetime.fromisoformat(gap["start"]), datetime.fromisoformat(gap["end"])
                minutes = int((z - a).total_seconds() / 60)
                candidate = gap_candidate(a, z)
                stats["gap_candidates"][candidate] += minutes
                cursor = a
                while cursor < z:
                    stats["missing_by_ny_clock"][cursor.astimezone(NY).strftime("%H:%M")] += 1
                    cursor += timedelta(minutes=1)
                gaps.append(gap | {"minutes": minutes, "candidate": candidate})
                shared_gaps.setdefault((gap["start"], gap["end"]), []).append(name)
            daily.append(
                {
                    "instrument": name,
                    "date": str(p.start.date()),
                    "bars": len(bars),
                    "unexplained": sum(g["minutes"] for g in gaps),
                }
            )
            for bar in bars:
                if stats["first"] is None:
                    stats["first"] = bar.start.isoformat()
                stats["last"] = bar.start.isoformat()
                stats["zero_activity"] += bar.activity_count == 0
                stats["flat_mid_ohlc"] += bar.mid.h == bar.mid.l
                residual = abs(bar.mid.c - (bar.bid.c + bar.ask.c) / 2)
                stats["mid_close_arithmetic_residual_gt_one_display_unit"] += (
                    residual > Decimal(10) ** -p.instrument.display_precision
                )
                spread = (bar.ask.c - bar.bid.c) / p.instrument.pip
                spread_counts.setdefault(name, Counter())[str(spread)] += 1
                prior, run = previous.get(name, (None, 0))
                if prior is not None and bar.start <= prior.start:
                    raise AtlasError("FOREX_AUDIT_CROSS_PARTITION_TIME_REGRESSION")
                contiguous = prior is not None and bar.start - prior.start == timedelta(minutes=1)
                run = run + 1 if contiguous and bar.mid.c == prior.mid.c else 1
                stats["longest_identical_close_run"] = max(
                    stats["longest_identical_close_run"], run
                )
                move = abs(bar.mid.o - prior.mid.c) / p.instrument.pip if prior else Decimal(0)
                width = (bar.mid.h - bar.mid.l) / p.instrument.pip
                if move > 50 or width > 100 or spread < 0 or spread > 25:
                    anomalies.append(
                        {
                            "instrument": name,
                            "time": bar.start.isoformat(),
                            "previous_time": prior.start.isoformat() if prior else None,
                            "contiguous": contiguous,
                            "move_pips": str(move),
                            "range_pips": str(width),
                            "spread_pips": str(spread),
                            "dataset": value["dataset"],
                            "candle": bar.model_dump(mode="json"),
                        }
                    )
                previous[name] = bar, run
            proofs.append([row["key"], value["dataset"], quality["checksum"]])
            details.write(
                json.dumps(
                    {
                        "key": row["key"],
                        "dataset": value["dataset"],
                        "instrument": name,
                        "date": str(p.start.date()),
                        "quality": quality,
                        "gap_candidates": gaps,
                    }
                )
                + "\n"
            )
            if number % 100 == 0:
                progress(number)
    scope = {
        "missing_ledger_keys": sorted(expected - ledger_keys),
        "extra_ledger_keys": sorted(ledger_keys - expected),
        "missing_jobs": sorted(expected - jobs.keys()),
        "extra_jobs": sorted(jobs.keys() - expected),
        "missing_datasets": sorted(expected - datasets),
        "extra_datasets": sorted(datasets - expected),
        "orphan_pages": orphan_pages,
        "nonaccepted_jobs": {k: v for k, v in jobs.items() if v != "ACCEPTED"},
    }
    tail_start, tail_end = map(
        datetime.fromisoformat, (plan["effective_end"], plan["requested_end"])
    )
    tail = int((tail_end - tail_start).total_seconds() / 60)
    shared = [
        {"start": a, "end": z, "instruments": names}
        for (a, z), names in shared_gaps.items()
        if len(names) == len(symbols)
    ]
    result = {
        "schema": "atlas-forex-full-audit-v1",
        "plan_hash": plan["plan_hash"],
        "integrity": integrity,
        "scope": scope,
        "counts": dict(counts),
        "source_pages": {"count": pages[0], "first_fetch": pages[1], "last_fetch": pages[2]},
        "target_start": plan["target_start"],
        "effective_end": plan["effective_end"],
        "requested_end": plan["requested_end"],
        "uncollected_tail_minutes_per_pair": tail,
        "bars": sum(s["bars"] for s in symbols.values()),
        "symbols": symbols,
        "indicative_close_spread_pips": {s: quantiles(c) for s, c in spread_counts.items()},
        "worst_missing_days": sorted(daily, key=lambda r: r["unexplained"], reverse=True)[:40],
        "shared_exact_gap_intervals": shared,
        "anomalies": anomalies,
        "dataset_proof_digest": digest(proofs),
        "source_integrity": "PASS" if not any(scope.values()) else "FAIL_SCOPE",
        "realistic_backtest_ready": False,
        "readiness": "CONDITIONAL_RESEARCH_ONLY_PENDING_GAP_AND_EXECUTION_POLICIES",
        "limitations": [
            "Gap candidate labels are hypotheses, not verified historical closures.",
            "Original publication times unknown; explicit assumed bar-close availability required.",
            "Bid/ask candle endpoints are indicative, not synchronized executable quotes.",
            "No gap filling; exclude affected decision windows or use explicit gap-aware replay.",
            "Globally merge instrument streams before portfolio decisions.",
            "Specify slippage, spread, commissions, financing and timestamp-safe GBP conversion.",
            "Intrabar stop/target order and gap-through fills require conservative explicit rules.",
            "Frozen tail is outside collected scope; do not claim full requested target coverage.",
        ],
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    output = Path(args.output).resolve()
    # Evidence must never be placed inside a protected source tree.
    if not output.is_relative_to(root / "local" / "verification"):
        raise AtlasError("AUDIT_OUTPUT_REQUIRES_LOCAL_VERIFICATION_DIRECTORY")
    output.mkdir(parents=True, exist_ok=False)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    baseline = inventory(root)
    (output / "source-hashes-before.json").write_text(
        json.dumps(baseline, indent=2), encoding="utf-8"
    )
    result = scan(plan, output, progress=lambda n: print(f"Verified {n} partitions", flush=True))
    # Independently reproduce the saved collection report as well as the new diagnostics.
    recomputed = report(plan)
    saved_path = Path(args.plan).parent / "final-quality.json"
    result["saved_collection_report_matches"] = (
        json.loads(saved_path.read_text(encoding="utf-8")) == recomputed
        if saved_path.is_file()
        else None
    )
    result["git_head"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    after = inventory(root)
    unreadable = [p for p, v in baseline.items() if v == "UNREADABLE_PERMISSION_DENIED"]
    result["preservation"] = {
        "files": len(baseline),
        "readable_files_unchanged": baseline == after,
        "unreadable_not_verified": unreadable,
    }
    if baseline != after:
        raise AtlasError("AUDIT_SOURCE_ARTIFACTS_CHANGED")
    result["checksum"] = digest(result)
    (output / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "bars",
                    "source_integrity",
                    "saved_collection_report_matches",
                    "preservation",
                    "readiness",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
