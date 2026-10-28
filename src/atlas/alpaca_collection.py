"""Daily multi-symbol SIP plans; existing immutable per-symbol historical imports."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from atlas.config import AtlasError
from atlas.market_data.alpaca import UNIVERSE, URL, AlpacaProvider, instrument, parameters
from atlas.market_data.calendars import RecordedCalendar
from atlas.market_data.contracts import DownloadRequest, digest
from atlas.market_data.service import download
from atlas.research_collection import ROOT, exclusive_worker, rolling_window, sessions_between


def plan_alpaca(as_of, *, first=None, last=None, stores=(), actions=(), symbols=UNIVERSE):
    earliest, latest = rolling_window(as_of)
    first, last = first or earliest, last or latest
    if not date(2016, 1, 1) <= first <= last <= latest:
        raise AtlasError("Alpaca plan must use completed historical sessions since 2016.")
    if stores or actions:
        raise AtlasError(
            "Alpaca planning does not reuse other-provider stores or unsourced action files."
        )
    if not symbols or len(set(symbols)) != len(symbols) or set(symbols) - set(UNIVERSE):
        raise AtlasError("Invalid Alpaca research universe.")
    tasks = []
    for session, proof in sessions_between(first, last):
        if session.close > as_of - timedelta(minutes=15):
            raise AtlasError("Historical SIP Basic delay gate excludes this session.")
        request = DownloadRequest(
            provider="alpaca-sip",
            instrument=instrument(symbols[0], session.open, session.close),
            start=session.open,
            end=session.close,
        )
        task = {
            "date": str(session.local_date),
            "start": session.open.isoformat(),
            "end": session.close.isoformat(),
            "symbols": sorted(symbols),
            "calendar": proof,
            "requests": [{"method": "GET", "url": URL, "parameters": parameters(request, symbols)}],
            "expected_bars": int((session.close - session.open).total_seconds() // 60)
            * len(symbols),
            "reusable": None,
            "checkpoints": [
                f"alpaca-sip/{symbol}/{session.local_date}" for symbol in sorted(symbols)
            ],
        }
        task["id"] = digest(task)
        tasks.append(task)
    count = len(tasks)
    bars = sum(t["expected_bars"] for t in tasks)
    result = {
        "schema": "atlas-alpaca-daily-plan-v1",
        "provider": "alpaca-sip",
        "as_of": as_of.isoformat(),
        "first": str(first),
        "last": str(last),
        "symbols": sorted(symbols),
        "tasks": tasks,
        "request_count_without_retries": count,
        "expected_minute_bars": bars,
        "pagination": (
            "One expected page/day at <=1950 bars; follow every non-null next_page_token. "
            "Conditional pages/retries add requests; this manifest is not authorization."
        ),
        "estimated_spacing_seconds": round(count * 0.35, 2),
        "estimated_collection_minutes": (
            "5-20 network estimate; validation/storage additional and unbenchmarked"
        ),
        "estimated_storage_bytes": {"low": bars * 800, "high": bars * 1800},
        "entitlement": (
            "Historical SIP access must succeed; Basic excludes latest 15 minutes. "
            "No real-time SIP entitlement asserted."
        ),
        "price_policy": (
            "raw USD; asof=- disables automatic symbol mapping; corporate-action coverage UNKNOWN"
        ),
        "availability": (
            "UNKNOWN; VERIFIED replay excludes these bars; ASSUMED exploratory replay only"
        ),
        "massive_comparison": {"requests": 5008, "duration_hours": "23-26"},
        "authorization": "DRY_RUN_ONLY; full collection requires separate approval",
    }
    result["manifest_id"] = digest(result)
    return result


def collect_alpaca(manifest, provider, store, *, authorization=None):
    """Bounded approved plan only; HTTP pages and symbol jobs both survive interruption."""
    if type(provider) is not AlpacaProvider:
        raise AtlasError("An explicit Alpaca provider is required.")
    expected = plan_alpaca(
        datetime.fromisoformat(manifest["as_of"]),
        first=date.fromisoformat(manifest["first"]),
        last=date.fromisoformat(manifest["last"]),
        symbols=tuple(manifest["symbols"]),
    )
    if expected != manifest or tuple(sorted(provider.symbols)) != tuple(manifest["symbols"]):
        raise AtlasError("Frozen Alpaca manifest or batch universe mismatch.")
    if provider.transport is None and (not authorization or provider.pilot is None):
        # Deliberately no full-network collection activation in this limited pilot release.
        raise AtlasError(
            "Only a separately approved, persistently capped network pilot is enabled."
        )
    if provider.transport is None and (
        len(manifest["tasks"]) != 1 or manifest["symbols"] != ["AAPL"]
    ):
        raise AtlasError(
            "This release permits only the approved one-session AAPL connection pilot."
        )
    results = []
    # One process across all batches; OS ownership releases on death, not on a lease timeout.
    lock = ROOT / "local/market-data/alpaca-collection.lock"
    with exclusive_worker(lock):
        for task in manifest["tasks"]:
            calendar = RecordedCalendar(task["calendar"])
            for symbol in task["symbols"]:
                start, end = (
                    datetime.fromisoformat(task["start"]),
                    datetime.fromisoformat(task["end"]),
                )
                request = DownloadRequest(
                    provider="alpaca-sip",
                    instrument=instrument(symbol, start, end),
                    start=start,
                    end=end,
                )
                # begin_job's exact request/context key resumes existing raw pages and prevents
                # duplicate datasets. Batch cache recovers crashes before the per-symbol checkpoint.
                dataset, job, cached = download(provider, request, calendar, store)
                state = store.inspect(dataset)[0]["state"]
                results.append(
                    {
                        "symbol": symbol,
                        "date": task["date"],
                        "job_id": job,
                        "dataset_id": dataset,
                        "state": state,
                        "cached": cached,
                    }
                )
                if state != "ACCEPTED":
                    raise AtlasError(
                        "Alpaca session quarantined; collection stopped without repair."
                    )
    return results


def write_pilot_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_alpaca_manifest(path, manifest):
    expected = plan_alpaca(
        datetime.fromisoformat(manifest["as_of"]),
        first=date.fromisoformat(manifest["first"]),
        last=date.fromisoformat(manifest["last"]),
        symbols=tuple(manifest["symbols"]),
    )
    if expected != manifest or Path(path).suffix != ".json":
        raise AtlasError("Invalid Alpaca dry-run manifest.")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
