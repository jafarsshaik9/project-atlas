"""Explicit historical-data commands. Defaults are artificial and offline."""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from functools import partial
from pathlib import Path

from pydantic import ValidationError

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import CorporateAction, DownloadRequest, canonical
from atlas.market_data.providers import (
    FixtureProvider,
    ProviderError,
    TwelveDataProvider,
    fixture_instruments,
)
from atlas.market_data.quality import human_report
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore


class _JSONSyntaxError(ValueError):
    """Control flow only: never retain an argparse message containing raw inputs."""


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, json_state=None, json_supported=False, **kwargs):
        self.json_root = json_state is None
        self.json_state = {"requested": False} if json_state is None else json_state
        self.json_supported = json_supported
        super().__init__(*args, **kwargs)

    def parse_known_args(self, args=None, namespace=None):
        actual = list(sys.argv[1:] if args is None else args)
        if self.json_root:
            self.json_state["requested"] = False
        if self.json_supported:
            for token in actual:
                if token == "--":
                    break
                # These are option tokens to argparse, not attached option values.
                # --db=--json and a positional --json after -- do not select this mode.
                if token == "--json" or token.startswith("--json="):
                    self.json_state["requested"] = True
        return super().parse_known_args(actual, namespace)

    def error(self, message):
        if self.json_state["requested"]:
            raise _JSONSyntaxError() from None
        super().error(message)


def parser():
    root = _ArgumentParser(description="Historical data only; no trades or notifications")
    commands = root.add_subparsers(
        dest="command",
        required=True,
        parser_class=partial(_ArgumentParser, json_state=root.json_state),
    )
    commands.add_parser("providers", help="List capabilities; no network")
    commands.add_parser("instruments", help="List artificial instruments; no network")
    fetch = commands.add_parser("download", help="Default: original artificial offline fixture")
    fetch.add_argument("--provider", choices=("fixture-v1", "twelve-data"), default="fixture-v1")
    fetch.add_argument(
        "--config", type=Path, help="Explicit instrument/request JSON; never credentials"
    )
    fetch.add_argument("--allow-network", action="store_true")
    fetch.add_argument("--refresh", action="store_true")
    fetch.add_argument("--resume-job", help="Resume this existing compatible interrupted job ID")
    fetch.add_argument("--venue", choices=tuple(v.value for v in Venue), default="XLON")
    fetch.add_argument("--unit", choices=("GBP", "GBX", "USD"))
    fetch.add_argument("--date", type=date.fromisoformat, default=date(2024, 10, 28))
    fetch.add_argument("--minutes", type=int, default=10)
    fetch.add_argument("--scenario", default="normal")
    fetch.add_argument("--assume-availability-delay", type=int)
    for command in ("validate", "inspect"):
        sub = commands.add_parser(command, json_supported=True)
        sub.add_argument("dataset_id")
        sub.add_argument("--json", action="store_true")
    coverage = commands.add_parser("coverage")
    coverage.add_argument("--limit", type=int, default=20)
    coverage.add_argument("--offset", type=int, default=0)
    backup = commands.add_parser("backup", help="SQLite online backup to a new private file")
    backup.add_argument("destination", type=Path)
    for sub in (
        fetch,
        *[commands.choices[c] for c in ("validate", "inspect", "coverage", "backup")],
    ):
        sub.add_argument(
            "--db", type=Path, help="Separate historical database (default local/market-data)"
        )
    return root


def load_configuration(path):
    if path.stat().st_size > 100_000:
        raise AtlasError("Historical request configuration is too large.")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or set(config) - {
        "request",
        "external_access",
        "actions",
        "action_coverage",
    }:
        raise AtlasError(
            "Unsupported historical configuration fields; credentials belong in the environment."
        )
    return config


