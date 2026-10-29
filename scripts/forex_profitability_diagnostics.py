"""Attribute sealed Phase 6B-3 trades without creating signals, orders or replaying strategies."""

import argparse
import csv
import gzip
import io
import json
import sqlite3
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from itertools import groupby
from pathlib import Path

from atlas.forex.audit import file_hash
from atlas.forex.collection import CollectionLedger
from atlas.forex.diagnostics import CloseQuote, attribution, spread_bet, unleveraged
from atlas.market_data.contracts import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
D = Decimal
MINUTE = timedelta(minutes=1)


def quote(bar):
    return CloseQuote(bar.bid.c, bar.ask.c)


def calculations(spec):
    rows = []
    for product in spec["spread_bets"]:
        for distance in spec["illustrative_stop_points"]:
            args = {k: D(v) for k, v in product["inputs"].items()}
            values = spread_bet(**args, stop_points=D(distance))
            rows.append(
                {
                    "provider": product["provider"],
                    "pair": product["pair"],
                    "stop_points": distance,
                    "spec_status": product["spec_status"],
                    **{k: str(v) if isinstance(v, D) else v for k, v in values.items()},
                }
            )
    fx = []
    for product in spec["unleveraged"]:
        values = unleveraged(**{k: D(v) for k, v in product["inputs"].items()})
        fx.append(
            {
                "pair": product["pair"],
                "platform": product["platform"],
                **{k: str(v) if isinstance(v, D) else v for k, v in values.items()},
            }
        )
    return {"assumptions": spec["assumptions"], "spread_bets": rows, "unleveraged": fx}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / "local/verification/phase6b4"):
        raise ValueError("Derived diagnostics must remain in Phase 6B-4 local verification")
    output.mkdir(exist_ok=False)
    spec_path = ROOT / "examples/forex-feasibility-phase6b4.json"
    specification = json.loads(spec_path.read_text())
    spec_hash = file_hash(spec_path)
    source_hashes = {
        str(p.relative_to(ROOT)): file_hash(p)
        for p in (Path(__file__), ROOT / "src/atlas/forex/diagnostics.py")
    }
    (output / "calculation-inputs.json").write_text(json.dumps(specification, indent=2) + "\n")
    prior = ROOT / "local/verification/phase6b3-v1"
    acceptance = json.loads((prior / "acceptance.json").read_text())
    summary = json.loads((prior / "results/summary.json").read_text())
    if digest({k: v for k, v in acceptance.items() if k != "checksum"}) != acceptance["checksum"]:
        raise ValueError("Prior acceptance seal failed")
    if summary["checksum"] != acceptance["summary_checksum"]:
        raise ValueError("Unexpected research summary")
    db_path = prior / "results/results.sqlite3"
    if file_hash(db_path) != acceptance["local_result_database_sha256"]:
        raise ValueError("Frozen research database changed")
    if digest({k: v for k, v in summary.items() if k != "checksum"}) != summary["checksum"]:
        raise ValueError("Summary seal failed")
    plan = json.loads((ROOT / "local/oanda/phase6b1/stage-b-plan.json").read_text())
    ledger = CollectionLedger(plan["output"], read_only=True)
    days = {
        day: list(rows)
        for day, rows in groupby(plan["partitions"], lambda r: r["candle_plan"]["start"][:10])
    }
    totals = defaultdict(lambda: defaultdict(D))
    exits = defaultdict(lambda: {"count": 0, "recorded_net_gbp": D(0), "holding_minutes": D(0)})
    entry_capital = defaultdict(lambda: defaultdict(D))
    counts = Counter()
    source_partitions = 0
    session_count = 0
    date_count = 0
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA cache_size=-8192")
    with (output / "trade-attribution.jsonl.gz").open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as detail:
                with localcontext() as ctx:
                    ctx.prec = 34
                    cursor = db.execute(
                        "SELECT day,cell,payload,checksum FROM sessions ORDER BY day,cell"
                    )
                    for date, records in groupby(cursor, lambda r: r[0]):
                        rows = []
                        for _, cell, payload, checksum in records:
                            row = json.loads(zlib.decompress(payload))
                            if digest(row) != checksum or cell != row["cell"]:
                                raise ValueError("Research row seal failed")
                            rows.append(row)
                        traded = [r for r in rows if r.get("execution", {}).get("trade")]
                        bars = {}
                        if traded:
                            for partition in days[date]:
                                saved = ledger.get(partition["key"])
                                manifest, values = ledger.store.inspect(
                                    saved["dataset"], check_database=False
                                )
                                pair = manifest["plan"]["instrument"]["name"]
                                bars[pair] = {b.start: b for b in values}
                                source_partitions += 1
                        for row in traded:
                            execution = row["execution"]
                            if (
                                digest({k: v for k, v in execution.items() if k != "checksum"})
                                != execution["checksum"]
                            ):
                                raise ValueError("Execution seal failed")
                            t = execution["trade"]
                            p = t["position"]
                            pair = p["intent"]["instrument"]
                            opened = datetime.fromisoformat(p["opened_at"])
                            closed = datetime.fromisoformat(t["exited_at"])
                            entry, exit_ = bars[pair][opened - MINUTE], bars[pair][closed - MINUTE]
                            gbp = bars["GBP_USD"][closed - MINUTE]
                            jpy = bars["USD_JPY"][closed - MINUTE] if pair == "USD_JPY" else None
                            values = attribution(
                                t,
                                execution["configuration"]["settings"]["costs"],
                                quote(entry),
                                quote(exit_),
                                quote(gbp),
                                quote(jpy) if jpy else None,
                            )
                            bridge = execution["cost_bridge"]
                            if values["gross_reference_gbp"] != D(bridge["gross_model_pnl_gbp"]):
                                raise ValueError("Frozen gross-reference bridge mismatch")
                            key = row["cell"], row["split"]
                            counts[key] += 1
                            for name, value in values.items():
                                totals[key][name] += value
                            code = key + (t["reason"],)
                            exits[code]["count"] += 1
                            exits[code]["recorded_net_gbp"] += D(t["net_gbp"])
                            exits[code]["holding_minutes"] += D(
                                str((closed - opened).total_seconds() / 60)
                            )
                            capital = entry_capital[key]
                            capital["planned_risk_gbp_sum"] += D(p["planned_risk_gbp"])
                            capital["reserved_fraction_of_cash_sum"] += D(p["reserved_gbp"]) / D(
                                row["starting_cash"]
                            )
                            capital["planned_risk_below_2_50_count"] += D(
                                p["planned_risk_gbp"]
                            ) < D("2.5")
                            capital["ambiguous_count"] += t["ambiguous"]
                            capital["entry_latency_seconds_sum"] += D(
                                str(
                                    (
                                        opened - datetime.fromisoformat(p["intent"]["signal_at"])
                                    ).total_seconds()
                                )
                            )
                            detail.write(
                                canonical(
                                    {
                                        "cell": row["cell"],
                                        "day": date,
                                        "original_execution_checksum": execution["checksum"],
                                        "reason": t["reason"],
                                        "components": {k: str(v) for k, v in values.items()},
                                    }
                                )
                                + "\n"
                            )
                        if len(rows) != 144:
                            raise ValueError("Missing supported cohort/date")
                        session_count += len(rows)
                        date_count += 1
                        if date_count % 100 == 0:
                            print(
                                json.dumps(
                                    {
                                        "diagnostic_dates": date_count,
                                        "saved_trades_attributed": sum(counts.values()),
                                    }
                                ),
                                flush=True,
                            )
    db.close()
    result = []
    for cell in summary["cells"]:
        for split, metric in cell["splits"].items():
            key = cell["cell"], split
            row = {
                "cell": key[0],
                "split": split,
                "status": cell["status"],
                "trade_count": counts[key],
                "original_metrics": metric,
            }
            if cell["status"] != "BLOCKED":
                if counts[key] != metric["trade_count"] or totals[key]["recorded_net_gbp"] != D(
                    metric["complete_case_net_pnl_gbp"]
                ):
                    raise ValueError("Published trade count/net mismatch")
                row["components"] = {k: str(v) for k, v in sorted(totals[key].items())}
                row["capital_sums"] = {k: str(v) for k, v in sorted(entry_capital[key].items())}
            else:
                row["components"] = None
                row["capital_sums"] = None
            result.append(row)
    if (
        session_count != 105264
        or date_count != 731
        or file_hash(spec_path) != spec_hash
        or any(file_hash(ROOT / p) != h for p, h in source_hashes.items())
        or file_hash(db_path) != acceptance["local_result_database_sha256"]
    ):
        raise ValueError("Diagnostic scope or immutable input changed")
    report = {
        "version": "PHASE6B4_FIXED_TRADE_ATTRIBUTION_V1",
        "source_summary_checksum": summary["checksum"],
        "research_database_sha256": file_hash(db_path),
        "specification_sha256": spec_hash,
        "diagnostic_source_hashes": source_hashes,
        "sessions_verified": session_count,
        "trades_attributed": sum(counts.values()),
        "source_partitions_read": source_partitions,
        "historical_strategy_runs": 0,
        "cells": result,
        "exit_attribution": [
            {
                "cell": k[0],
                "split": k[1],
                "reason": k[2],
                **{n: str(v) if isinstance(v, D) else v for n, v in value.items()},
            }
            for k, value in sorted(exits.items())
        ],
        "capital_calculations": calculations(specification),
    }
    report["checksum"] = digest(report)
    (output / "diagnostics.json").write_text(json.dumps(report, indent=2) + "\n")
    fields = ["cell", "split", "status", "trade_count"] + sorted(
        next(r["components"] for r in result if r["components"])
    )
    with (output / "attribution.csv").open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for row in result:
            w.writerow({k: row[k] for k in fields[:4]} | (row["components"] or {}))
    with (output / "exits.csv").open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, ["cell", "split", "reason", "count", "recorded_net_gbp", "holding_minutes"]
        )
        w.writeheader()
        w.writerows(report["exit_attribution"])
    print(
        json.dumps(
            {k: report[k] for k in ("checksum", "trades_attributed", "historical_strategy_runs")}
        )
    )


if __name__ == "__main__":
    main()
