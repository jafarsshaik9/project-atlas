"""Frozen daily collection plans, separate approvals and resumable bounded-memory runs."""

import hmac
import json
import math
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atlas.config import AtlasError
from atlas.domain import utc
from atlas.forex.access import Permit, signature
from atlas.forex.config import SYMBOLS
from atlas.forex.history import ForexStore, windows
from atlas.forex.models import CandlePlan, FxInstrument
from atlas.forex.quality import SCHEDULE, evaluate, open_minutes
from atlas.market_data.contracts import canonical, digest

START = datetime(2024, 10, 28, tzinfo=UTC)
END = datetime(2026, 10, 29, tzinfo=UTC)


def metadata(evidence):
    if evidence.get("status") != "READ_ONLY_DISCOVERY_COMPLETE" or not evidence.get(
        "discovery", {}
    ).get("configured_account_accessible"):
        raise AtlasError("Successful configured-account discovery evidence required.")
    items = tuple(FxInstrument.model_validate(i) for i in evidence["instruments"])
    if {i.name for i in items} != set(SYMBOLS) or len(items) != len(SYMBOLS):
        raise AtlasError("Discovery must include all three planned pairs.")
    return tuple(sorted(items, key=lambda i: i.name))


def make_plan(
    instruments,
    environment,
    output,
    *,
    now,
    stage="A",
    pilot=None,
    start=START,
    requested_end=END,
    page_minutes=720,
    origin="EXTERNAL_HISTORICAL_MARKET_DATA",
):
    utc(now)
    utc(start)
    utc(requested_end)
    instruments = tuple(sorted(instruments, key=lambda item: item.name))
    if stage not in ("A", "B") or environment not in ("live", "practice"):
        raise AtlasError("Unknown collection stage/environment.")
    if tuple(sorted(i.name for i in instruments)) != tuple(sorted(SYMBOLS)):
        raise AtlasError("Historical collection requires the three planned instruments.")
    effective_end = min(requested_end, now.replace(second=0, microsecond=0))
    if not START <= start < effective_end <= END:
        raise AtlasError("Collection range outside the authorized research target.")
    if stage == "B":
        if pilot is None or pilot["stage"] != "A":
            raise AtlasError("Stage B requires its distinct frozen Stage A plan.")
        validate_plan(pilot)
        start = datetime.fromisoformat(pilot["target_start"])
        requested_end = datetime.fromisoformat(pilot["requested_end"])
        effective_end = datetime.fromisoformat(pilot["effective_end"])
        if (
            environment != pilot["environment"]
            or str(Path(output).resolve()) != pilot["output"]
            or [i.model_dump(mode="json") for i in instruments] != pilot["instruments"]
            or origin != pilot["origin"]
            or page_minutes != pilot["page_minutes"]
        ):
            raise AtlasError("Stage B cannot change pilot metadata, destination or origin.")
    dates, cursor = [], start
    while cursor < effective_end:
        end = min(cursor.replace(hour=0, minute=0) + timedelta(days=1), effective_end)
        dates.append((cursor, end, open_minutes(cursor, end)))
        cursor = end
    if stage == "A":
        # Five full weekday UTC dates starting near the oldest requested history.
        dates = [
            d for d in dates if d[0].weekday() < 5 and d[1] - d[0] == timedelta(days=1) and d[2]
        ][:5]
        if len(dates) != 5:
            raise AtlasError("Five completed weekday UTC dates required for Stage A.")
    partitions = []
    for begin, end, expected in dates:
        for inst in instruments:
            p = CandlePlan(
                environment=environment,
                instrument=inst,
                start=begin,
                end=end,
                as_of=end,
                origin=origin,
                components="MBA",
                page_minutes=page_minutes,
                empty_policy="QUARANTINE" if expected else "DOCUMENTED_CLOSURE_ASSUMPTION",
            )
            value = p.model_dump(mode="json")
            partitions.append(
                {
                    "key": digest(value),
                    "candle_plan": value,
                    "expected_open_minutes": expected,
                    "requests": len(list(windows(p))),
                }
            )
    requests = sum(p["requests"] for p in partitions)
    expected_bars = sum(p["expected_open_minutes"] for p in partitions)
    result = {
        "schema": "oanda-collection-plan-v1",
        "stage": stage,
        "environment": environment,
        "origin": origin,
        "target_start": start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "effective_end": effective_end.isoformat(),
        "output": str(Path(output).resolve()),
        "instruments": [i.model_dump(mode="json") for i in instruments],
        "components": "MBA",
        "granularity": "M1",
        "smooth": False,
        "page_minutes": page_minutes,
        "schedule": SCHEDULE,
        "pilot_hash": pilot["plan_hash"] if pilot else None,
        "partitions": partitions,
        "initial_requests": requests,
        "request_cap": requests * 3,
        "expected_bars_assuming_published_hours": expected_bars,
        "estimated_storage_bytes": expected_bars * 1200,
        "minimum_free_bytes": max(100_000_000, expected_bars * 2400),
        "estimated_minimum_seconds": math.ceil(requests * 61 / 60),
    }
    return result | {"plan_hash": digest(result)}


