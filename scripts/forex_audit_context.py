"""Supplement a completed source audit with gap and collection context, read-only.

Run from the repository root after atlas.forex.audit. No credentials are loaded.
"""

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from atlas.forex.collection import CollectionLedger, validate_plan
from atlas.forex.quality import NY, closure
from atlas.market_data.contracts import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    path = Path(args.plan)
    plan = validate_plan(json.loads(path.read_text(encoding="utf-8")))
    ledger = CollectionLedger(plan["output"], read_only=True)
    buckets = defaultdict(Counter)
    for row in plan["partitions"]:
        record = ledger.get(row["key"])
        for gap in record["quality"]["missing_intervals"]:
            if gap["kind"] != "UNEXPLAINED_MISSING_MINUTE":
                continue
            a, z = map(datetime.fromisoformat, (gap["start"], gap["end"]))
            while a < z:
                t = a.astimezone(NY)
                label = (
                    "holiday_dates"
                    if (t.month, t.day) in ((12, 24), (12, 25), (12, 31), (1, 1))
                    else "NY_17_hour"
                    if t.hour == 17
                    else "other"
                )
                buckets[record["range"][0]][label] += 1
                a += timedelta(minutes=1)
    observed = defaultdict(Counter)
    with ledger.store.connect() as db:
        for plan_text, content in db.execute(
            "SELECT j.plan,p.content FROM fx_pages p JOIN fx_jobs j ON p.job=j.id"
        ):
            name = json.loads(plan_text)["instrument"]["name"]
            for bar in json.loads(content):
                stamp = datetime.fromisoformat(bar["start"])
                if closure(stamp):
                    observed[name][stamp.astimezone(NY).strftime("%a %H:%M")] += 1
    oanda = Path(plan["output"]).parent
    quota = sqlite3.connect((oanda / "quota.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        statuses = quota.execute(
            "SELECT status,count(*) FROM oanda_attempts GROUP BY status"
        ).fetchall()
        unfinished = quota.execute(
            "SELECT count(*) FROM oanda_attempts WHERE finished IS NULL"
        ).fetchone()[0]
    finally:
        quota.close()
    pilot = json.loads((path.parent / "stage-a-plan.json").read_text(encoding="utf-8"))
    discovery = json.loads((oanda / "phase6a-discovery-evidence.json").read_text(encoding="utf-8"))
    result = {
        "plan_hash": plan["plan_hash"],
        "note": "Supplement only; primary audit verifies every page and quality checksum.",
        "unexplained_minutes_by_ny_context": buckets,
        "observed_during_assumed_closure_by_ny_weekday_clock": observed,
        "quota_http_status_counts_including_discovery": statuses,
        "unfinished_requests": unfinished,
        "pilot_hash_matches": plan["pilot_hash"] == pilot["plan_hash"],
        "discovery_metadata_matches": sorted(plan["instruments"], key=lambda x: x["name"])
        == sorted(discovery["instruments"], key=lambda x: x["name"]),
    }
    result["checksum"] = digest(result)
    with Path(args.output).open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