def perform_download(args):
    config = load_configuration(args.config) if args.config else {}
    if config:
        request = DownloadRequest.model_validate(config["request"])
        if request.provider != args.provider:
            raise AtlasError("Configured provider differs from the explicit command selection.")
        calendar = HistoricalCalendar(
            request.instrument.venue, request.start.date() - timedelta(days=1), request.end.date()
        )
    else:
        if args.provider != "fixture-v1" or args.allow_network:
            raise AtlasError(
                "External downloads require an explicit approved request configuration."
            )
        calendar = HistoricalCalendar(Venue(args.venue), args.date, args.date)
        session = calendar.session(Venue(args.venue), args.date)
        if session is None or not 1 <= args.minutes <= 1440:
            raise AtlasError("Choose a trading date and a positive bounded fixture interval.")
        instruments = [
            i
            for i in fixture_instruments()
            if i.venue.value == args.venue
            and (args.unit is None or i.quote_unit.value == args.unit)
        ]
        if not instruments:
            raise AtlasError(
                "No artificial instrument has the requested explicit currency metadata."
            )
        request = DownloadRequest(
            provider="fixture-v1",
            instrument=instruments[0],
            start=session.open,
            end=min(session.close, session.open + timedelta(minutes=args.minutes)),
            fixture_scenario=args.scenario,
            assumed_delay_seconds=args.assume_availability_delay,
        )
    # Gate external access before creating any database/job or reading the local key.
    access = config.get("external_access", {})
    if args.provider == "twelve-data" and (
        not args.allow_network
        or not isinstance(access, dict)
        or access.get("access_confirmed") is not True
        or not access.get("license_reference", "").strip()
        or access.get("license_reference", "").startswith("replace_")
    ):
        raise AtlasError("External access/licensing is not approved; no request made.")
    store = HistoricalStore(args.db) if args.db else HistoricalStore()
    if args.provider == "fixture-v1":
        provider = FixtureProvider(calendar, page_size=5000)
    else:

        def before_request():
            delay = store.reserve_request("twelve-data")
            if delay > 30:
                raise ProviderError("RATE_LIMIT_DEFERRED", retry_after=delay)
            if delay:
                time.sleep(delay)
                if store.reserve_request("twelve-data"):
                    raise ProviderError("CONCURRENT_RATE_BUDGET_BUSY")

        provider = TwelveDataProvider(
            os.environ.get("ATLAS_TWELVEDATA_API_KEY", ""),
            allow_network=True,
            access_confirmed=True,
            license_reference=access["license_reference"],
            before_request=before_request,
            on_backoff=lambda seconds: store.defer_requests("twelve-data", seconds),
        )
    actions = tuple(CorporateAction.model_validate(a) for a in config.get("actions", []))
    dataset_id, job_id, cached = download(
        provider,
        request,
        calendar,
        store,
        refresh=args.refresh,
        resume_job_id=args.resume_job,
        actions=actions,
        action_coverage=config.get("action_coverage", "UNKNOWN"),
    )
    manifest, report, _ = store.inspect(dataset_id)
    print(f"Origin: {manifest['origin']} | {manifest['state']} | cached={cached}")
    print(f"Dataset: {dataset_id}\nJob: {job_id}")
    print(human_report(report))
    return int(report.status == "FAIL")


def main(argv=None):
    try:
        args = parser().parse_args(argv)
    except _JSONSyntaxError:
        print(canonical({"status": "ERROR", "error": "INVALID_ARGUMENTS"}))
        print("Atlas: Invalid historical command arguments; use --help.", file=sys.stderr)
        return 2
    try:
        json_mode = getattr(args, "json", False)
        if not json_mode:
            print("HISTORICAL MARKET DATA | EXECUTION: NONE | NOTIFICATIONS: NONE")
        if args.command == "providers":
            for descriptor in (FixtureProvider.descriptor, TwelveDataProvider.descriptor):
                print(canonical(descriptor.model_dump(mode="json")))
            return 0
        if args.command == "instruments":
            print(canonical([i.model_dump(mode="json") for i in fixture_instruments()]))
            print(
                "External symbol catalogs are not downloaded or inferred; supply verified metadata."
            )
            return 0
        if args.command == "download":
            return perform_download(args)
        store = HistoricalStore(args.db, read_only=True)
        if args.command == "coverage":
            manifests, total = store.coverage(limit=args.limit, offset=args.offset)
            print(
                canonical(
                    {
                        "total": total,
                        "datasets": [
                            {
                                k: m[k]
                                for k in (
                                    "dataset_id",
                                    "origin",
                                    "state",
                                    "row_count",
                                    "observed_start",
                                    "observed_end",
                                    "integrity",
                                    "integrity_note",
                                )
                            }
                            for m in manifests
                        ],
                    }
                )
            )
        elif args.command == "backup":
            store.backup(args.destination)
            print("Historical online backup completed.")
        elif args.command == "inspect":
            manifest, _, _ = store.inspect(args.dataset_id)
            print(canonical(manifest))
        elif args.command == "validate":
            report = store.validate(args.dataset_id)
            print(report.model_dump_json() if args.json else human_report(report))
            return int(report.status == "FAIL")
        return 0
    except (ValidationError, ValueError, KeyError, TypeError):
        if getattr(args, "json", False):
            print(canonical({"status": "ERROR", "error": "INVALID_HISTORICAL_CONTRACT"}))
        print(
            "Atlas: Invalid historical configuration or source contract; values withheld.",
            file=sys.stderr,
        )
        return 1
    except (AtlasError, OSError) as error:
        message = (
            str(error)
            if isinstance(error, AtlasError)
            else "Historical file operation failed; check private paths."
        )
        if getattr(args, "json", False):
            print(canonical({"status": "ERROR", "error": message}))
        print("Atlas: " + message, file=sys.stderr)
        resume_job = getattr(error, "resume_job_id", None)
        if resume_job is not None:
            print("Historical import interrupted. Resume job: " + resume_job, file=sys.stderr)
        return 1