def validate_plan(plan):
    try:
        body = dict(plan)
        claimed = body.pop("plan_hash")
        if digest(body) != claimed or plan["schema"] != "oanda-collection-plan-v1":
            raise ValueError
        if (
            plan["stage"] not in ("A", "B")
            or plan["components"] != "MBA"
            or plan["smooth"] is not False
            or plan["schedule"] != SCHEDULE
        ):
            raise ValueError
        start, end = (
            datetime.fromisoformat(plan["target_start"]),
            datetime.fromisoformat(plan["effective_end"]),
        )
        utc(start)
        utc(end)
        requested = datetime.fromisoformat(plan["requested_end"])
        utc(requested)
        if not end <= requested <= END or len(plan["instruments"]) != len(SYMBOLS):
            raise ValueError
        if not START <= start < end <= END or len(plan["partitions"]) > 2300:
            raise ValueError
        if plan["environment"] not in ("live", "practice") or plan["granularity"] != "M1":
            raise ValueError
        instruments = {i["name"]: FxInstrument.model_validate(i) for i in plan["instruments"]}
        if set(instruments) != set(SYMBOLS):
            raise ValueError
        expected_ranges, cursor = [], start
        while cursor < end:
            stop = min(cursor.replace(hour=0, minute=0) + timedelta(days=1), end)
            if plan["stage"] == "B" or (
                cursor.weekday() < 5 and stop - cursor == timedelta(days=1)
            ):
                expected_ranges.extend((name, cursor, stop) for name in sorted(SYMBOLS))
                if plan["stage"] == "A" and len(expected_ranges) == 15:
                    break
            cursor = stop
        actual_ranges = []
        keys, requests, previous = set(), 0, {}
        for row in plan["partitions"]:
            p = CandlePlan.model_validate(row["candle_plan"])
            if (
                row["key"] != digest(p.model_dump(mode="json"))
                or row["key"] in keys
                or p.components != "MBA"
                or p.environment != plan["environment"]
                or p.origin != plan["origin"]
                or p.instrument != instruments[p.instrument.name]
                or p.as_of != p.end
                or p.page_minutes != plan["page_minutes"]
                or not start <= p.start < p.end <= end
                or p.end - p.start > timedelta(days=1)
                or p.start < previous.get(p.instrument.name, start)
            ):
                raise ValueError
            keys.add(row["key"])
            previous[p.instrument.name] = p.end
            actual_ranges.append((p.instrument.name, p.start, p.end))
            expected = open_minutes(p.start, p.end)
            required = len(list(windows(p)))
            if row["expected_open_minutes"] != expected or row["requests"] != required:
                raise ValueError
            requests += required
        if actual_ranges != expected_ranges:
            raise ValueError
        expected_bars = sum(r["expected_open_minutes"] for r in plan["partitions"])
        if (
            plan["expected_bars_assuming_published_hours"] != expected_bars
            or plan["estimated_storage_bytes"] != expected_bars * 1200
            or plan["minimum_free_bytes"] != max(100_000_000, expected_bars * 2400)
        ):
            raise ValueError
        if (
            not requests
            or plan["request_cap"] != requests * 3
            or plan["initial_requests"] != requests
        ):
            raise ValueError
        if plan["stage"] == "A" and (len(keys) != 15 or plan["pilot_hash"] is not None):
            raise ValueError
        if plan["stage"] == "B" and (
            not isinstance(plan["pilot_hash"], str) or len(plan["pilot_hash"]) != 64
        ):
            raise ValueError
        return plan
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AtlasError("OANDA_COLLECTION_PLAN_INVALID") from None


