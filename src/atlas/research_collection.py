"""Frozen two-year plans and resumable session imports through the existing adapter only."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas.config import AtlasError
from atlas.domain import Venue, utc
from atlas.market_data.calendars import HistoricalCalendar, RecordedCalendar
from atlas.market_data.contracts import Adjustment, CorporateAction, DownloadRequest, digest
from atlas.market_data.store import HistoricalStore

UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")
ROOT = Path(__file__).resolve().parents[2]
COORDINATOR = ROOT / "local/market-data/two-year-collection.sqlite3"


def rolling_window(as_of):
    day = utc(as_of).astimezone(ZoneInfo("America/New_York")).date()
    try:
        first = day.replace(year=day.year - 2)
    except ValueError:
        first = day.replace(year=day.year - 2, day=28)
    return first, day - timedelta(days=1)


def sessions_between(first, last):
    """Keep the existing calendar's materialization bound; retain each exact day proof."""
    result = []
    while first <= last:
        end = min(last, first + timedelta(days=365))
        calendar = HistoricalCalendar(Venue.NASDAQ, first, end)
        provenance = calendar.provenance()
        for session in calendar.sessions():
            proof = provenance | {
                "start": str(session.local_date),
                "end": str(session.local_date),
                "entries": [
                    r for r in provenance["entries"] if r["date"] == str(session.local_date)
                ],
            }
            result.append((session, proof))
        first = end + timedelta(days=1)
    return result


def inventory(paths):
    """Inspect only explicitly named historical SQLite stores; never load local settings."""
    found, jobs = {}, []
    for path in paths:
        path = Path(path).resolve()
        if path.suffix != ".sqlite3":
            raise AtlasError("Inventory requires an explicit historical .sqlite3 file.")
        store = HistoricalStore(path, read_only=True)
        with store.connect() as db:
            ids = [
                r[0] for r in db.execute("SELECT dataset_id FROM md_datasets ORDER BY dataset_id")
            ]
            jobs.extend(
                {"store": str(path), **dict(r)}
                for r in db.execute(
                    "SELECT job_id,status,page_count,dataset_id,last_code "
                    "FROM md_jobs ORDER BY job_id"
                )
            )
        for identity in ids:
            manifest, quality, bars = store.inspect(identity)
            request = DownloadRequest.model_validate(manifest["request"])
            if manifest["state"] != "ACCEPTED" or request.provider != "massive":
                continue
            symbol = request.instrument.mapping("massive", request.start, request.end).symbol
            day = request.start.astimezone(ZoneInfo("America/New_York")).date()
            found[(symbol, str(day), request.adjustment.value)] = {
                "store": str(path),
                "dataset_id": identity,
                "manifest_checksum": digest(manifest),
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "rows": len(bars),
                "quality": quality.status,
                "action_coverage": manifest["action_coverage"],
            }
    return found, jobs


def plan_collection(as_of, *, stores=(), first=None, last=None, symbols=UNIVERSE, actions=()):
    earliest, latest = rolling_window(as_of)
    first, last = first or earliest, last or latest
    if not earliest <= first <= last <= latest:
        raise AtlasError("Plan exceeds the calendar-based Basic historical eligibility window.")
    if not symbols or len(set(symbols)) != len(symbols) or set(symbols) - set(UNIVERSE):
        raise AtlasError("Only the explicit five-symbol research universe is supported.")
    existing, jobs = inventory(stores)
    action_records = tuple(
        CorporateAction.model_validate(a).model_dump(mode="json") for a in actions
    )
    tasks = []
    for session, calendar in sessions_between(first, last):
        for symbol in sorted(symbols):
            cached = existing.get((symbol, str(session.local_date), Adjustment.RAW.value))
            if action_records:
                cached = None  # Changed action evidence requires a separately validated import.
            if cached and (
                datetime.fromisoformat(cached["start"]) != session.open
                or datetime.fromisoformat(cached["end"]) != session.close
            ):
                cached = None
            task = {
                "symbol": symbol,
                "date": str(session.local_date),
                "start": session.open.isoformat(),
                "end": session.close.isoformat(),
                "expected_minutes": int((session.close - session.open).total_seconds() / 60),
                "calendar": calendar,
                "adjustment": Adjustment.RAW.value,
                "reusable": cached,
                "action_policy_checksum": digest(action_records),
            }
            task["id"] = digest({k: v for k, v in task.items() if k != "reusable"})
            start_ms, end_ms = (
                int(session.open.timestamp() * 1000),
                int(session.close.timestamp() * 1000) - 1,
            )
            task["requests"] = (
                []
                if cached
                else [
                    {
                        "method": "GET",
                        "path": f"/v3/reference/tickers/{symbol}",
                        "params": {"date": str(session.local_date)},
                        "kind": "ticker_metadata",
                    },
                    {
                        "method": "GET",
                        "path": f"/v2/aggs/ticker/{symbol}/range/1/minute/{start_ms}/{end_ms}",
                        "params": {"adjusted": "false", "sort": "asc", "limit": "5000"},
                        "kind": "minute_aggregates",
                    },
                ]
            )
            tasks.append(task)
    attempts = sum(len(t["requests"]) for t in tasks)
    manifest = {
        "schema": "atlas-two-year-plan-v1",
        "as_of": utc(as_of).isoformat(),
        "eligibility": "CALENDAR_BASED_TWO_YEARS_NOT_AUTHENTICATED_ENTITLEMENT_VERIFICATION",
        "first": str(first),
        "last": str(last),
        "symbols": sorted(symbols),
        "price_policy": "UNADJUSTED_REGULAR_SESSION_ONLY_NO_INTERPOLATION",
        "action_coverage": "UNKNOWN",
        "actions": list(action_records),
        "sessions_per_symbol": len(tasks) // len(symbols),
        "tasks": tasks,
        "existing_jobs": jobs,
        "request_count_without_retries": attempts,
        "max_attempts_three_per_request": attempts * 3,
        "minimum_spacing_seconds": 16,
        "ideal_seconds_without_http": max(0, attempts - 1) * 16,
        "expected_rows": sum(t["expected_minutes"] for t in tasks),
        "conditional_requests": (
            "Adapter-validated same-path next_url pages; unknown cursors cannot be prelisted; "
            "64-page safety bound per task; each retry/page consumes shared quota."
        ),
        "permissions": (
            "DRY_RUN_ONLY; real collection and non-display research rights "
            "require separate approval"
        ),
    }
    return manifest | {"manifest_id": digest(manifest)}


