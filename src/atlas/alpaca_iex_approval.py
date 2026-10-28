"""Explicit interactive IEX approval. Documentation, observed access and execution stay separate."""

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from atlas import alpaca_collection_cli as cli
from atlas.config import AtlasError
from atlas.market_data.contracts import digest

DOC_URL = "https://docs.alpaca.markets/us/docs/about-market-data-api"
DOC_REVIEW_DATE = date(2026, 10, 28)


def readiness_state():
    try:
        personal = personal_evidence()["status"]
    except AtlasError:
        personal = "UNCONFIRMED"
    try:
        access = observed_pilot()["status"]
    except (AtlasError, OSError):
        access = "NOT_YET_TESTED"
    return {
        "personal_use_permission": personal,
        "actual_iex_access": access,
        "execution_approval": "NOT_APPLIED_IN_DRY_RUN",
    }


def personal_evidence():
    path = cli.ROOT / "local/research/two-year/alpaca-personal-use-evidence.json"
    if not path.is_file():
        raise AtlasError(
            "Personal non-commercial Support evidence is missing; approval not recorded."
        )
    record = cli.public_json(path)
    if (
        record.get("provider") != "alpaca"
        or record.get("personal_use_permission", {}).get("status")
        != "SUPPORT_CONFIRMED_PERSONAL_NONCOMMERCIAL_ONLY"
    ):
        raise AtlasError("Personal-use permission has not been confirmed.")
    # Deliberately do not inherit the different SIP entitlement field.
    return {
        "status": "SUPPORT_CONFIRMED_PERSONAL_NONCOMMERCIAL_ONLY",
        "evidence_digest": digest(record),
    }


def observed_pilot():
    path = cli.ROOT / "local/market-data/alpaca-iex/pilot/access.json"
    if not path.is_file():
        raise AtlasError(
            "IEX_ACCOUNT_ACCESS_UNVERIFIED: complete the separately approved IEX pilot first."
        )
    record = cli.public_json(path)
    if record.get("feed") != "iex" or record.get("scope") != "OBSERVED_SINGLE_SESSION_ONLY":
        raise AtlasError("Invalid IEX pilot access evidence.")
    if record["quota_pilot_id"] != "iex-pilot-" + record["run_id"]:
        raise AtlasError("SIP quota history cannot establish IEX pilot access.")
    proof, _, bars = cli.inspect_partition(
        cli.local_path(record["store"]), record["dataset_id"], expected_feed="iex"
    )
    if not bars or digest(proof) != record["manifest_checksum"]:
        raise AtlasError("IEX pilot dataset evidence changed.")
    import sqlite3

    with sqlite3.connect(cli.DEFAULT_QUOTA.resolve().as_uri() + "?mode=ro", uri=True) as db:
        cli.AlpacaQuota.validate(db)
        row = db.execute(
            "SELECT used FROM pilots WHERE id=?", (record["quota_pilot_id"],)
        ).fetchone()
        success = db.execute(
            "SELECT count(*) FROM attempts WHERE pilot=? AND status=200 AND finished IS NOT NULL",
            (record["quota_pilot_id"],),
        ).fetchone()[0]
    if row is None or not 1 <= row[0] <= 2 or not success:
        raise AtlasError("IEX pilot has no completed successful attempt in the canonical quota.")
    return {
        "status": "OBSERVED_SINGLE_SESSION_ONLY",
        "evidence_digest": digest(record),
        "dataset_id": record["dataset_id"],
    }


def make_record(manifest, plan, *, account_plan, now, confirmation):
    if manifest["provider"] != "alpaca-iex" or plan.get("feed") != "iex" or account_plan != "basic":
        raise AtlasError(
            "IEX approval requires an explicit Basic account declaration and IEX plan."
        )
    cli.validate_manifest(manifest, pilot=plan["mode"] == "pilot")
    cli.verify_run_plan(plan, manifest)
    if not timedelta(0) <= now.date() - DOC_REVIEW_DATE <= timedelta(days=30):
        raise AtlasError("Basic/IEX documentation review is stale; refresh it before approval.")
    expected = f"APPROVE IEX {plan['mode'].upper()} {plan['run_id']}"
    if confirmation != expected:
        raise AtlasError("Explicit IEX execution confirmation did not match; no record written.")
    personal = personal_evidence()
    access = {"status": "NOT_YET_TESTED"} if plan["mode"] == "pilot" else observed_pilot()
    return {
        "schema": 2,
        "kind": "IEX_SCOPED_EXECUTION_APPROVAL",
        "repository": str(cli.ROOT.resolve()),
        "manifest_id": manifest["manifest_id"],
        "run_id": plan["run_id"],
        "feed": "iex",
        "adjustment": "raw",
        "scope": plan["mode"],
        "personal_use_permission": personal,
        "entitlement": {
            "account_plan": "BASIC_USER_DECLARED",
            "plan_eligibility": "IEX_DOCUMENTED_FOR_BASIC",
            "documentation": DOC_URL,
            "documentation_reviewed": str(DOC_REVIEW_DATE),
            "observed_access": access,
        },
        "execution_approval": {
            "status": "USER_EXPLICIT_APPROVAL",
            "confirmation": expected,
            "approved_at": now.isoformat(),
        },
        "maximum_http_attempts": 2 if plan["mode"] == "pilot" else None,
    }