def approval_phrase(plan):
    return f"APPROVE OANDA {plan['environment'].upper()} STAGE {plan['stage']} {plan['plan_hash']}"


def approve(config, plan, confirmation, *, now, pilot_report=None):
    validate_plan(plan)
    if config.environment != plan["environment"] or confirmation != approval_phrase(plan):
        raise AtlasError("OANDA_COLLECTION_CONFIRMATION_MISMATCH")
    if plan["stage"] == "B" and (
        pilot_report is None
        or pilot_report["plan_hash"] != plan["pilot_hash"]
        or pilot_report["status"] != "COMPLETE_REVIEW_REQUIRED"
    ):
        raise AtlasError("Successful Stage A quality review required before Stage B approval.")
    record = {
        "schema": "oanda-collection-approval-v1",
        "plan_hash": plan["plan_hash"],
        "stage": plan["stage"],
        "environment": config.environment,
        "components": "MBA",
        "instruments": list(SYMBOLS),
        "target_start": plan["target_start"],
        "effective_end": plan["effective_end"],
        "output": plan["output"],
        "request_cap": plan["request_cap"],
        "created": now,
        "expires": now + 86400,
        "pilot_report_hash": pilot_report["checksum"] if pilot_report else None,
        "budget_id": signature(config, {"collection_plan": plan["plan_hash"]}),
    }
    return record | {"signature": signature(config, record)}


def collection_permit(config, plan, record, *, now, pilot_report=None):
    try:
        validate_plan(plan)
        signed = dict(record)
        actual = signed.pop("signature")
        expected = approve(
            config, plan, approval_phrase(plan), now=record["created"], pilot_report=pilot_report
        )
        if (
            record != expected
            or not record["created"] <= now < record["expires"]
            or not hmac.compare_digest(actual, signature(config, signed))
        ):
            raise ValueError
        allowed = frozenset(
            (f"/v3/instruments/{p.instrument.name}/candles", a.isoformat(), b.isoformat(), "MBA")
            for row in plan["partitions"]
            if row["requests"]
            for p in (CandlePlan.model_validate(row["candle_plan"]),)
            for a, b in windows(p)
        )
        return Permit(
            record["budget_id"],
            config.environment,
            "candles",
            record["request_cap"],
            record["expires"],
            allowed,
        )
    except (KeyError, ValueError, TypeError, AtlasError):
        raise AtlasError("OANDA_COLLECTION_APPROVAL_INVALID_OR_EXPIRED") from None