def verify_plan(manifest):
    if manifest.get("schema") != "atlas-two-year-plan-v1" or manifest.get("manifest_id") != digest(
        {k: v for k, v in manifest.items() if k != "manifest_id"}
    ):
        raise AtlasError("Collection manifest identity mismatch.")
    # Rebuild calendar, requests and window rather than trusting a recomputed hash alone.
    rebuilt = plan_collection(
        datetime.fromisoformat(manifest["as_of"]),
        first=date.fromisoformat(manifest["first"]),
        last=date.fromisoformat(manifest["last"]),
        symbols=manifest["symbols"],
        actions=manifest["actions"],
    )
    for actual, expected in zip(manifest["tasks"], rebuilt["tasks"], strict=True):
        actual_definition = {
            k: v for k, v in actual.items() if k not in {"id", "requests", "reusable"}
        }
        if actual["id"] != expected["id"] or actual["id"] != digest(actual_definition):
            raise AtlasError("Collection task/calendar mismatch.")
        if actual["requests"] != ([] if actual["reusable"] else expected["requests"]):
            raise AtlasError("Collection request allowlist mismatch.")
    if manifest["action_coverage"] != "UNKNOWN":
        raise AtlasError("This collector cannot certify complete corporate-action coverage.")
    return manifest


def write_manifest(path, manifest):
    verify_plan(manifest)
    path = Path(path)
    if path.suffix != ".json":
        raise AtlasError("A new public JSON manifest path is required.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, sort_keys=True, indent=2)
        handle.write("\n")


@contextmanager
def exclusive_worker(path):
    """OS ownership releases on process death, without expiring a paused live worker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AtlasError("Another collection worker owns the coordinator.") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class CollectionState:
    def __init__(self, path=COORDINATOR):
        self.path = Path(path).resolve()
        if self.path.suffix != ".sqlite3":
            raise AtlasError("Collection coordinator requires a dedicated .sqlite3 file.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names and names != {"collection_tasks", "collection_meta"}:
                raise AtlasError("Foreign collection coordinator; migration refused.")
            if not names:
                db.execute("CREATE TABLE collection_meta(version INTEGER NOT NULL)")
                db.execute("INSERT INTO collection_meta VALUES(1)")
                db.execute("""CREATE TABLE collection_tasks(
                    id TEXT PRIMARY KEY, definition TEXT NOT NULL, status TEXT NOT NULL,
                    identity TEXT, request TEXT, job_id TEXT, dataset_id TEXT,
                    store TEXT, code TEXT)""")
            if [r[0] for r in db.execute("SELECT version FROM collection_meta")] != [1]:
                raise AtlasError("Unsupported collection schema.")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            db.close()

    def checkpoint(self, task_id, **values):
        allowed = {"status", "identity", "request", "job_id", "dataset_id", "store", "code"}
        if set(values) - allowed:
            raise AtlasError("Unknown collection checkpoint field.")
        with self.connect() as db:
            db.execute(
                "UPDATE collection_tasks SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                (*values.values(), task_id),
            )

    def results(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM collection_tasks ORDER BY id")]


def collect(manifest_path, provider, store, state, *, authorization_reference=None, limit=None):
    """No CLI enables real collection. Tests inject the real adapter with MockTransport."""
    import httpx

    from atlas.market_data.massive import MassiveProvider, identity_instrument
    from atlas.market_data.massive_limits import MassiveQuota, quota_path
    from atlas.market_data.service import download

    path = Path(manifest_path)
    if path.suffix != ".json" or path.stat().st_size > 20_000_000:
        raise AtlasError("Collection requires a frozen public JSON dry-run manifest.")
    manifest = verify_plan(json.loads(path.read_text(encoding="utf-8")))
    if type(provider) is not MassiveProvider or type(provider.quota) is not MassiveQuota:
        raise AtlasError("Collection must use the existing Massive adapter and shared quota.")
    mocked = isinstance(provider.transport, httpx.MockTransport)
    if not mocked:
        if not authorization_reference or state.path != COORDINATOR.resolve():
            raise AtlasError("Real collection is not authorized by an offline dry run.")
        if provider.quota.path != quota_path().resolve():
            raise AtlasError("Real collection cannot switch the shared account quota.")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise AtlasError("Collection task limit must be positive.")
    with exclusive_worker(str(state.path) + ".lock"):
        for index, task in enumerate(manifest["tasks"]):
            if limit is not None and index >= limit:
                break
            definition = json.dumps(
                {
                    "task": {k: v for k, v in task.items() if k not in {"requests", "reusable"}},
                    "provider": provider.descriptor.model_dump(mode="json"),
                    "target_store": str(store.path),
                },
                sort_keys=True,
            )
            with state.connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO collection_tasks(id,definition,status) "
                    "VALUES(?,?,'PENDING')",
                    (task["id"], definition),
                )
                row = dict(
                    db.execute(
                        "SELECT * FROM collection_tasks WHERE id=?", (task["id"],)
                    ).fetchone()
                )
                if row["definition"] != definition:
                    raise AtlasError("Collection checkpoint policy drift.")
            if row["status"] in ("ACCEPTED", "REUSED", "QUARANTINED"):
                checked = HistoricalStore(row["store"], read_only=True).inspect(row["dataset_id"])
                if checked[0]["state"] != (
                    "QUARANTINED" if row["status"] == "QUARANTINED" else "ACCEPTED"
                ):
                    raise AtlasError("Collection dataset state drift.")
                continue
            cached = task["reusable"]
            if cached:
                checked = HistoricalStore(cached["store"], read_only=True).inspect(
                    cached["dataset_id"]
                )
                request = DownloadRequest.model_validate(checked[0]["request"])
                if (
                    digest(checked[0]) != cached["manifest_checksum"]
                    or checked[0]["state"] != "ACCEPTED"
                    or request.provider != "massive"
                    or request.instrument.mapping("massive", request.start, request.end).symbol
                    != task["symbol"]
                    or request.start != datetime.fromisoformat(task["start"])
                    or request.end != datetime.fromisoformat(task["end"])
                    or request.adjustment != Adjustment.RAW
                ):
                    raise AtlasError("Reusable dataset does not match collection task.")
                state.checkpoint(
                    task["id"],
                    status="REUSED",
                    dataset_id=cached["dataset_id"],
                    store=cached["store"],
                )
                continue
            # Recheck retention at dispatch; a frozen two-year plan can age out while downloading.
            current_first, current_last = rolling_window(provider.clock())
            if not current_first <= date.fromisoformat(task["date"]) <= current_last:
                raise AtlasError("Collection task aged outside Basic eligibility; replan required.")
            try:
                identity = (
                    json.loads(row["identity"])
                    if row["identity"]
                    else provider.identity(task["symbol"], date.fromisoformat(task["date"]))
                )
                instrument = identity_instrument(identity)
                if identity["ticker"] != task["symbol"] or identity["as_of"] != task["date"]:
                    raise AtlasError("Saved dated identity does not match the collection task.")
                provider.identities[(task["symbol"], date.fromisoformat(task["date"]))] = identity
                state.checkpoint(
                    task["id"], status="RUNNING", identity=json.dumps(identity, sort_keys=True)
                )
                calendar = RecordedCalendar(task["calendar"])
                if instrument.venue != calendar.venue:
                    raise AtlasError(
                        "Dated listing venue differs from planned calendar; replan required."
                    )
                request = DownloadRequest(
                    provider="massive",
                    instrument=instrument,
                    start=datetime.fromisoformat(task["start"]),
                    end=datetime.fromisoformat(task["end"]),
                    adjustment=Adjustment.RAW,
                )
                if provider.aggregate_path(request) != task["requests"][1]["path"]:
                    raise AtlasError("Dispatch does not match the frozen request manifest.")
                state.checkpoint(task["id"], request=request.model_dump_json())
                actions = tuple(
                    CorporateAction.model_validate(a)
                    for a in manifest["actions"]
                    if a["instrument_id"] == instrument.instrument_id
                )
                dataset, job, _ = download(
                    provider,
                    request,
                    calendar,
                    store,
                    resume_job_id=row["job_id"],
                    actions=actions,
                    action_coverage=manifest["action_coverage"],
                )
                accepted = store.inspect(dataset)[0]["state"]
                state.checkpoint(
                    task["id"],
                    status=accepted,
                    job_id=job,
                    dataset_id=dataset,
                    store=str(store.path),
                    code=None,
                )
            except (AtlasError, ValueError, OSError) as error:
                state.checkpoint(
                    task["id"],
                    status="INTERRUPTED",
                    code="IMPORT_REJECTED_OR_INTERRUPTED",
                    job_id=getattr(error, "resume_job_id", row["job_id"]),
                )
                raise
    return state.results()
