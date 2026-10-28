"""Manifest-bound full collection. Dry runs never load credentials or activate HTTP."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from atlas.alpaca_collection import plan_alpaca
from atlas.alpaca_exclusions import load_exclusion
from atlas.config import AtlasError
from atlas.market_data.alpaca import UNIVERSE, AlpacaProvider, feed_for, instrument
from atlas.market_data.alpaca_limits import DEFAULT_QUOTA, AlpacaQuota
from atlas.market_data.calendars import RecordedCalendar
from atlas.market_data.contracts import DownloadRequest, digest
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore
from atlas.research_collection import ROOT, CollectionState, exclusive_worker

PILOT = "31059db0b3252dbd15168065aaae011ac75364ba2fee21cb2ead5b964b1d5f3a"
CONFIRMATION = (
    "I separately confirm applicable account permissions for automated historical research."
)


def public_json(path):
    path = Path(path)
    if path.suffix != ".json" or path.stat().st_size > 20_000_000:
        raise AtlasError("A bounded public JSON record is required.")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def local_path(path):
    path = Path(path)
    resolved = (path if path.is_absolute() else ROOT / path).resolve()
    if not resolved.is_relative_to((ROOT / "local").resolve()):
        raise AtlasError("Collection runtime paths must be inside repository local/.")
    return resolved


def validate_manifest(manifest, *, pilot=False):
    expected = plan_alpaca(
        datetime.fromisoformat(manifest["as_of"]),
        first=datetime.fromisoformat(manifest["first"]).date(),
        last=datetime.fromisoformat(manifest["last"]).date(),
        symbols=tuple(manifest["symbols"]),
        feed=feed_for(manifest["provider"]),
    )
    scope_ok = (
        (
            manifest["provider"] == "alpaca-iex"
            and manifest["symbols"] == ["AAPL"]
            and len(manifest["tasks"]) == 1
        )
        if pilot
        else set(manifest["symbols"]) == set(UNIVERSE)
    )
    if expected != manifest or not scope_ok:
        raise AtlasError("Frozen five-symbol Alpaca manifest is inconsistent.")
    return manifest


def partition_request(task, symbol):
    start, end = datetime.fromisoformat(task["start"]), datetime.fromisoformat(task["end"])
    feed = task["requests"][0]["parameters"]["feed"]
    return DownloadRequest(
        provider="alpaca-" + feed,
        instrument=instrument(symbol, start, end, feed=feed),
        start=start,
        end=end,
    )


def inspect_partition(path, identity, task=None, symbol=None, *, expected_feed=None):
    manifest, quality, bars = HistoricalStore(path, read_only=True).inspect(identity)
    request = DownloadRequest.model_validate(manifest["request"])
    if manifest["state"] != "ACCEPTED" or request.provider not in ("alpaca-sip", "alpaca-iex"):
        raise AtlasError("Reuse requires an accepted Alpaca partition; provider mixing refused.")
    feed = feed_for(request.provider)
    if expected_feed is not None and feed != expected_feed:
        raise AtlasError("SIP and IEX partitions cannot be mixed or relabelled.")
    if manifest["provider_descriptor"] != AlpacaProvider.descriptor_for(feed).model_dump(
        mode="json"
    ):
        raise AtlasError("Existing Alpaca provider policy differs from this collector.")
    if task is not None and (
        request != partition_request(task, symbol) or manifest["calendar"] != task["calendar"]
    ):
        raise AtlasError("Reusable partition differs from the frozen request/calendar.")
    return manifest, quality, bars


def reuse_inventory(paths, manifest):
    wanted = {(t["date"], s): t for t in manifest["tasks"] for s in t["symbols"]}
    found = {}
    for path in sorted(set(map(str, paths))):
        store = HistoricalStore(path, read_only=True)
        with store.connect() as db:
            identities = [r[0] for r in db.execute("SELECT dataset_id FROM md_datasets")]
        for identity in identities:
            proof, _, bars = inspect_partition(
                path, identity, expected_feed=feed_for(manifest["provider"])
            )
            req = DownloadRequest.model_validate(proof["request"])
            symbol = req.instrument.mapping(req.provider, req.start, req.end).symbol
            day = req.start.astimezone(ZoneInfo("America/New_York")).date()
            key = (str(day), symbol)
            if key not in wanted:
                continue
            inspect_partition(path, identity, wanted[key], symbol)
            ref = {
                "store": str(Path(path).resolve()),
                "dataset_id": identity,
                "checksum": digest(proof),
                "bars": len(bars),
            }
            if key in found and found[key]["dataset_id"] != identity:
                raise AtlasError("Ambiguous accepted Alpaca partition; choose one source store.")
            found[key] = ref
    return {day + "/" + symbol: ref for (day, symbol), ref in sorted(found.items())}


def verify_run_plan(plan, manifest):
    copy = dict(plan)
    identity = copy.pop("run_id")
    if digest(copy) != identity or plan["manifest_id"] != manifest["manifest_id"]:
        raise AtlasError("Collection run plan checksum or manifest binding failed.")
    feed = feed_for(manifest["provider"])
    if plan["repository"] != str(ROOT.resolve()) or plan["schema"] != (2 if feed == "iex" else 1):
        raise AtlasError("Collection run plan belongs to another repository.")
    if feed == "iex" and (plan.get("feed") != "iex" or plan.get("mode") not in ("pilot", "full")):
        raise AtlasError("IEX plan must bind its feed and execution scope.")
    for name in ("store", "state", "cache"):
        if str(local_path(plan[name])) != plan[name] or Path(plan[name]).suffix != ".sqlite3":
            raise AtlasError("Collection run plan has an unsafe runtime path.")
        if Path(plan[name]) == DEFAULT_QUOTA.resolve():
            raise AtlasError("Collection runtime files cannot replace the account quota.")
        if feed == "iex" and not Path(plan[name]).is_relative_to(
            (ROOT / "local/market-data/alpaca-iex" / plan["mode"]).resolve()
        ):
            raise AtlasError(
                "IEX runtime files must remain in their separate feed/scope directory."
            )
    if len({plan[k] for k in ("store", "state", "cache")}) != 3:
        raise AtlasError("Collection databases must be separate.")
    tasks = {t["date"]: t for t in manifest["tasks"]}
    for key, ref in plan["reuse"].items():
        if str(local_path(ref["store"])) in {plan[k] for k in ("store", "state", "cache")}:
            raise AtlasError("Reusable source stores must remain separate and read-only.")
        day, symbol = key.split("/")
        proof, _, bars = inspect_partition(
            local_path(ref["store"]), ref["dataset_id"], tasks[day], symbol
        )
        if digest(proof) != ref["checksum"] or len(bars) != ref["bars"]:
            raise AtlasError("Reusable dataset changed after run planning.")
    return plan


def check_permission(path, manifest, run_plan, now):
    if manifest["provider"] == "alpaca-iex":
        from atlas.alpaca_iex_approval import check_iex_permission

        return check_iex_permission(path, manifest, run_plan, now)
    if not Path(path).is_file():
        raise AtlasError(
            "ACCOUNT_PERMISSIONS_UNVERIFIED: separate user confirmation record required."
        )
    record = public_json(path)
    required = {
        "schema",
        "repository",
        "manifest_id",
        "run_id",
        "feed",
        "adjustment",
        "account_permissions",
        "confirmation",
        "confirmed_at",
        "evidence_reference",
    }
    if set(record) != required:
        raise AtlasError("Account permission record has missing or unknown fields.")
    stamp = datetime.fromisoformat(record["confirmed_at"])
    if (
        record["schema"] != 1
        or record["repository"] != str(ROOT.resolve())
        or record["manifest_id"] != manifest["manifest_id"]
        or record["run_id"] != run_plan["run_id"]
        or record["feed"] != "sip"
        or record["adjustment"] != "raw"
        or record["account_permissions"] != "USER_CONFIRMED_CHECKED"
        or record["confirmation"] != CONFIRMATION
        or not record["evidence_reference"].strip()
        or stamp.tzinfo is None
        or not timedelta(0) <= now - stamp <= timedelta(days=30)
    ):
        raise AtlasError(
            "Account permissions remain unverified or the confirmation is stale/mismatched."
        )


def preflight(manifest, plan, now):
    if not (ROOT / "pyproject.toml").is_file() or not (ROOT / "src/atlas/main.py").is_file():
        raise AtlasError("Atlas repository markers are missing.")
    if (
        tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
        != "project-atlas"
    ):
        raise AtlasError("Collection is not running in the Atlas project.")
    verify_run_plan(plan, manifest)
    if any(
        datetime.fromisoformat(t["end"]) > now - timedelta(minutes=15) for t in manifest["tasks"]
    ):
        raise AtlasError("Historical session is not yet eligible.")
    required = manifest["estimated_storage_bytes"]["high"] * 2 + 1_000_000_000
    available = shutil.disk_usage(ROOT).free
    if available < required:
        raise AtlasError("Insufficient free space for datasets, WAL and safety reserve.")
    if Path(plan["store"]).exists():
        target = HistoricalStore(plan["store"], read_only=True)
        with target.connect() as db:
            for row in db.execute("SELECT request_json FROM md_jobs"):
                if json.loads(row[0])["provider"] != manifest["provider"]:
                    raise AtlasError("Target store contains another provider; mixing refused.")
    if DEFAULT_QUOTA.exists():
        with sqlite3.connect(DEFAULT_QUOTA.resolve().as_uri() + "?mode=ro", uri=True) as db:
            AlpacaQuota.validate(db)
            last, blocked, pending = db.execute("SELECT last,blocked,pending FROM state").fetchone()
            if pending or now.timestamp() < last:
                raise AtlasError(
                    "Shared Alpaca quota has an uncertain request or clock regression."
                )
            if blocked - now.timestamp() > 300:
                raise AtlasError(
                    "Shared Alpaca cooldown requires later resume; quota was not reset."
                )
    return available


def run_collection(
    manifest,
    plan,
    provider,
    *,
    progress=lambda event: None,
    permission_record=None,
    exclusion_record=None,
):
    """Programmatic callers share the CLI permission gate and worker lock."""
    validate_manifest(manifest, pilot=plan.get("mode") == "pilot")
    verify_run_plan(plan, manifest)
    with exclusive_worker(ROOT / "local/market-data/alpaca-collection.lock"):
        if not isinstance(provider.transport, httpx.MockTransport):
            if provider.transport is not None or provider.quota.path != DEFAULT_QUOTA.resolve():
                raise AtlasError("Real collection requires the shared quota and native transport.")
            preflight(manifest, plan, datetime.now(UTC))
            check_permission(permission_record or "", manifest, plan, datetime.now(UTC))
        return _collect_locked(
            manifest, plan, provider, progress=progress, exclusion_record=exclusion_record
        )


def _collect_locked(
    manifest, plan, provider, *, progress=lambda event: None, exclusion_record=None
):
    exclusion = load_exclusion(exclusion_record, manifest, plan)
    if (
        manifest["provider"] == "alpaca-iex"
        and plan.get("mode") == "pilot"
        and not isinstance(provider.transport, httpx.MockTransport)
        and provider.pilot != "iex-pilot-" + plan["run_id"]
    ):
        raise AtlasError("IEX pilot requires its persistent two-attempt budget.")
    if (
        type(provider) is not AlpacaProvider
        or provider.descriptor.provider_id != manifest["provider"]
        or tuple(provider.symbols) != tuple(manifest["symbols"])
        or provider.cache != Path(plan["cache"]).resolve()
    ):
        raise AtlasError("Collection provider differs from the five-symbol manifest.")
    state = CollectionState(plan["state"])
    definition = json.dumps(plan, sort_keys=True)
    run_key = "run-" + plan["run_id"]
    with state.connect() as db:
        if db.execute(
            "SELECT id FROM collection_tasks WHERE id LIKE 'run-%' AND id!=?", (run_key,)
        ).fetchone():
            raise AtlasError("Collection coordinator belongs to another frozen run plan.")
        db.execute(
            "INSERT OR IGNORE INTO collection_tasks(id,definition,status,identity) "
            "VALUES(?,?,'RUNNING',?)",
            (
                run_key,
                definition,
                json.dumps(
                    {"retries": 0, "elapsed": 0, "initial_attempts": provider.quota.attempt_count()}
                ),
            ),
        )
        saved = dict(db.execute("SELECT * FROM collection_tasks WHERE id=?", (run_key,)).fetchone())
        if saved["definition"] != definition:
            raise AtlasError("Collection state policy drift.")
    stats = json.loads(saved["identity"])
    exclusion_id = exclusion["record_id"] if exclusion else None
    if "exclusion_id" in stats and stats["exclusion_id"] != exclusion_id:
        raise AtlasError("Resume requires the same approved session exclusion record.")
    if exclusion:
        stats["exclusion_id"] = exclusion_id
        state.checkpoint(run_key, identity=json.dumps(stats))
    started, previous_elapsed = time.monotonic(), stats["elapsed"]
    downloaded = 0
    with provider.connect() as db:
        for row in db.execute("SELECT payload,checksum FROM pages"):
            if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                raise AtlasError("Alpaca page-cache checksum mismatch.")
            downloaded += sum(len(rows) for rows in json.loads(row[0])["rows"].values())
    results = []
    total = len(manifest["tasks"]) * len(manifest["symbols"])

    def emit():
        stats["elapsed"] = previous_elapsed + time.monotonic() - started
        state.checkpoint(run_key, identity=json.dumps(stats))
        accepted = sum(r["state"] in ("ACCEPTED", "REUSED") for r in results)
        quarantined = sum(r["state"] == "QUARANTINED" for r in results)
        excluded = sum(r["state"] == "EXCLUDED" for r in results)
        done = {(r["date"], r["symbol"]) for r in results if r["state"] in ("ACCEPTED", "REUSED")}
        gaps = {(r["date"], r["symbol"]) for r in results if r["state"] == "EXCLUDED"}
        excluded_days = sum(
            all((t["date"], s) in gaps for s in t["symbols"]) for t in manifest["tasks"]
        )
        completed_days = sum(
            all((t["date"], s) in done for s in t["symbols"]) for t in manifest["tasks"]
        )
        progress(
            {
                "event": "progress",
                "completed_sessions": completed_days,
                "remaining_sessions": len(manifest["tasks"]) - completed_days - excluded_days,
                "excluded_sessions": excluded_days,
                "excluded_partitions": excluded,
                "coverage_complete": not bool(exclusion),
                "completed_symbols": sum(
                    all((t["date"], s) in done for t in manifest["tasks"])
                    for s in manifest["symbols"]
                ),
                "completed_partitions": accepted,
                "total_partitions": total,
                "bars_downloaded": downloaded,
                "accepted_partitions": accepted,
                "reused_partitions": sum(r["state"] == "REUSED" for r in results),
                "quarantined_partitions": quarantined + excluded,
                "unresolved_quarantined_partitions": quarantined,
                "api_requests_consumed": provider.quota.attempt_count() - stats["initial_attempts"],
                "account_api_requests_total": provider.quota.attempt_count(),
                "retries": stats["retries"],
                "elapsed_seconds": round(stats["elapsed"], 2),
                "estimated_remaining_seconds": round(
                    stats["elapsed"] * (total - accepted - excluded) / (accepted + excluded), 2
                )
                if accepted + excluded
                else None,
            }
        )

    def on_event(event):
        nonlocal downloaded
        if event["event"] == "http_attempt":
            stats["retries"] += int(event["retry"])
        elif event["event"] == "page_committed":
            downloaded += event["bars"]
        emit()

    provider.on_event = on_event
    store = HistoricalStore(plan["store"])
    emit()
    for task in manifest["tasks"]:
        for symbol in task["symbols"]:
            key = task["date"] + "/" + symbol
            identity = digest({"run": plan["run_id"], "partition": key})
            with state.connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO collection_tasks(id,definition,status) "
                    "VALUES(?,?,'PENDING')",
                    (
                        identity,
                        json.dumps({"run": plan["run_id"], "partition": key}, sort_keys=True),
                    ),
                )
            rows = {r["id"]: r for r in state.results()}
            row = rows[identity]
            if json.loads(row["definition"]) != {"run": plan["run_id"], "partition": key}:
                raise AtlasError("Partition checkpoint definition drift.")
            if exclusion and task["date"] == exclusion["date"]:
                ref = exclusion["evidence"][symbol]
                if row["status"] in ("ACCEPTED", "REUSED") or key in plan["reuse"]:
                    raise AtlasError(
                        "An accepted partition cannot be replaced by a session exclusion."
                    )
                if row["status"] in ("QUARANTINED", "EXCLUDED"):
                    proof, quality, bars = HistoricalStore(row["store"], read_only=True).inspect(
                        row["dataset_id"]
                    )
                    if (
                        proof["state"] != "QUARANTINED"
                        or bars
                        or quality.raw_rows
                        or {f.code for f in quality.findings if f.severity == "FAIL"}
                        != {"NO_VALID_BARS"}
                        or DownloadRequest.model_validate(proof["request"])
                        != partition_request(task, symbol)
                        or proof["calendar"] != task["calendar"]
                        or digest(proof) != row["identity"]
                        or (row["status"] == "EXCLUDED" and row["code"] != exclusion_id)
                    ):
                        raise AtlasError("Excluded partition evidence differs from its checkpoint.")
                    # Preserve the original quarantine checkpoint and immutable dataset.
                    state.checkpoint(identity, code=exclusion_id)
                elif row["status"] == "PENDING":
                    state.checkpoint(
                        identity,
                        status="EXCLUDED",
                        dataset_id=ref["dataset_id"],
                        store=str(local_path(ref["store"])),
                        identity=ref["checksum"],
                        code=exclusion_id,
                    )
                else:
                    raise AtlasError("Unresolved partition state cannot be silently excluded.")
                results.append(
                    {
                        "date": task["date"],
                        "symbol": symbol,
                        "state": "EXCLUDED",
                        "dataset_id": row["dataset_id"] or ref["dataset_id"],
                        "store": row["store"] or str(local_path(ref["store"])),
                        "bars": 0,
                        "exclusion_id": exclusion_id,
                    }
                )
                emit()
                continue
            if row["status"] == "EXCLUDED":
                raise AtlasError("Excluded partition requires its approved exclusion record.")
            try:
                if row["status"] in ("ACCEPTED", "REUSED", "QUARANTINED"):
                    proof, quality, bars = HistoricalStore(row["store"], read_only=True).inspect(
                        row["dataset_id"]
                    )
                    if proof["state"] != (
                        "QUARANTINED" if row["status"] == "QUARANTINED" else "ACCEPTED"
                    ):
                        raise AtlasError("Completed partition state drift.")
                    if (
                        DownloadRequest.model_validate(proof["request"])
                        != partition_request(task, symbol)
                        or proof["calendar"] != task["calendar"]
                        or digest(proof) != row["identity"]
                    ):
                        raise AtlasError("Completed partition provenance drift.")
                    status, dataset, source = row["status"], row["dataset_id"], row["store"]
                elif key in plan["reuse"]:
                    ref = plan["reuse"][key]
                    proof, quality, bars = inspect_partition(
                        ref["store"], ref["dataset_id"], task, symbol
                    )
                    if digest(proof) != ref["checksum"]:
                        raise AtlasError("Reusable source checksum changed.")
                    status, dataset, source = "REUSED", ref["dataset_id"], ref["store"]
                else:
                    request = partition_request(task, symbol)
                    dataset, job, _ = download(
                        provider, request, RecordedCalendar(task["calendar"]), store
                    )
                    proof, quality, bars = store.inspect(dataset)
                    status, source = proof["state"], str(store.path)
                    state.checkpoint(identity, job_id=job)
                state.checkpoint(
                    identity,
                    status=status,
                    dataset_id=dataset,
                    store=source,
                    identity=digest(proof),
                    code=None,
                )
                results.append(
                    {
                        "date": task["date"],
                        "symbol": symbol,
                        "state": status,
                        "dataset_id": dataset,
                        "store": source,
                        "bars": len(bars),
                    }
                )
                emit()
                if status == "QUARANTINED":
                    failures = ", ".join(
                        finding.code for finding in quality.findings if finding.severity == "FAIL"
                    )
                    raise AtlasError(
                        f"Partition quarantined: {key}; failures={failures}; "
                        f"valid_bars={quality.valid_bars}; expected_bars={quality.expected_bars}; "
                        f"dataset_id={dataset}. Accepted partitions are preserved. "
                        "Resume stops at this quarantine until it is explicitly resolved."
                    )
            except (AtlasError, ValueError, OSError) as error:
                if rows[identity]["status"] not in ("ACCEPTED", "REUSED", "QUARANTINED"):
                    # Preserve any quarantined checkpoint already committed above.
                    current = {r["id"]: r for r in state.results()}[identity]
                    if current["status"] != "QUARANTINED":
                        state.checkpoint(
                            identity, status="INTERRUPTED", code="COLLECTION_INTERRUPTED"
                        )
                emit()
                raise error
    state.checkpoint(run_key, status="COMPLETED_WITH_GAPS" if exclusion else "COMPLETED")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Alpaca collection: dry-run unless --execute AND confirmed permissions"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-plan")
    parser.add_argument("--feed", choices=("sip", "iex"))
    parser.add_argument(
        "--pilot", action="store_true", help="IEX: one AAPL session, lifetime cap two HTTP attempts"
    )
    parser.add_argument("--reuse-store", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--permission-record")
    parser.add_argument(
        "--exclusion-record",
        help="Reviewed empty-session exclusion; grants no execution permission",
    )
    args = parser.parse_args(argv)
    try:
        manifest = validate_manifest(public_json(local_path(args.manifest)), pilot=args.pilot)
        feed = feed_for(manifest["provider"])
        if args.feed is not None and args.feed != feed:
            raise AtlasError(
                "Selected feed conflicts with frozen manifest; create a separate manifest."
            )
        mode = "pilot" if args.pilot else "full"
        plan_path = local_path(
            args.run_plan
            or (
                f"local/research/two-year/iex-{mode}-run-plan.json"
                if feed == "iex"
                else "local/research/two-year/alpaca-run-plan.json"
            )
        )
        permission_path = local_path(
            args.permission_record
            or (
                f"local/research/two-year/iex-{mode}-approval.json"
                if feed == "iex"
                else "local/research/two-year/alpaca-account-permissions.json"
            )
        )
        if plan_path.exists():
            plan = verify_run_plan(public_json(plan_path), manifest)
            if feed == "iex" and plan["mode"] != mode:
                raise AtlasError("Pilot/full plan scope mismatch.")
            if args.reuse_store:
                raise AtlasError(
                    "Reuse inventory is frozen; existing run plans do not accept new stores."
                )
        else:
            if args.execute or args.resume:
                raise AtlasError("Create and review a dry-run plan before execution/resume.")
            sources = [local_path(p) for p in args.reuse_store]
            pilot = ROOT / (
                "local/market-data/alpaca-iex/pilot/historical.sqlite3"
                if feed == "iex"
                else "local/market-data/alpaca-pilot/historical.sqlite3"
            )
            if pilot.is_file() and not sources and not args.pilot:
                sources.append(pilot)
            base = ROOT / (
                f"local/market-data/alpaca-iex/{mode}"
                if feed == "iex"
                else "local/market-data/alpaca-full"
            )
            plan = {
                "schema": 1,
                "repository": str(ROOT.resolve()),
                "manifest_id": manifest["manifest_id"],
                "store": str(base / "historical.sqlite3"),
                "state": str(base / "collection.sqlite3"),
                "cache": str(base / "pages.sqlite3"),
                "reuse": reuse_inventory(sources, manifest),
            }
            if feed == "iex":
                plan.update(schema=2, feed="iex", mode=mode)
            plan["run_id"] = digest(plan)
            verify_run_plan(plan, manifest)
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            with plan_path.open("x", encoding="utf-8") as handle:
                json.dump(plan, handle, indent=2, sort_keys=True)
        now = datetime.now(UTC)
        free = preflight(manifest, plan, now)
        with exclusive_worker(ROOT / "local/market-data/alpaca-collection.lock"):
            exclusion = load_exclusion(args.exclusion_record, manifest, plan)
            if not args.execute:
                from atlas.alpaca_iex_approval import readiness_state

                print(
                    json.dumps(
                        {
                            "status": "DRY_RUN",
                            "account_permissions": "UNVERIFIED",
                            "feed": feed,
                            "execution_scope": mode,
                            "iex_entitlement": "DOCUMENTED_FOR_BASIC; ACCOUNT_UNTESTED"
                            if feed == "iex"
                            else "NOT_APPLICABLE",
                            "network_enabled": False,
                            "approved_excluded_dates": [exclusion["date"]] if exclusion else [],
                            "coverage_complete": exclusion is None,
                            "run_id": plan["run_id"],
                            "run_plan": str(plan_path),
                            "reused_partitions": len(plan["reuse"]),
                            "reuse": plan["reuse"],
                            "expected_initial_requests": sum(
                                (not exclusion or t["date"] != exclusion["date"])
                                and any(
                                    t["date"] + "/" + s not in plan["reuse"] for s in t["symbols"]
                                )
                                for t in manifest["tasks"]
                            ),
                            "request_count_is_not_a_cap": True,
                            "expected_bars": manifest["expected_minute_bars"],
                            "bar_count_is_upper_bound": feed == "iex",
                            **(readiness_state() if feed == "iex" else {}),
                            "disk_free_bytes": free,
                            "worker_lock_available": True,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            check_permission(permission_path, manifest, plan, now)
            if Path(plan["state"]).exists() and not args.resume:
                raise AtlasError("Existing collection checkpoints require explicit --resume.")
            if args.resume and not Path(plan["state"]).exists():
                raise AtlasError("No collection checkpoints exist to resume.")
            quota = AlpacaQuota()
            pilot_id = None
            if feed == "iex" and args.pilot:
                pilot_id = "iex-pilot-" + plan["run_id"]
                with quota.connect() as db:
                    exists = db.execute(
                        "SELECT used FROM pilots WHERE id=?", (pilot_id,)
                    ).fetchone()
                if exists is None:
                    quota.start_pilot(pilot_id)
            provider = AlpacaProvider(
                symbols=tuple(manifest["symbols"]),
                quota=quota,
                cache=plan["cache"],
                allow_network=True,
                feed=feed,
                pilot=pilot_id,
            )
            preflight(manifest, plan, datetime.now(UTC))
            results = _collect_locked(
                manifest,
                plan,
                provider,
                progress=lambda event: print(json.dumps(event, sort_keys=True), flush=True),
                exclusion_record=args.exclusion_record,
            )
            if feed == "iex" and args.pilot:
                from atlas.alpaca_iex_approval import record_pilot_access

                record_pilot_access(manifest, plan, results, quota, pilot_id)
            print(
                json.dumps(
                    {
                        "status": "COMPLETED_WITH_GAPS" if exclusion else "COMPLETED",
                        "partitions": len(results),
                        "excluded_partitions": sum(r["state"] == "EXCLUDED" for r in results),
                        "coverage_complete": exclusion is None,
                    }
                )
            )
            return 0
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "status": "INTERRUPTED",
                    "error": "Checkpoints retained; resume after job lease expiry.",
                }
            ),
            file=sys.stderr,
        )
        return 130
    except (AtlasError, ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        message = (
            str(error)
            if isinstance(error, AtlasError)
            else "COLLECTION_CONFIGURATION_OR_STORAGE_FAILURE"
        )
        print(json.dumps({"status": "STOPPED", "error": message}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