class CollectionLedger:
    def __init__(self, output):
        self.root = Path(output)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = ForexStore(self.root / "historical.sqlite3")
        self.path = self.root / "collection.sqlite3"
        with self.connect() as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names and names != {"partitions"}:
                raise AtlasError("Foreign collection ledger.")
            db.execute(
                "CREATE TABLE IF NOT EXISTS partitions(key TEXT PRIMARY KEY, "
                "record TEXT NOT NULL, checksum TEXT NOT NULL, symbol TEXT NOT NULL, "
                "start TEXT NOT NULL, end TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS partition_ranges ON partitions(symbol,start,end)"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def get(self, key):
        with self.connect() as db:
            row = db.execute(
                "SELECT record,checksum,symbol,start,end FROM partitions WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if digest(value) != row[1] or value["key"] != key or value["range"] != list(row[2:]):
            raise AtlasError("COLLECTION_CHECKPOINT_CORRUPT")
        return value

    def verified(self, row):
        value = self.get(row["key"])
        if not value or value["status"] not in ("ACCEPTED", "OBSERVED_EMPTY_EXPECTED_CLOSED"):
            raise AtlasError("Incomplete or quarantined partition requires review.")
        manifest, bars = self.store.inspect(value["dataset"], check_database=False)
        p = CandlePlan.model_validate(row["candle_plan"])
        quality = evaluate(p, bars)
        expected_status = "ACCEPTED" if bars else "OBSERVED_EMPTY_EXPECTED_CLOSED"
        if (
            manifest["plan"] != row["candle_plan"]
            or value["range"] != [p.instrument.name, p.start.isoformat(), p.end.isoformat()]
            or quality != value["quality"]
            or quality["critical"]
            or value["status"] != expected_status
        ):
            raise AtlasError("COLLECTION_DATASET_OR_QUALITY_MISMATCH")
        return value, manifest, bars

    def save(self, value):
        previous = self.get(value["key"])
        if previous is not None and previous != value:
            raise AtlasError("IMMUTABLE_COLLECTION_CHECKPOINT_CONFLICT")
        with self.connect() as db:
            symbol, start, end = value["range"]
            if db.execute(
                "SELECT key FROM partitions WHERE symbol=? AND start<? AND end>? AND key!=?",
                (symbol, end, start, value["key"]),
            ).fetchone():
                raise AtlasError("OVERLAPPING_IMMUTABLE_PARTITION_REQUIRES_REVIEW")
            db.execute(
                "INSERT OR IGNORE INTO partitions VALUES(?,?,?,?,?,?)",
                (value["key"], canonical(value), digest(value), symbol, start, end),
            )

    def check_overlap(self, key, scope):
        symbol, start, end = scope
        with self.connect() as db:
            if db.execute(
                "SELECT key FROM partitions WHERE symbol=? AND start<? AND end>? AND key!=?",
                (symbol, end, start, key),
            ).fetchone():
                raise AtlasError("OVERLAPPING_IMMUTABLE_PARTITION_REQUIRES_REVIEW")


@contextmanager
def worker_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=1)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS collection_worker(id INTEGER PRIMARY KEY)")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        yield
    except sqlite3.OperationalError:
        raise AtlasError("OANDA_COLLECTION_WORKER_BUSY_OR_LOCK_UNAVAILABLE") from None
    finally:
        db.rollback()
        db.close()


def run_collection(
    plan, reader, *, lock_path, progress=lambda event: None, checkpoint_hook=lambda: None
):
    validate_plan(plan)
    started = time.monotonic()
    with worker_lock(lock_path):
        ledger = CollectionLedger(plan["output"])
        if shutil.disk_usage(ledger.root).free < plan["minimum_free_bytes"]:
            raise AtlasError("Insufficient collection disk space.")
        with ledger.store.connect() as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AtlasError("Historical database integrity failed.")
        reused = accepted = bars = 0
        for ordinal, row in enumerate(plan["partitions"]):
            p = CandlePlan.model_validate(row["candle_plan"])
            scope = [p.instrument.name, p.start.isoformat(), p.end.isoformat()]
            ledger.check_overlap(row["key"], scope)
            existing = ledger.get(row["key"])
            if existing:
                if existing["range"] != scope or existing["status"] not in (
                    "ACCEPTED",
                    "QUARANTINED",
                    "OBSERVED_EMPTY_EXPECTED_CLOSED",
                ):
                    raise AtlasError("COLLECTION_CHECKPOINT_SCOPE_INVALID")
                if existing["status"] == "QUARANTINED":
                    raise AtlasError("QUARANTINED_PARTITION_REQUIRES_REVIEW")
                if existing["dataset"]:
                    existing, _, source = ledger.verified(row)
                    reused += 1
                else:
                    raise AtlasError("COLLECTION_DATASET_MISSING")
            else:
                dataset, source = None, ()
                if row["requests"]:
                    try:
                        dataset = ledger.store.collect(p, reader)
                        _, source = ledger.store.inspect(dataset, check_database=False)
                    except AtlasError as exc:
                        if "QUARANTINED" in str(exc):
                            ledger.save(
                                {
                                    "key": row["key"],
                                    "range": scope,
                                    "status": "QUARANTINED",
                                    "dataset": None,
                                    "quality": None,
                                }
                            )
                        raise
                quality = evaluate(p, source)
                status = (
                    "QUARANTINED"
                    if quality["critical"]
                    else "ACCEPTED"
                    if source
                    else "OBSERVED_EMPTY_EXPECTED_CLOSED"
                )
                existing = {
                    "key": row["key"],
                    "range": scope,
                    "status": status,
                    "dataset": dataset,
                    "quality": quality,
                }
                # Source is already durable; resume can recover this boundary.
                checkpoint_hook()
                ledger.save(existing)
                if status == "QUARANTINED":
                    raise AtlasError("CRITICAL_PARTITION_QUALITY_FAILURE")
            accepted += existing["status"] == "ACCEPTED"
            bars += existing["quality"]["bars"]
            elapsed = time.monotonic() - started
            progress(
                {
                    "event": "progress",
                    "completed_partitions": ordinal + 1,
                    "remaining_partitions": len(plan["partitions"]) - ordinal - 1,
                    "accepted": accepted,
                    "reused": reused,
                    "bars": bars,
                    "elapsed_seconds": round(elapsed, 2),
                    "estimated_remaining_seconds": round(
                        elapsed / (ordinal + 1) * (len(plan["partitions"]) - ordinal - 1), 2
                    ),
                }
            )
    return report(plan)


def report(plan):
    validate_plan(plan)
    ledger = CollectionLedger(plan["output"])
    counts, warning_counts, missing_counts, hourly, dataset_proofs = {}, {}, {}, {}, []
    previous_closes, boundary_discontinuities = {}, []
    bars = 0
    for row in plan["partitions"]:
        value = ledger.get(row["key"])
        status = value["status"] if value else "PENDING"
        counts[status] = counts.get(status, 0) + 1
        if value is None or status == "QUARANTINED":
            continue
        p = CandlePlan.model_validate(row["candle_plan"])
        value, _, source = ledger.verified(row)
        quality = value["quality"]
        if source:
            previous = previous_closes.get(p.instrument.name)
            if previous:
                move = abs(source[0].mid.o - previous.mid.c) / p.instrument.pip
                if move > 50:
                    boundary_discontinuities.append(
                        {
                            "instrument": p.instrument.name,
                            "previous": previous.start.isoformat(),
                            "next": source[0].start.isoformat(),
                            "absolute_move_pips": str(move),
                        }
                    )
            previous_closes[p.instrument.name] = source[-1]
        dataset_proofs.append([row["key"], value["dataset"], quality["checksum"]])
        bars += quality["bars"]
        for target, items in (
            (warning_counts, quality["warnings"]),
            (missing_counts, quality["counts"]),
        ):
            for key, count in items.items():
                target[key] = target.get(key, 0) + count
        for hour, stats in quality["spread_by_utc_hour"].items():
            key = p.instrument.name + "/" + hour
            if key not in hourly:
                hourly[key] = dict(stats, histogram=list(stats["histogram"]))
            else:
                aggregate = hourly[key]
                aggregate["count"] += stats["count"]
                aggregate["sum"] = str(Decimal(aggregate["sum"]) + Decimal(stats["sum"]))
                aggregate["min"] = str(min(Decimal(aggregate["min"]), Decimal(stats["min"])))
                aggregate["max"] = str(max(Decimal(aggregate["max"]), Decimal(stats["max"])))
                aggregate["histogram"] = [
                    a + b for a, b in zip(aggregate["histogram"], stats["histogram"], strict=True)
                ]
    result = {
        "schema": "oanda-collection-report-v1",
        "plan_hash": plan["plan_hash"],
        "status": "COMPLETE_REVIEW_REQUIRED"
        if not counts.get("PENDING") and not counts.get("QUARANTINED")
        else "INCOMPLETE_OR_QUARANTINED",
        "stage": plan["stage"],
        "counts": counts,
        "bars": bars,
        "missing_minutes": missing_counts,
        "warnings": warning_counts,
        "spread_by_instrument_utc_hour": hourly,
        "dataset_proofs": dataset_proofs,
        "boundary_discontinuities_over_50_pips_review": boundary_discontinuities,
        "historical_pricing": "NOT_PROOF_OF_ACCOUNT_EXECUTABLE_QUOTES",
        "availability": "UNKNOWN",
        "schedule": SCHEDULE,
    }
    return result | {"checksum": digest(result)}


def iter_dataset(plan):
    """Phase 6B-2 streaming interface; validate one complete partition before yielding."""
    from atlas.forex.dataset import partitions

    for identity, manifest, quality, bars in partitions(plan):
        for candle in bars:
            yield {
                "dataset_id": identity,
                "manifest_hash": identity,
                "instrument": manifest["plan"]["instrument"],
                "provider": "oanda-v20",
                "environment": plan["environment"],
                "origin": plan["origin"],
                "candle": candle.model_dump(mode="json"),
                "quality_hash": quality["checksum"],
                "missing_intervals": quality["missing_intervals"],
            }
