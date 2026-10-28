"""Historical research and explicitly gated Alpaca collection; dry-run by default."""

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from atlas.config import AtlasError
from atlas.historical_backtest import AvailabilityMode
from atlas.historical_backtest_cli import load_config
from atlas.multi_session_research import (
    SessionRef,
    WalkForward,
    header,
    run_multi_session,
    walk_forward,
)
from atlas.research_collection import plan_collection, write_manifest
from atlas.strategy import ORBConfig


def public_json(path, maximum=20_000_000):
    path = Path(path)
    if (
        path.suffix.lower() != ".json"
        or path.name.startswith(".env")
        or path.stat().st_size > maximum
    ):
        raise AtlasError("An explicitly named public JSON file is required.")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_bundle(path):
    bundle = public_json(path)
    if (
        set(bundle)
        - {"schema_version", "datasets", "configuration", "fx_policy", "walk_forward", "strategies"}
        or bundle.get("schema_version") != 1
        or not bundle.get("datasets")
    ):
        raise AtlasError("Unsupported research bundle.")
    folder = Path(path).resolve().parent
    config_path = folder / bundle["configuration"]
    public_json(config_path, 100_000)
    _, base = load_config(config_path)
    refs = []
    for item in bundle["datasets"]:
        if set(item) != {"database", "dataset_id"}:
            raise AtlasError("Research dataset references require database and dataset_id.")
        ref = SessionRef(str((folder / item["database"]).resolve()), item["dataset_id"], base)
        if bundle.get("fx_policy", "AS_CONFIGURED") == "FIXED_ASSUMED_PER_SESSION":
            if base.availability_mode != AvailabilityMode.ASSUMED or base.fx.point_in_time_verified:
                raise AtlasError("Repeated fixed FX is exploratory ASSUMED research only.")
            session = header(ref)[2]
            ref = replace(
                ref,
                config=replace(
                    base,
                    fx=replace(
                        base.fx,
                        event_time=session.open,
                        available_time=session.open,
                        source="FIXED_RESEARCH_ASSUMPTION_NOT_HISTORICAL_FX",
                    ),
                ),
            )
        elif bundle.get("fx_policy", "AS_CONFIGURED") != "AS_CONFIGURED":
            raise AtlasError("Unknown research FX policy.")
        refs.append(ref)
    candidates = []
    for values in bundle.get("strategies", []):
        values = dict(values)
        for key in ("target_r", "minimum_bar_volume"):
            if key in values:
                if not isinstance(values[key], str):
                    raise AtlasError("Strategy monetary parameters require decimal strings.")
                values[key] = Decimal(values[key])
        candidates.append(ORBConfig(**values))
    return refs, candidates, bundle.get("walk_forward")


def main(argv=None):
    actual = sys.argv[1:] if argv is None else argv
    if actual and actual[0] == "diagnose":
        from atlas.research_diagnostics import main as diagnostics_main

        return diagnostics_main(actual[1:])
    if actual and actual[0] == "collect":
        from atlas.alpaca_collection_cli import main as collection_main

        return collection_main(actual[1:])
    parser = argparse.ArgumentParser(
        description="Historical research and explicitly gated collection; dry-run by default"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect", help="Alpaca collection; dry-run unless explicitly approved")
    commands.add_parser("diagnose", help="Read-only historical ORB diagnostics and reporting")
    plan = commands.add_parser("plan")
    plan.add_argument("--provider", choices=("alpaca", "massive"), default="alpaca")
    plan.add_argument(
        "--as-of", required=True, help="Explicit timezone-aware UTC ISO planning instant"
    )
    plan.add_argument("--first", type=date.fromisoformat)
    plan.add_argument("--last", type=date.fromisoformat)
    plan.add_argument("--store", action="append", default=[], type=Path)
    plan.add_argument("--actions", type=Path, help="Reviewed public corporate-action JSON array")
    plan.add_argument("--output", type=Path, required=True)
    backtest = commands.add_parser("backtest")
    backtest.add_argument("--bundle", type=Path, required=True)
    backtest.add_argument("--allow-artificial", action="store_true")
    backtest.add_argument("--output", type=Path)
    for sub in (plan, backtest):
        sub.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            from atlas.alpaca_collection import plan_alpaca, write_alpaca_manifest

            planner = plan_alpaca if args.provider == "alpaca" else plan_collection
            manifest = planner(
                datetime.fromisoformat(args.as_of),
                stores=args.store,
                first=args.first,
                last=args.last,
                actions=public_json(args.actions) if args.actions else (),
            )
            writer = write_alpaca_manifest if args.provider == "alpaca" else write_manifest
            writer(args.output, manifest)
            result = {
                key: value
                for key, value in manifest.items()
                if key not in {"tasks", "actions", "existing_jobs"}
            }
            result.update(
                manifest_path=str(args.output),
                task_count=len(manifest["tasks"]),
                reused_partitions=sum(bool(t["reusable"]) for t in manifest["tasks"]),
            )
        else:
            refs, candidates, windows = load_bundle(args.bundle)
            if windows:
                result = walk_forward(
                    refs, candidates, WalkForward(**windows), allow_artificial=args.allow_artificial
                )
            elif candidates:
                result = {
                    "comparisons": [
                        run_multi_session(refs, orb=c, allow_artificial=args.allow_artificial)
                        for c in candidates
                    ],
                    "selection": "NONE; no out-of-sample claim",
                }
            else:
                result = run_multi_session(refs, allow_artificial=args.allow_artificial)
            if args.output:
                if args.output.suffix != ".json":
                    raise AtlasError("Research output requires a new JSON path.")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("x", encoding="utf-8") as handle:
                    json.dump(result, handle, indent=2, sort_keys=True)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AtlasError, ValueError, KeyError, TypeError, OSError):
        print(
            json.dumps({"error": "OFFLINE_RESEARCH_FAILED", "network_enabled": False}),
            file=sys.stderr,
        )
        return 1
