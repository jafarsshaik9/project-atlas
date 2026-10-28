"""Offline diagnostics: no environment, credentials, external providers or execution imports."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.paper_data.contracts import Delay, FeedConfig, FreshnessPolicy, MockEntitlementResponse
from atlas.paper_data.contracts import timestamp as parse_timestamp
from atlas.paper_data.gates import validate_feed, verify_entitlement
from atlas.paper_data.mock import SCENARIOS, mock_config, mock_entitlement, mock_script
from atlas.paper_data.scheduler import SessionScheduler
from atlas.paper_data.store import SchedulerStore


def _timestamp(value):
    try:
        return parse_timestamp(value)
    except (ValueError, AtlasError):
        raise argparse.ArgumentTypeError("An explicit ISO UTC datetime is required.") from None


def parser():
    root = argparse.ArgumentParser(description="Offline paper data only; no orders or network")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("state", "ownership", "freshness", "validate-feed", "entitlement", "replay"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true", help="All diagnostics are structured JSON")
        if name in ("state", "ownership", "freshness", "replay"):
            sub.add_argument("--db", type=Path, required=True)
            sub.add_argument("--stream", default="mock-paper")
        if name in ("validate-feed", "entitlement", "replay"):
            sub.add_argument("--config", type=Path, help="Public mock-only JSON; never credentials")
            sub.add_argument("--venue", choices=("XNYS", "XNAS"), default="XNAS")
            sub.add_argument(
                "--at", type=_timestamp, default=datetime(2026, 10, 28, 14, tzinfo=UTC)
            )
            sub.add_argument("--unverified", action="store_true")
            sub.add_argument("--allow-delayed", action="store_true")
        if name == "replay":
            sub.add_argument("--scenario", choices=SCENARIOS, default="ordered")
            sub.add_argument("--attempt", help="Unique worker attempt ID; default UUID")
            sub.add_argument(
                "--now", type=_timestamp, help="Explicit recovery clock, separate from script --at"
            )
    return root


def configuration(args):
    config = mock_config(
        Venue(args.venue),
        Delay.DELAYED if getattr(args, "scenario", None) == "delayed" else Delay.REPLAY,
    )
    policy = FreshnessPolicy(allow_delayed=args.allow_delayed)
    entitlement = mock_entitlement(config, args.at, verified=not args.unverified)
    if args.config is not None:
        path = args.config.resolve()
        if path.suffix.lower() != ".json" or path.stat().st_size > 100_000:
            raise AtlasError("Mock feed configuration must be a public JSON file <=100000 bytes.")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or set(data) - {"feed", "policy", "entitlement"}:
            raise AtlasError("Unknown mock feed configuration fields.")
        config = FeedConfig.model_validate(data["feed"])
        policy = FreshnessPolicy.model_validate(data.get("policy", {}))
        if args.allow_delayed:
            policy = FreshnessPolicy(**(policy.model_dump() | {"allow_delayed": True}))
        entitlement = (
            MockEntitlementResponse.model_validate(data["entitlement"])
            if "entitlement" in data
            else mock_entitlement(config, args.at, verified=not args.unverified)
        )
        if args.unverified:
            entitlement = MockEntitlementResponse(
                **(entitlement.model_dump() | {"verified": False})
            )
    return config, policy, entitlement


def replay(args, config, policy, entitlement):
    provider = mock_script(config, args.at, args.scenario)
    # Injected replay time is explicit; source timestamps are already in the script.
    current = [args.now or args.at]
    store = SchedulerStore(args.db, clock=lambda: current[0])
    calendar = HistoricalCalendar(
        config.instrument.venue, args.at.date(), args.at.date() + timedelta(days=1)
    )
    scheduler = SessionScheduler(
        store, args.stream, config, calendar, provider, entitlement, policy
    )
    before = store.snapshot(args.stream)
    if args.now is None and before["last_clock"]:
        current[0] = max(current[0], datetime.fromisoformat(before["last_clock"]))
    lease = store.claim(args.stream, args.attempt or str(uuid4()))
    if lease is None:
        raise AtlasError("Paper stream has an active worker; replay did not acquire ownership.")
    try:
        cursor = before["cursor"]
        next_scan_at = before["next_scan_at"]
        while cursor < len(provider.events):
            if next_scan_at:
                target = max(provider.events[cursor].at, datetime.fromisoformat(next_scan_at))
            else:
                target = provider.events[cursor].at
            # This finite harness renews before advancing time. It never revives an expired token.
            if target > current[0] + timedelta(seconds=29):
                raise AtlasError("Mock replay gap exceeds the harness lease; use bounded scripts.")
            current[0] = max(current[0], target)
            store.heartbeat(lease)
            result = scheduler.scan(lease)
            if result["outcome"] in ("WAITING_SESSION", "NO_DATA", "NOT_DUE"):
                break
            cursor += 1
            next_scan_at = (
                current[0] + timedelta(seconds=config.scan_interval_seconds)
            ).isoformat()
    finally:
        store.shutdown(lease)
    result = store.snapshot(args.stream)
    result["scope"] = "MOCK_ONLY"
    result["execution_enabled"] = False
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ("state", "ownership", "freshness"):
            snapshot = SchedulerStore(args.db, read_only=True).snapshot(args.stream)
            if args.command == "ownership":
                result = {
                    key: snapshot[key]
                    for key in (
                        "stream_id",
                        "owner",
                        "fence",
                        "lease_until",
                        "owner_active",
                        "status",
                    )
                }
            elif args.command == "freshness":
                result = {
                    "stream_id": args.stream,
                    "counts": snapshot["counts"],
                    "status": snapshot["status"],
                    "last_scan_at": snapshot["last_scan_at"],
                    "last_scan_outcome": snapshot["last_scan_outcome"],
                    "recent_failures": [r for r in snapshot["results"] if r["reasons"]],
                    "limit": 100,
                    "policy": snapshot["binding_json"]["policy"],
                    "note": "Persisted scan-time assessments; no live-feed guarantee.",
                }
            else:
                result = snapshot
        else:
            config, policy, entitlement = configuration(args)
            if args.command == "replay":
                result = replay(args, config, policy, entitlement)
            else:
                status = verify_entitlement(config, entitlement, args.at)
                failures = validate_feed(config, policy, args.at)
                valid = status.verified and (not failures or args.command == "entitlement")
                result = {
                    "scope": "MOCK_ONLY",
                    "valid": valid,
                    "feed_failures": failures,
                    "entitlement": status.model_dump(mode="json"),
                    "feed": config.model_dump(mode="json"),
                    "policy": policy.model_dump(mode="json"),
                }
                print(json.dumps(result, sort_keys=True))
                return 0 if valid else 1
        print(json.dumps(result, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"status": "INTERRUPTED", "scope": "MOCK_ONLY"}), file=sys.stderr)
        return 130
    except (AtlasError, ValidationError, ValueError, KeyError, OSError):
        # Input and upstream validation errors may contain private input: never echo payloads.
        print(
            json.dumps({"error": "PAPER_DIAGNOSTIC_FAILED", "scope": "MOCK_ONLY"}), file=sys.stderr
        )
        return 1
