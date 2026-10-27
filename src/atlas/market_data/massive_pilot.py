"""Explicit, lifetime-budgeted AAPL pilot; never runs at import or dashboard startup."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import DownloadRequest, canonical
from atlas.market_data.massive import MassiveProvider, identity_instrument
from atlas.market_data.massive_limits import MassiveQuota
from atlas.market_data.providers import ProviderError
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore

PILOT_ID = "initial-aapl-phase3am-v1"


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pilot-report-", dir=path.parent) as temporary:
        staged = Path(temporary) / "report.json"
        staged.write_text(canonical(report) + "\n", encoding="utf-8")
        os.replace(staged, path)


def verify_restart(database, dataset, expected):
    # No credential/environment export to the fresh verification process.
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if name in os.environ
    }
    environment.update(
        PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", ATLAS_TELEGRAM_ENABLED="false"
    )
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "atlas.main",
            "market-data",
            "inspect",
            dataset,
            "--json",
            "--db",
            str(database),
        ],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    try:
        if process.returncode or json.loads(process.stdout) != expected:
            raise ValueError
    except ValueError:
        raise ProviderError("MASSIVE_FRESH_PROCESS_INTEGRITY_FAILED") from None


def run_pilot(
    *,
    authorized=False,
    root=None,
    quota=None,
    provider_factory=MassiveProvider,
    clock=lambda: datetime.now(UTC),
):
    if not authorized:
        raise ProviderError("MASSIVE_PILOT_REQUIRES_EXPLICIT_NETWORK_OPT_IN")
    root = Path(root or Path(__file__).resolve().parents[3] / "local/market-data/massive-pilot")
    quota = quota or MassiveQuota()
    quota.start_pilot(PILOT_ID)
    report = dict(
        kind="HISTORICAL_MARKET_DATA",
        execution="NONE",
        notifications="NONE",
        pilot_id=PILOT_ID,
        status="FAILED",
        authentication_succeeded=False,
        metadata_available=False,
        minute_aggregates_available=False,
        complete_session=False,
        normalization_passed=False,
        adjustment_verified=False,
        dataset_state="NOT_PUBLISHED",
        dataset_id=None,
        restart_verified=False,
        historical_availability="UNKNOWN",
        error=None,
        started_at=clock().isoformat(),
    )
    try:
        today = clock().astimezone(ZoneInfo("America/New_York")).date()
        selection = HistoricalCalendar(Venue.NASDAQ, today - timedelta(days=14), today)
        sessions = [
            s
            for s in selection.sessions()
            if s.local_date < today and not s.half_day and s.close < clock()
        ]
        if not sessions:
            raise ProviderError("MASSIVE_PILOT_NO_COMPLETED_REGULAR_SESSION")
        session = sessions[-1]
        report["session"] = session.local_date.isoformat()
        adapter = provider_factory(
            quota,
            allow_network=True,
            access_reference="OWNER_AUTHORISED_PHASE3AM_INITIAL_PILOT",
            attempts=1,
            pilot=PILOT_ID,
            clock=clock,
        )
        evidence = adapter.identity("AAPL", session.local_date)
        instrument = identity_instrument(evidence)
        report.update(
            authentication_succeeded=True,
            metadata_available=True,
            ticker="AAPL",
            primary_exchange=instrument.venue.value,
            currency="USD",
            quote_unit="USD",
        )
        calendar = HistoricalCalendar(instrument.venue, session.local_date, session.local_date)
        session = calendar.sessions()[0]
        request = DownloadRequest(
            provider="massive", instrument=instrument, start=session.open, end=session.close
        )
        root.mkdir(parents=True, exist_ok=True)
        (root / "request.json").write_text(request.model_dump_json(indent=2), encoding="utf-8")
        store = HistoricalStore(root / "historical.sqlite3", clock=clock)
        dataset, job, _ = download(adapter, request, calendar, store)
        manifest, quality, bars = store.inspect(dataset)
        report.update(dataset_id=dataset, job_id=job, dataset_state=manifest["state"])
        verify_restart(store.path, dataset, manifest)
        report.update(
            dataset_id=dataset,
            job_id=job,
            dataset_state=manifest["state"],
            minute_aggregates_available=bool(bars),
            complete_session=quality.status != "FAIL" and len(bars) == quality.expected_bars,
            expected_bars=quality.expected_bars,
            stored_bars=len(bars),
            normalization_passed=bool(bars) and quality.status != "FAIL",
            adjustment_verified=all(
                not p.body["aggregate_evidence"]["adjusted"] for p in store.pages(job)
            ),
            quality=quality.model_dump(mode="json"),
            restart_verified=True,
            request_start=request.start.isoformat(),
            request_end=request.end.isoformat(),
            status="COMPLETED" if manifest["state"] == "ACCEPTED" else "QUARANTINED",
        )
    except (AtlasError, ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
        report["error"] = (
            error.code if isinstance(error, ProviderError) else "PILOT_VALIDATION_FAILED"
        )
    finally:
        quota.end_pilot(PILOT_ID, success=report["status"] == "COMPLETED")
        report["attempts"] = quota.audit(PILOT_ID)
        report["authenticated_attempt_count"] = len(report["attempts"])
        report["rate_limit_responses"] = sum(a["status"] == 429 for a in report["attempts"])
        report["finished_at"] = clock().isoformat()
        save_report(root / "report.json", report)
    return report
