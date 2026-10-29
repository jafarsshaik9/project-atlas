"""Streaming enrichment of a completed full audit, with one record per missing minute."""

import argparse
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.audit import file_hash, quantiles
from atlas.forex.audit_calendar import LONDON, VERSION, classification, near_dst, session
from atlas.forex.collection import CollectionLedger, validate_plan
from atlas.forex.models import CandlePlan
from atlas.forex.quality import NY, closure, minute_times
from atlas.market_data.contracts import digest


def groups(stamp):
    ny = stamp.astimezone(NY)
    clock = ny.hour * 60 + ny.minute
    values = [
        "ALL",
        "UTC_HOUR/" + str(stamp.hour),
        "MONTH/" + stamp.strftime("%Y-%m"),
        "SESSION/" + session(stamp),
        "NY_HOUR/" + str(ny.hour),
    ]
    if 1005 <= clock < 1050:
        values.append("ROLLOVER_1645_1730_NY")
    if ny.weekday() == 4 and 960 <= clock < 1020:
        values.append("FRIDAY_CLOSE_1600_1700_NY")
    if ny.weekday() == 6 and 1020 <= clock < 1080:
        values.append("SUNDAY_OPEN_1700_1800_NY")
    return values


def enrich(plan, primary, output, *, progress=lambda name: None):
    validate_plan(plan)
    if primary["plan_hash"] != plan["plan_hash"] or primary["source_integrity"] != "PASS":
        raise AtlasError("DETAILS_REQUIRE_SUCCESSFUL_MATCHING_AUDIT")
    body = {k: v for k, v in primary.items() if k != "checksum"}
    if digest(body) != primary["checksum"]:
        raise AtlasError("AUDIT_CHECKSUM_MISMATCH")
    ledger = CollectionLedger(plan["output"], read_only=True)
    protected = (ledger.path, ledger.store.path)
    before = {str(p): file_hash(p) for p in protected}
    gaps = defaultdict(list)
    shared = defaultdict(list)
    summary = defaultdict(lambda: defaultdict(Counter))
    expected_sessions = defaultdict(Counter)
    for row in plan["partitions"]:
        p = CandlePlan.model_validate(row["candle_plan"])
        name = p.instrument.name
        value = ledger.get(row["key"])
        for t in minute_times(p.start, p.end):
            if closure(t) is None:
                expected_sessions[name][session(t)] += 1
        for gap in value["quality"]["missing_intervals"]:
            if gap["kind"] != "UNEXPLAINED_MISSING_MINUTE":
                continue
            a, z = map(datetime.fromisoformat, (gap["start"], gap["end"]))
            duration = int((z - a).total_seconds() / 60)
            summary[name]["gap_interval_duration_counts"][str(duration)] += 1
            for t in minute_times(a, z):
                gaps[name].append((t, a, z, duration))
                shared[t].append(name)
    spreads = defaultdict(lambda: defaultdict(Counter))
    observed_v2 = defaultdict(Counter)
    cases, boundary_changes, largest = [], [], []
    with gzip.open(output / "missing-minutes.jsonl.gz", "xt", encoding="utf-8") as handle:
        for instrument in plan["instruments"]:
            name = instrument["name"]
            pip = Decimal(10) ** instrument["pip_location"]
            pending, index, previous, previous_partition = None, 0, None, None
            missing = sorted(gaps[name])

            def emit_missing(next_bar, previous, missing=missing, name=name):
                nonlocal index
                while index < len(missing) and (
                    next_bar is None or missing[index][0] < next_bar.start
                ):
                    t, a, z, duration = missing[index]
                    ny = t.astimezone(NY)
                    record = {
                        "instrument": name,
                        "utc": t.isoformat(),
                        "utc_date": str(t.date()),
                        "utc_hour": t.hour,
                        "new_york": ny.isoformat(),
                        "session": session(t),
                        "near_rollover": "ROLLOVER_1645_1730_NY" in groups(t),
                        "near_us_dst": near_dst(t, NY),
                        "near_uk_dst": near_dst(t, LONDON),
                        "original": "UNEXPLAINED_MISSING_MINUTE",
                        "classification_v2": classification(t, observed=False),
                        "classification_version": VERSION,
                        "shared_with": [s for s in shared[t] if s != name],
                        "unexplained_interval_start": a.isoformat(),
                        "unexplained_interval_end": z.isoformat(),
                        "unexplained_interval_minutes": duration,
                        "previous": previous.model_dump(mode="json") if previous else None,
                        "next": next_bar.model_dump(mode="json") if next_bar else None,
                        "entire_unobserved_span_minutes": int(
                            (next_bar.start - previous.start).total_seconds() / 60 - 1
                        )
                        if previous and next_bar
                        else None,
                    }
                    for dimension, key in (
                        ("month", t.strftime("%Y-%m")),
                        ("utc_hour", str(t.hour)),
                        ("session", session(t)),
                        ("duration_minutes", str(duration)),
                        ("shared_instrument_count", str(len(shared[t]))),
                        ("classification_v2", record["classification_v2"]),
                    ):
                        summary[name][dimension][key] += 1
                    handle.write(json.dumps(record) + "\n")
                    if t == a:
                        largest.append(
                            {k: v for k, v in record.items() if k not in ("previous", "next")}
                        )
                    index += 1

            for row in plan["partitions"]:
                if row["candle_plan"]["instrument"]["name"] != name:
                    continue
                item = ledger.get(row["key"])
                _, bars = ledger.store.inspect(item["dataset"], check_database=False)
                for bar in bars:
                    emit_missing(bar, previous)
                    if pending is not None:
                        pending["next"] = bar.model_dump(mode="json")
                        pending["next_open_vs_close_pips"] = str(
                            abs(bar.mid.o - previous.mid.c) / pip
                        )
                        pending["next_is_contiguous"] = bar.start - previous.start == timedelta(
                            minutes=1
                        )
                        pending = None
                    spread = (bar.ask.c - bar.bid.c) / pip
                    for group in groups(bar.start):
                        spreads[name][group][str(spread)] += 1
                    observed_v2[name][classification(bar.start, observed=True)] += 1
                    if previous and previous_partition != row["key"]:
                        prior_spread = (previous.ask.c - previous.bid.c) / pip
                        boundary_changes.append(
                            {
                                "instrument": name,
                                "previous": previous.start.isoformat(),
                                "next": bar.start.isoformat(),
                                "previous_spread": str(prior_spread),
                                "next_spread": str(spread),
                                "absolute_change": str(abs(spread - prior_spread)),
                            }
                        )
                    width = (bar.mid.h - bar.mid.l) / pip
                    if width > 100:
                        pending = {
                            "instrument": name,
                            "dataset": item["dataset"],
                            "pip": str(pip),
                            "candle": bar.model_dump(mode="json"),
                            "range_pips": str(width),
                            "spread_pips": str(spread),
                            "session": session(bar.start),
                            "previous": previous.model_dump(mode="json") if previous else None,
                            "previous_is_contiguous": previous is not None
                            and bar.start - previous.start == timedelta(minutes=1),
                            "previous_close_vs_open_pips": str(
                                abs(bar.mid.o - previous.mid.c) / pip
                            )
                            if previous
                            else None,
                            "next": None,
                            "assessment": (
                                "STRUCTURALLY_VALID_VOLATILITY_CANDIDATE_EXTERNALLY_UNVERIFIED"
                            ),
                            "treatment": (
                                "RETAIN; report conservative execution and exclusion sensitivity"
                            ),
                        }
                        cases.append(pending)
                    previous, previous_partition = bar, row["key"]
            emit_missing(None, previous)
            progress(name)
    after = {str(p): file_hash(p) for p in protected}
    if before != after:
        raise AtlasError("DETAILS_SOURCE_CHANGED")
    result = {
        "schema": "atlas-forex-audit-details-v1",
        "primary_audit_checksum": primary["checksum"],
        "classification_version": VERSION,
        "source_hashes": before,
        "source_unchanged": True,
        "missing_summary": summary,
        "expected_open_session_minutes": expected_sessions,
        "largest_unexplained_intervals": sorted(
            largest, key=lambda r: r["unexplained_interval_minutes"], reverse=True
        )[:50],
        "observed_v2": observed_v2,
        "spread_pips": {
            name: {g: quantiles(v) for g, v in stats.items()} for name, stats in spreads.items()
        },
        "largest_partition_spread_changes": sorted(
            boundary_changes, key=lambda r: Decimal(r["absolute_change"]), reverse=True
        )[:50],
        "large_range_case_studies": cases,
        "decision": "CONDITIONAL PASS",
        "phase6b2": "AWAIT_USER_REVIEW; IMPLEMENT_AND_TEST_CONTRACT_BEFORE_REPLAY",
    }
    result["checksum"] = digest(result)
    with (output / "details.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--audit-directory", required=True)
    args = parser.parse_args(argv)
    directory = Path(args.audit_directory)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    primary = json.loads((directory / "audit.json").read_text(encoding="utf-8"))
    result = enrich(plan, primary, directory, progress=lambda s: print(s + " enriched", flush=True))
    print(
        json.dumps(
            {"decision": result["decision"], "cases": len(result["large_range_case_studies"])}
        )
    )


if __name__ == "__main__":
    main()
