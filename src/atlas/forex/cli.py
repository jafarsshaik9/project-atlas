"""Explicit offline validation / separately approved GET-only discovery pilot."""

import argparse
import json
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.access import Quota, approval_record, phrase, utc_now, validate_approval
from atlas.forex.config import ROOT, SYMBOLS, load_config
from atlas.forex.provider import OandaReader, ReadOnlyTransport


def local_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to((ROOT / "local").resolve()) or path.suffix != ".json":
        raise AtlasError("OANDA evidence requires a JSON path inside repository local/.")
    return path


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)


def main(argv=None):
    import sys

    actual = sys.argv[1:] if argv is None else argv
    if actual and actual[0] == "history":
        from atlas.forex.collection_cli import main as history_main

        return history_main(actual[1:])
    parser = argparse.ArgumentParser(description="OANDA read-only research; offline by default")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Offline scoped configuration validation; no network")
    approve = sub.add_parser("approve", help="Record explicit interactive discovery approval")
    approve.add_argument("--scope", choices=("accounts", "accounts-instruments"), required=True)
    approve.add_argument("--output", required=True)
    discover = sub.add_parser("discover", help="Offline unless --execute and valid approval")
    discover.add_argument("--execute", action="store_true")
    discover.add_argument("--approval")
    discover.add_argument("--output", default=str(ROOT / "local/oanda/discovery.json"))
    args = parser.parse_args(argv)
    try:
        config = load_config()  # Selective local values, never os.environ assignments.
        if args.command == "validate" or (args.command == "discover" and not args.execute):
            print(
                json.dumps(
                    {
                        "status": "OFFLINE_VALIDATED",
                        "environment": config.environment,
                        "credential_presence": True,
                        "authenticated_attempts": 0,
                        "symbols": SYMBOLS,
                        "account_access": "UNVERIFIED",
                        "account_currency": "UNKNOWN",
                        "broker_execution": "UNSUPPORTED",
                    }
                )
            )
            return 0
        if args.command == "approve":
            path = local_path(args.output)
            if path.exists():
                raise AtlasError("Approval output exists; overwrite refused.")
            expected = phrase(config.environment, args.scope)
            print(
                f"This records approval for {config.environment} GET discovery only. "
                "No request is made now."
            )
            confirmation = input(f"Type exactly: {expected}\n")
            write_new(path, approval_record(config, args.scope, confirmation))
            print(json.dumps({"status": "APPROVAL_RECORDED", "authenticated_attempts": 0}))
            return 0
        if not args.approval:
            raise AtlasError("OANDA_SEPARATE_APPROVAL_REQUIRED")
        output = local_path(args.output)
        if output.exists():
            raise AtlasError("Discovery evidence exists; overwrite refused before any request.")
        approval = local_path(args.approval)
        if approval.stat().st_size > 8192:
            raise AtlasError("Invalid approval file size.")
        permit = validate_approval(config, json.loads(approval.read_text(encoding="utf-8")))
        quota = Quota(ROOT / "local/oanda/quota.sqlite3")
        transport = ReadOnlyTransport(config, permit, quota)
        evidence = {
            "schema": "oanda-discovery-v1",
            "environment": config.environment,
            "scope": permit.scope,
        }
        try:
            reader = OandaReader(transport)
            evidence["discovery"] = reader.accounts()
            if permit.scope == "accounts-instruments":
                if not evidence["discovery"]["configured_account_accessible"]:
                    raise AtlasError("CONFIGURED_V20_ACCOUNT_NOT_ACCESSIBLE")
                instruments = reader.instruments()
                evidence["instruments"] = [i.model_dump(mode="json") for i in instruments]
                evidence["unavailable_requested_instruments"] = sorted(
                    set(SYMBOLS) - {i.name for i in instruments}
                )
            evidence["status"] = "READ_ONLY_DISCOVERY_COMPLETE"
        except AtlasError as exc:
            evidence["status"] = "STOPPED"
            evidence["error"] = str(exc)
        finally:
            transport.close()
        evidence["authenticated_attempts"] = quota.count(permit)
        evidence["recorded_at"] = utc_now().isoformat()
        write_new(output, evidence)
        print(json.dumps(evidence))
        return 0 if evidence["status"] == "READ_ONLY_DISCOVERY_COMPLETE" else 1
    except AtlasError as exc:
        print(json.dumps({"status": "STOPPED", "error": str(exc)}))
        return 1
    except (OSError, ValueError, TypeError, EOFError):
        # Never include upstream exception arguments: paths/payloads may be sensitive.
        print(json.dumps({"status": "STOPPED", "error": "OANDA_OFFLINE_CONFIG_OR_APPROVAL_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