def check_iex_permission(path, manifest, plan, now):
    if not Path(path).is_file():
        raise AtlasError(
            "IEX_EXECUTION_NOT_APPROVED: run the separate interactive approve-iex command."
        )
    record = cli.public_json(path)
    stamp = datetime.fromisoformat(record["execution_approval"]["approved_at"])
    if stamp.tzinfo is None or not timedelta(0) <= now - stamp <= timedelta(days=30):
        raise AtlasError("IEX execution approval is stale or invalid.")
    expected = make_record(
        manifest,
        plan,
        account_plan="basic",
        now=stamp,
        confirmation=f"APPROVE IEX {plan['mode'].upper()} {plan['run_id']}",
    )
    if record != expected:
        raise AtlasError(
            "IEX permission, entitlement or execution scope differs from the approved plan."
        )


def record_pilot_access(manifest, plan, results, quota, pilot_id):
    if (
        plan.get("mode") != "pilot"
        or len(results) != 1
        or quota.path != cli.DEFAULT_QUOTA.resolve()
    ):
        raise AtlasError("Invalid native IEX pilot evidence source.")
    with quota.connect() as db:
        if not db.execute(
            "SELECT count(*) FROM attempts WHERE pilot=? AND status=200 AND finished IS NOT NULL",
            (pilot_id,),
        ).fetchone()[0]:
            raise AtlasError("No successful IEX pilot HTTP attempt to record.")
    result = results[0]
    proof, _, bars = cli.inspect_partition(
        result["store"], result["dataset_id"], expected_feed="iex"
    )
    if not bars:
        raise AtlasError("An empty IEX session cannot verify observed access.")
    record = {
        "schema": 1,
        "feed": "iex",
        "scope": "OBSERVED_SINGLE_SESSION_ONLY",
        "run_id": plan["run_id"],
        "manifest_id": manifest["manifest_id"],
        "store": result["store"],
        "dataset_id": result["dataset_id"],
        "manifest_checksum": digest(proof),
        "quota_pilot_id": pilot_id,
    }
    path = cli.ROOT / "local/market-data/alpaca-iex/pilot/access.json"
    if path.exists():
        if cli.public_json(path) != record:
            raise AtlasError("Existing IEX pilot access evidence differs; overwrite refused.")
    else:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record user approval for IEX; never contacts any API"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-plan", required=True)
    parser.add_argument("--record", required=True)
    parser.add_argument(
        "--account-plan",
        choices=("basic",),
        required=True,
        help="Your explicit declaration of this account's plan; no account API query",
    )
    args = parser.parse_args(argv)
    try:
        plan = cli.public_json(cli.local_path(args.run_plan))
        manifest = cli.validate_manifest(
            cli.public_json(cli.local_path(args.manifest)), pilot=plan.get("mode") == "pilot"
        )
        cli.preflight(manifest, plan, datetime.now(UTC))
        if manifest["provider"] != "alpaca-iex":
            raise AtlasError("approve-iex cannot authorize SIP.")
        personal_evidence()
        if plan["mode"] == "full":
            observed_pilot()
        if not sys.stdin.isatty():
            raise AtlasError(
                "IEX approval requires your interactive terminal; no noninteractive flag exists."
            )
        expected = f"APPROVE IEX {plan['mode'].upper()} {plan['run_id']}"
        print("Confirm this is your Basic account and personal, non-commercial use only.")
        print("IEX-only feed, not SIP. This records execution approval but sends no request.")
        print(
            "Pilot is capped at two HTTP attempts including retries/pages."
            if plan["mode"] == "pilot"
            else "Full collection may use additional continuation/retry requests."
        )
        entered = input("Type exactly: " + expected + "\n> ").strip()
        record = make_record(
            manifest,
            plan,
            account_plan=args.account_plan,
            now=datetime.now(UTC),
            confirmation=entered,
        )
        path = cli.local_path(args.record)
        if path.suffix != ".json":
            raise AtlasError("Approval requires a new local JSON record.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
        print(json.dumps({"status": "RECORDED", "scope": plan["mode"], "network_enabled": False}))
        return 0
    except (AtlasError, ValueError, TypeError, KeyError, OSError, EOFError) as error:
        print(
            json.dumps(
                {
                    "status": "STOPPED",
                    "error": str(error)
                    if isinstance(error, AtlasError)
                    else "IEX_APPROVAL_NOT_RECORDED",
                }
            )
        )
        return 1
