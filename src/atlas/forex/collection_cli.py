"""Historical collection is offline by default; Stage A and B require distinct approvals."""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.access import Quota
from atlas.forex.cli import write_new
from atlas.forex.collection import (
    approval_phrase,
    approve,
    collection_permit,
    make_plan,
    metadata,
    report,
    run_collection,
    validate_plan,
)
from atlas.forex.config import ROOT, load_config
from atlas.forex.provider import OandaReader, ReadOnlyTransport


def private_path(value, *, suffix=None):
    path = Path(value).resolve()
    if not path.is_relative_to((ROOT / "local").resolve()) or (suffix and path.suffix != suffix):
        raise AtlasError("Collection artifacts must remain inside repository local/.")
    return path


def read_json(path):
    path = private_path(path, suffix=".json")
    if path.stat().st_size > 20_000_000:
        raise AtlasError("Collection JSON exceeds size bound.")
    return json.loads(path.read_text(encoding="utf-8"))


def pilot_evidence(plan, path):
    if plan["stage"] != "B":
        return None
    if not path:
        raise AtlasError("Stage B requires --pilot-plan and successful Stage A data.")
    pilot = validate_plan(read_json(path))
    if pilot["stage"] != "A" or pilot["plan_hash"] != plan["pilot_hash"]:
        raise AtlasError("Stage A plan linkage mismatch.")
    for key in (
        "environment",
        "origin",
        "output",
        "instruments",
        "effective_end",
        "target_start",
        "page_minutes",
    ):
        if pilot[key] != plan[key]:
            raise AtlasError("Stage B changed the Stage A collection contract.")
    try:
        result = report(pilot)
    except AtlasError as exc:
        if str(exc) not in ("FOREX_STORE_NOT_FOUND", "FOREX_COLLECTION_LEDGER_NOT_FOUND"):
            raise
        raise AtlasError("Stage A is incomplete; Stage B blocked.") from None
    if result["status"] != "COMPLETE_REVIEW_REQUIRED":
        raise AtlasError("Stage A is incomplete or quarantined; Stage B blocked.")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="OANDA historical collection; offline until separately approved"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("plan")
    planning.add_argument("--stage", choices=("A", "B"), required=True)
    planning.add_argument(
        "--discovery", default=str(ROOT / "local/oanda/phase6a-discovery-evidence.json")
    )
    planning.add_argument("--pilot-plan")
    planning.add_argument("--destination", default=str(ROOT / "local/oanda/two-year"))
    planning.add_argument("--output", required=True)
    for name in ("approve", "collect", "report", "export"):
        sub = commands.add_parser(name)
        sub.add_argument("--plan", required=True)
        if name in ("approve", "collect"):
            sub.add_argument("--pilot-plan")
        if name in ("approve", "report", "export"):
            sub.add_argument("--output", required=True)
        if name == "collect":
            sub.add_argument("--execute", action="store_true")
            sub.add_argument("--approval")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            evidence = read_json(args.discovery)
            pilot = read_json(args.pilot_plan) if args.pilot_plan else None
            value = make_plan(
                metadata(evidence),
                evidence["environment"],
                private_path(args.destination),
                now=datetime.now(UTC),
                stage=args.stage,
                pilot=pilot,
            )
            write_new(private_path(args.output, suffix=".json"), value)
            print(
                json.dumps(
                    {k: v for k, v in value.items() if k not in ("partitions", "instruments")}
                )
            )
            return 0
        plan = validate_plan(read_json(args.plan))
        private_path(plan["output"])
        if args.command == "collect" and not args.execute:
            print(
                json.dumps(
                    {
                        "status": "DRY_RUN",
                        "stage": plan["stage"],
                        "plan_hash": plan["plan_hash"],
                        "initial_requests": plan["initial_requests"],
                        "request_cap": plan["request_cap"],
                        "effective_end": plan["effective_end"],
                        "authenticated_attempts": 0,
                    }
                )
            )
            return 0
        if args.command == "report":
            result = report(plan)
            write_new(private_path(args.output, suffix=".json"), result)
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k not in ("dataset_proofs", "spread_by_instrument_utc_hour")
                    }
                )
            )
            return 0
        if args.command == "export":
            from atlas.forex.dataset import export_dataset

            export_dataset(plan, private_path(args.output, suffix=".jsonl"))
            print(json.dumps({"status": "EXPORTED", "plan_hash": plan["plan_hash"]}))
            return 0
        if args.command == "collect" and not args.approval:
            raise AtlasError("SEPARATE_COLLECTION_APPROVAL_REQUIRED")
        pilot = pilot_evidence(plan, args.pilot_plan)
        config = load_config()
        if plan["origin"] != "EXTERNAL_HISTORICAL_MARKET_DATA":
            raise AtlasError("Fixture plans cannot authorize real HTTP requests.")
        if datetime.fromisoformat(plan["effective_end"]) > datetime.now(UTC).replace(
            second=0, microsecond=0
        ):
            raise AtlasError("Frozen plan end is in the future; regenerate and reapprove.")
        if args.command == "approve":
            path = private_path(args.output, suffix=".json")
            if path.exists():
                raise AtlasError("Approval output exists; overwrite refused.")
            print(
                json.dumps(
                    {
                        "stage": plan["stage"],
                        "request_cap": plan["request_cap"],
                        "pilot_quality": pilot["checksum"] if pilot else None,
                        "notice": (
                            "Confirm applicable historical research/storage permission "
                            "before approval. No request made now."
                        ),
                    }
                )
            )
            answer = input("Type exactly: " + approval_phrase(plan) + "\n")
            write_new(
                path,
                approve(
                    config, plan, answer, now=datetime.now(UTC).timestamp(), pilot_report=pilot
                ),
            )
            print(
                json.dumps({"status": "COLLECTION_APPROVAL_RECORDED", "authenticated_attempts": 0})
            )
            return 0
        permit = collection_permit(
            config,
            plan,
            read_json(args.approval),
            now=datetime.now(UTC).timestamp(),
            pilot_report=pilot,
        )
        quota = Quota(ROOT / "local/oanda/quota.sqlite3")
        transport = ReadOnlyTransport(config, permit, quota)
        try:
            result = run_collection(
                plan,
                OandaReader(transport),
                lock_path=ROOT / "local/oanda/collection-worker.sqlite3",
                progress=lambda e: print(
                    json.dumps(e | {"api_attempts_used": quota.count(permit)}), flush=True
                ),
            )
        finally:
            transport.close()
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "stage": plan["stage"],
                    "bars": result["bars"],
                    "api_attempts_used": quota.count(permit),
                    "next_stage_authorized": False,
                }
            )
        )
        return 0
    except AtlasError as exc:
        print(json.dumps({"status": "STOPPED", "error": str(exc)}))
        return 1
    except (OSError, ValueError, TypeError, KeyError, EOFError, sqlite3.Error):
        print(json.dumps({"status": "STOPPED", "error": "OANDA_COLLECTION_INPUT_OR_STORAGE_ERROR"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
