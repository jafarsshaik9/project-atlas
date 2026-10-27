"""Separate historical SQLite store: resumable raw pages and atomic dataset publication."""

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import zlib
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from atlas.config import AtlasError
from atlas.market_data.contracts import (
    CorporateAction,
    DownloadRequest,
    HistoricalBar,
    ProviderDescriptor,
    QualityReport,
    RawPage,
    canonical,
    digest,
)
from atlas.market_data.providers import ProviderError
from atlas.market_data.quality import ARITHMETIC_POLICY

SCHEMA_VERSION = 1
NORMALIZER = "historical-normalization-v2"


class JobOwnershipLost(AtlasError):
    pass


def default_path():
    return Path(__file__).resolve().parents[3] / "local" / "market-data" / "historical.sqlite3"


class HistoricalStore:
    def __init__(self, path=None, *, clock=lambda: datetime.now(UTC), read_only=False):
        self.path = Path(path or default_path()).resolve()
        self.clock = clock
        self.read_only = read_only
        try:
            if read_only:
                if not self.path.is_file():
                    raise AtlasError(
                        "Historical database does not exist; download to this path first."
                    )
                with self.connect() as db:
                    self._check_schema(db)
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                    tables = {
                        r[0]
                        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                    if tables and "md_meta" not in tables:
                        raise AtlasError(
                            "Selected file is not an Atlas historical store; it was not migrated."
                        )
                    if "md_meta" in tables:
                        self._check_schema(db)
            with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
            with self.connect(write=True) as db:
                db.execute("CREATE TABLE IF NOT EXISTS md_meta(version INTEGER NOT NULL)")
                row = db.execute("SELECT version FROM md_meta").fetchone()
                if row is None:
                    db.execute("INSERT INTO md_meta VALUES(?)", (SCHEMA_VERSION,))
                elif row[0] != SCHEMA_VERSION:
                    raise AtlasError("Unsupported historical-store schema; migration refused.")
                db.execute("""CREATE TABLE IF NOT EXISTS md_jobs (
                    job_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, request_json TEXT NOT NULL,
                    status TEXT NOT NULL, owner TEXT, lease_until TEXT, cursor TEXT,
                    page_count INTEGER NOT NULL DEFAULT 0, dataset_id TEXT, last_code TEXT)""")
                db.execute("""CREATE TABLE IF NOT EXISTS md_pages (
                    job_id TEXT NOT NULL REFERENCES md_jobs, ordinal INTEGER NOT NULL,
                    content BLOB NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(job_id,ordinal))""")
                db.execute("""CREATE TABLE IF NOT EXISTS md_datasets (
                    dataset_id TEXT PRIMARY KEY, manifest TEXT NOT NULL, quality TEXT NOT NULL,
                    manifest_checksum TEXT NOT NULL)""")
                db.execute("""CREATE TABLE IF NOT EXISTS md_bars (
                    dataset_id TEXT NOT NULL REFERENCES md_datasets, ordinal INTEGER NOT NULL,
                    content TEXT NOT NULL, PRIMARY KEY(dataset_id,ordinal))""")
                db.execute("""CREATE TABLE IF NOT EXISTS md_budget (
                    provider TEXT PRIMARY KEY, day TEXT NOT NULL, used INTEGER NOT NULL,
                    next_at TEXT NOT NULL)""")
        except (OSError, sqlite3.Error):
            raise AtlasError(
                "Historical store unavailable; check the private path and permissions."
            ) from None

    @staticmethod
    def _check_schema(db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "md_meta" not in tables:
            raise AtlasError("Selected file is not an Atlas historical database.")
        versions = db.execute("SELECT version FROM md_meta").fetchall()
        if len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
            raise AtlasError("Unsupported historical-store schema; migration refused.")

    @contextmanager
    def connect(self, *, write=False):
        if write and self.read_only:
            raise AtlasError("Historical database is open read-only; write refused.")
        connection = None
        try:
            mode = "rw" if write else "ro"
            connection = sqlite3.connect(
                self.path.as_uri() + f"?mode={mode}", uri=True, timeout=5, isolation_level=None
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            if write:
                connection.commit()
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise AtlasError(
                "Historical store transaction failed; no source payload is shown."
            ) from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _owned(db, job_id, owner, now):
        row = db.execute(
            """SELECT * FROM md_jobs WHERE job_id=? AND owner=?
            AND status='RUNNING' AND lease_until>?""",
            (job_id, owner, now.isoformat()),
        ).fetchone()
        if row is None:
            raise JobOwnershipLost(
                "Historical import ownership expired or changed; no publication permitted."
            )
        return row

    @staticmethod
    def _job_key(request, calendar, context, *, normalizer=NORMALIZER):
        payload = {
            "request": request.model_dump(mode="json"),
            "calendar": calendar,
            "normalizer": normalizer,
            "import_context": context,
        }
        if normalizer == NORMALIZER:
            payload["arithmetic"] = ARITHMETIC_POLICY
        return digest(payload)

    def begin_job(
        self,
        request,
        calendar_provenance,
        *,
        refresh=False,
        import_context=None,
        resume_job_id=None,
    ):
        request = DownloadRequest.model_validate(request.model_dump())
        if refresh and resume_job_id is not None:
            raise AtlasError("Choose a new refresh or an existing resume job, not both.")
        if resume_job_id is not None:
            self._identifier(resume_job_id)
        request_key = self._job_key(request, calendar_provenance, import_context)
        job_id = resume_job_id or (
            digest({"request_key": request_key, "refresh": uuid4().hex}) if refresh else request_key
        )
        owner = uuid4().hex
        with self.connect(write=True) as db:
            now = self.clock()
            row = db.execute("SELECT * FROM md_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row and row["status"] not in ("RUNNING", "INTERRUPTED", "COMPLETED"):
                raise AtlasError("Historical job has an unsupported state; resume refused.")
            if resume_job_id is not None and row is None:
                raise AtlasError("Historical resume job was not found; no new job created.")
            if row and (
                row["request_key"] != request_key
                or json.loads(row["request_json"]) != request.model_dump(mode="json")
            ):
                raise AtlasError(
                    "Resume request/provider/calendar/policy mismatch; use a new import."
                )
            if row and row["status"] == "COMPLETED":
                return job_id, None, row["dataset_id"]
            if row and row["status"] == "RUNNING" and row["lease_until"] > now.isoformat():
                raise AtlasError("An active worker already owns this historical import.")
            db.execute(
                """INSERT INTO md_jobs(job_id,request_key,request_json,status,owner,lease_until)
                VALUES(?,?,?,'RUNNING',?,?) ON CONFLICT(job_id) DO UPDATE SET status='RUNNING',
                owner=excluded.owner,lease_until=excluded.lease_until,last_code=NULL""",
                (
                    job_id,
                    request_key,
                    request.model_dump_json(),
                    owner,
                    (now + timedelta(seconds=90)).isoformat(),
                ),
            )
        return job_id, owner, None

    def checkpoint(self, job_id, owner, page):
        page = RawPage.model_validate(page.model_dump())
        encoded = page.model_dump_json().encode()
        if len(encoded) > 8_000_000:
            raise AtlasError("Historical page exceeds storage size limit.")
        with self.connect(write=True) as db:
            now = self.clock()
            row = self._owned(db, job_id, owner, now)
            if row["page_count"] and row["cursor"] is None:
                raise AtlasError("Checkpoint chain is already terminal; append refused.")
            if row["page_count"] >= 64:
                raise AtlasError(
                    "Historical pagination bound exceeded; resume requires a smaller request."
                )
            db.execute(
                "INSERT INTO md_pages VALUES(?,?,?,?)",
                (
                    job_id,
                    row["page_count"],
                    zlib.compress(encoded),
                    hashlib.sha256(encoded).hexdigest(),
                ),
            )
            db.execute(
                "UPDATE md_jobs SET cursor=?,page_count=page_count+1,lease_until=? WHERE job_id=?",
                (page.next_cursor, (now + timedelta(seconds=90)).isoformat(), job_id),
            )

    def heartbeat(self, job_id, owner):
        with self.connect(write=True) as db:
            now = self.clock()
            self._owned(db, job_id, owner, now)
            db.execute(
                "UPDATE md_jobs SET lease_until=? WHERE job_id=?",
                ((now + timedelta(seconds=90)).isoformat(), job_id),
            )

    def pages(self, job_id):
        with self.connect() as db:
            job = db.execute("SELECT * FROM md_jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise AtlasError("Historical import job not found.")
            rows = db.execute(
                "SELECT * FROM md_pages WHERE job_id=? ORDER BY ordinal", (job_id,)
            ).fetchall()
        pages = self._decode_pages(rows)
        self._check_chain(job, pages)
        return pages

    @staticmethod
    def _decode_pages(rows):
        result = []
        for index, row in enumerate(rows):
            try:
                decompressor = zlib.decompressobj()
                raw = decompressor.decompress(row["content"], 8_000_001)
                if len(raw) > 8_000_000 or not decompressor.eof or decompressor.unused_data:
                    raise ValueError
                if row["ordinal"] != index or hashlib.sha256(raw).hexdigest() != row["checksum"]:
                    raise ValueError
                result.append(RawPage.model_validate_json(raw))
            except (zlib.error, ValueError):
                raise AtlasError(
                    "Historical raw-page integrity check failed; import is not trusted."
                ) from None
        return tuple(result)

    @staticmethod
    def _check_chain(job, pages, *, complete=False):
        cursors = [page.next_cursor for page in pages[:-1]]
        if (
            len(pages) != job["page_count"]
            or len(pages) > 64
            or any(cursor is None for cursor in cursors)
            or len(cursors) != len(set(cursors))
            or job["cursor"] != (pages[-1].next_cursor if pages else None)
            or (pages and pages[-1].next_cursor in cursors)
            or (complete and (not pages or pages[-1].next_cursor is not None))
        ):
            raise AtlasError("Historical source checkpoint chain is incomplete or inconsistent.")

    @staticmethod
    def _page_token(rows):
        # Only bounded compressed-byte hashing occurs under the final write lock.
        return tuple(
            (row["ordinal"], row["checksum"], hashlib.sha256(row["content"]).hexdigest())
            for row in rows
        )

    @classmethod
    def _check_binding(
        cls, job, request, descriptor, calendar, actions, action_coverage, *, normalizer=NORMALIZER
    ):
        context = {"provider": descriptor, "actions": actions, "action_coverage": action_coverage}
        if (
            normalizer not in ("historical-normalization-v1", NORMALIZER)
            or json.loads(job["request_json"]) != request.model_dump(mode="json")
            or job["request_key"] != cls._job_key(request, calendar, context, normalizer=normalizer)
        ):
            raise AtlasError("Historical publication does not match its stored job context.")

    @staticmethod
    def _verify_batch(
        request,
        descriptor,
        calendar,
        bars,
        report,
        pages,
        actions,
        action_coverage,
        *,
        heartbeat=None,
    ):
        from zoneinfo import ZoneInfo

        from atlas.market_data.calendars import RecordedCalendar
        from atlas.market_data.quality import assess

        request = DownloadRequest.model_validate(request.model_dump())
        descriptor = ProviderDescriptor.model_validate(descriptor.model_dump())
        actions = tuple(CorporateAction.model_validate(a.model_dump()) for a in actions)
        bars = tuple(HistoricalBar.model_validate(b.model_dump()) for b in bars)
        report = QualityReport.model_validate(report.model_dump())
        recorded = RecordedCalendar(calendar)
        if (
            request.provider != descriptor.provider_id
            or request.instrument.venue not in descriptor.exchanges
            or request.resolution not in descriptor.resolutions
            or recorded.venue != request.instrument.venue
            or action_coverage not in ("UNKNOWN", "PARTIAL", "PROVIDER_DECLARED_COMPLETE")
        ):
            raise AtlasError("Historical request/provider/calendar metadata is inconsistent.")
        zone = ZoneInfo(request.instrument.exchange_timezone)
        day = request.start.astimezone(zone).date()
        last = (request.end - timedelta(microseconds=1)).astimezone(zone).date()
        while day <= last:
            recorded.session(request.instrument.venue, day)
            day += timedelta(days=1)
        checked, expected = assess(
            request,
            pages,
            recorded,
            actions=actions,
            action_coverage=action_coverage,
            heartbeat=heartbeat,
        )

        def values(rows):
            return [b.model_dump(mode="json", exclude={"dataset_version"}) for b in rows]

        if report != expected or values(bars) != values(checked):
            raise AtlasError(
                "Historical bars/quality report differ from durable source validation."
            )
        return request, descriptor, checked, expected, actions

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM md_jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def interrupted(self, job_id, owner, code):
        with self.connect(write=True) as db:
            db.execute(
                """UPDATE md_jobs SET status='INTERRUPTED',last_code=?,owner=NULL,lease_until=NULL
                WHERE job_id=? AND owner=? AND status='RUNNING' AND lease_until>?""",
                (code, job_id, owner, self.clock().isoformat()),
            )

    def publish(
        self,
        job_id,
        owner,
        request,
        descriptor,
        calendar,
        bars,
        report,
        pages,
        actions,
        action_coverage,
    ):
        # Snapshot source under one read transaction; normalize outside the write lock.
        with self.connect() as db:
            job = dict(self._owned(db, job_id, owner, self.clock()))
            rows = db.execute(
                "SELECT * FROM md_pages WHERE job_id=? ORDER BY ordinal", (job_id,)
            ).fetchall()
        durable = self._decode_pages(rows)
        self._check_chain(job, durable, complete=True)
        token = self._page_token(rows)
        try:
            calendar = json.loads(canonical(calendar))
            supplied = [p.model_dump(mode="json") for p in pages]
            if supplied != [p.model_dump(mode="json") for p in durable]:
                raise AtlasError("Supplied source pages differ from durable checkpoints.")
            request, descriptor, bars, report, actions = self._verify_batch(
                request,
                descriptor,
                calendar,
                bars,
                report,
                durable,
                actions,
                action_coverage,
                heartbeat=lambda: self.heartbeat(job_id, owner),
            )
            self._check_binding(
                job,
                request,
                descriptor.model_dump(mode="json"),
                calendar,
                [a.model_dump(mode="json") for a in actions],
                action_coverage,
            )
        except (ValueError, KeyError, TypeError):
            raise AtlasError(
                "Invalid historical publication contract; no dataset published."
            ) from None
        pages = durable
        content = {
            "schema_version": 1,
            "normalization_version": NORMALIZER,
            "kind": "HISTORICAL_MARKET_DATA",
            "execution": "NONE",
            "origin": descriptor.origin.value,
            "provider": descriptor.provider_id,
            "provider_descriptor": descriptor.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
            "calendar": calendar,
            "bars": [bar.content() for bar in bars],
            "quality": report.model_dump(mode="json"),
            "raw_body_checksums": [digest(p.body) for p in pages],
            "actions": [a.model_dump(mode="json") for a in actions],
            "action_coverage": action_coverage,
            "quality_policy": {
                "version": report.policy_version,
                "price_discontinuity_fraction": "0.30",
                "arithmetic": ARITHMETIC_POLICY,
            },
        }
        dataset_id = digest(content)
        encoded_bars = [
            bar.model_copy(update={"dataset_version": dataset_id}).model_dump_json() for bar in bars
        ]
        storage_checksum = digest([json.loads(b) for b in encoded_bars])
        manifest = {k: v for k, v in content.items() if k not in ("bars", "quality")}
        manifest.update(
            dataset_id=dataset_id,
            source_job_id=job_id,
            source_page_checksums=[row["checksum"] for row in rows],
            state="QUARANTINED" if report.status == "FAIL" else "ACCEPTED",
            first_ingested_at=min(p.fetched_at for p in pages).isoformat(),
            last_ingested_at=max(p.fetched_at for p in pages).isoformat(),
            row_count=len(bars),
            storage_checksum=storage_checksum,
            quality_checksum=digest(report.model_dump(mode="json")),
            quality_reference="embedded:md_datasets.quality",
            observed_start=min((b.start for b in bars), default=None).isoformat() if bars else None,
            observed_end=max((b.end for b in bars), default=None).isoformat() if bars else None,
            unknown_availability_rows=sum(b.availability.value == "UNKNOWN" for b in bars),
            wire_checksums=[p.wire_sha256 for p in pages],
            transformations=[
                "Explicit UTC timestamp normalization",
                "Stable chronological ordering",
                "No price/volume repair or interpolation",
                "Allowlisted metadata and known-key redaction",
            ],
            licensing=descriptor.licensing,
            provider_descriptor=descriptor.model_dump(mode="json"),
        )
        with self.connect() as db:
            existing = db.execute(
                "SELECT 1 FROM md_datasets WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
        if existing:
            self.inspect(dataset_id)
        with self.connect(write=True) as db:
            current = self._owned(db, job_id, owner, self.clock())
            current_rows = db.execute(
                "SELECT ordinal,checksum,content FROM md_pages WHERE job_id=? ORDER BY ordinal",
                (job_id,),
            ).fetchall()
            if self._page_token(current_rows) != token or any(
                current[key] != job[key]
                for key in ("request_key", "request_json", "page_count", "cursor")
            ):
                raise AtlasError(
                    "Historical checkpoints changed during validation; publication refused."
                )
            if not db.execute(
                "SELECT 1 FROM md_datasets WHERE dataset_id=?", (dataset_id,)
            ).fetchone():
                db.execute(
                    "INSERT INTO md_datasets VALUES(?,?,?,?)",
                    (dataset_id, canonical(manifest), report.model_dump_json(), digest(manifest)),
                )
                db.executemany(
                    "INSERT INTO md_bars VALUES(?,?,?)",
                    ((dataset_id, i, value) for i, value in enumerate(encoded_bars)),
                )
            db.execute(
                "UPDATE md_jobs SET status='COMPLETED',dataset_id=?,owner=NULL,lease_until=NULL "
                "WHERE job_id=?",
                (dataset_id, job_id),
            )
        return dataset_id

    def inspect(self, dataset_id):
        self._identifier(dataset_id)
        try:
            with self.connect() as db:
                row = db.execute(
                    "SELECT * FROM md_datasets WHERE dataset_id=?", (dataset_id,)
                ).fetchone()
                if row is None:
                    raise AtlasError("Historical dataset not found.")
                manifest = json.loads(row["manifest"])
                if not isinstance(manifest, dict):
                    raise ValueError
                values = db.execute(
                    "SELECT ordinal,content FROM md_bars WHERE dataset_id=? ORDER BY ordinal",
                    (dataset_id,),
                ).fetchall()
                source_id = manifest.get("source_job_id")
                if source_id is not None:
                    self._identifier(source_id)
                    job = db.execute(
                        "SELECT * FROM md_jobs WHERE job_id=? AND dataset_id=? "
                        "AND status='COMPLETED'",
                        (source_id, dataset_id),
                    ).fetchone()
                else:
                    # Legacy v1 had no origin job ID. Fail closed if its first source cannot verify.
                    job = db.execute(
                        "SELECT * FROM md_jobs WHERE dataset_id=? AND status='COMPLETED' "
                        "ORDER BY rowid LIMIT 1",
                        (dataset_id,),
                    ).fetchone()
                if job is None:
                    raise AtlasError("Historical completed source job is missing.")
                source_rows = db.execute(
                    "SELECT * FROM md_pages WHERE job_id=? ORDER BY ordinal", (job["job_id"],)
                ).fetchall()
            manifest, report = (
                manifest,
                QualityReport.model_validate_json(row["quality"]),
            )
            bars = tuple(HistoricalBar.model_validate_json(r["content"]) for r in values)
            if (
                [r["ordinal"] for r in values] != list(range(len(bars)))
                or len(bars) != manifest["row_count"]
                or digest([b.model_dump(mode="json") for b in bars]) != manifest["storage_checksum"]
                or digest(report.model_dump(mode="json")) != manifest["quality_checksum"]
                or any(b.dataset_version != dataset_id for b in bars)
                or digest(manifest) != row["manifest_checksum"]
                or manifest["state"] != ("QUARANTINED" if report.status == "FAIL" else "ACCEPTED")
            ):
                raise ValueError
            content = {
                k: manifest[k]
                for k in (
                    "schema_version",
                    "normalization_version",
                    "kind",
                    "execution",
                    "origin",
                    "provider",
                    "provider_descriptor",
                    "request",
                    "calendar",
                    "raw_body_checksums",
                    "actions",
                    "action_coverage",
                    "quality_policy",
                )
            }
            content.update(bars=[b.content() for b in bars], quality=report.model_dump(mode="json"))
            if digest(content) != dataset_id or manifest["dataset_id"] != dataset_id:
                raise ValueError
            pages = self._decode_pages(source_rows)
            self._check_chain(job, pages, complete=True)
            normalizer = manifest["normalization_version"]
            expected_policy = {
                "version": report.policy_version,
                "price_discontinuity_fraction": "0.30",
            }
            if normalizer == NORMALIZER:
                expected_policy["arithmetic"] = ARITHMETIC_POLICY
            if manifest["quality_policy"] != expected_policy:
                raise ValueError
            if (
                manifest["raw_body_checksums"] != [digest(p.body) for p in pages]
                or manifest["wire_checksums"] != [p.wire_sha256 for p in pages]
                or manifest["first_ingested_at"] != min(p.fetched_at for p in pages).isoformat()
                or manifest["last_ingested_at"] != max(p.fetched_at for p in pages).isoformat()
                or (
                    normalizer == NORMALIZER
                    and (
                        source_id is None
                        or manifest["source_page_checksums"] != [r["checksum"] for r in source_rows]
                        or manifest["quality_policy"]
                        != {
                            "version": report.policy_version,
                            "price_discontinuity_fraction": "0.30",
                            "arithmetic": ARITHMETIC_POLICY,
                        }
                    )
                )
            ):
                raise ValueError
            request = DownloadRequest.model_validate(manifest["request"])
            descriptor = ProviderDescriptor.model_validate(manifest["provider_descriptor"])
            actions = tuple(CorporateAction.model_validate(a) for a in manifest["actions"])
            if (
                manifest["schema_version"] != 1
                or manifest["kind"] != "HISTORICAL_MARKET_DATA"
                or manifest["execution"] != "NONE"
                or manifest["provider"] != request.provider
                or manifest["origin"] != descriptor.origin.value
                or manifest["observed_start"]
                != (min(b.start for b in bars).isoformat() if bars else None)
                or manifest["observed_end"]
                != (max(b.end for b in bars).isoformat() if bars else None)
                or manifest["unknown_availability_rows"]
                != sum(b.availability.value == "UNKNOWN" for b in bars)
            ):
                raise ValueError
            self._check_binding(
                job,
                request,
                manifest["provider_descriptor"],
                manifest["calendar"],
                manifest["actions"],
                manifest["action_coverage"],
                normalizer=normalizer,
            )
            self._verify_batch(
                request,
                descriptor,
                manifest["calendar"],
                bars,
                report,
                pages,
                actions,
                manifest["action_coverage"],
            )
        except (ValueError, KeyError, TypeError):
            raise AtlasError(
                "Historical dataset checksum/schema failed; data is not trusted."
            ) from None
        return manifest, report, bars

    def visible_bars(self, dataset_id, as_of, *, allow_assumed=False):
        from atlas.domain import utc

        utc(as_of)
        manifest, _, bars = self.inspect(dataset_id)
        if manifest["state"] != "ACCEPTED":
            raise AtlasError("Quarantined historical data cannot be selected for research.")
        return tuple(b for b in bars if b.visible_at(as_of, allow_assumed=allow_assumed))

    def validate(self, dataset_id):
        return self.inspect(dataset_id)[1]

    def coverage(self, *, limit=50, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise AtlasError("Historical coverage query exceeds bounded limits.")
        with self.connect() as db:
            rows = db.execute(
                "SELECT manifest,manifest_checksum FROM md_datasets "
                "ORDER BY dataset_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = db.execute("SELECT count(*) FROM md_datasets").fetchone()[0]
        manifests = [json.loads(r[0]) for r in rows]
        if any(digest(manifest) != row[1] for manifest, row in zip(manifests, rows, strict=True)):
            raise AtlasError("Historical manifest integrity check failed.")
        for manifest in manifests:
            manifest["integrity"] = "NOT_VERIFIED"
            manifest["integrity_note"] = (
                "Metadata only; inspect/validate verifies source and row integrity."
            )
        return manifests, total

    def reserve_request(self, provider, *, daily_limit=800):
        with self.connect(write=True) as db:
            now = self.clock()
            row = db.execute("SELECT * FROM md_budget WHERE provider=?", (provider,)).fetchone()
            day = now.date().isoformat()
            if row and row["day"] > day:
                raise ProviderError("CLOCK_MOVED_BACKWARDS")
            used = row["used"] if row and row["day"] == day else 0
            if used >= daily_limit:
                raise ProviderError("LOCAL_DAILY_BUDGET_EXHAUSTED")
            if row and row["next_at"] > now.isoformat():
                return math.ceil((datetime.fromisoformat(row["next_at"]) - now).total_seconds())
            db.execute(
                "INSERT INTO md_budget VALUES(?,?,?,?) ON CONFLICT(provider) DO UPDATE SET "
                "day=excluded.day,used=excluded.used,next_at=excluded.next_at",
                (provider, day, used + 1, (now + timedelta(seconds=8)).isoformat()),
            )
        return 0

    def defer_requests(self, provider, seconds):
        with self.connect(write=True) as db:
            until = (self.clock() + timedelta(seconds=seconds)).isoformat()
            db.execute(
                "UPDATE md_budget SET next_at=max(next_at,?) WHERE provider=?", (until, provider)
            )

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination.exists() or destination == self.path:
            raise AtlasError("Use a new, private backup destination.")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Own only this private staging directory, never a placeholder at destination.
            # A failure therefore cannot remove a file created by a competing process.
            with tempfile.TemporaryDirectory(
                prefix=".atlas-backup-", dir=destination.parent
            ) as stage:
                staged = Path(stage) / "snapshot.sqlite3"
                with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(staged)) as target:
                        source.backup(target)
                        # Publish a self-contained file, with no uncheckpointed WAL dependency.
                        if target.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                            raise AtlasError(
                                "Historical backup could not become a standalone file."
                            )
                        self._check_schema(target)
                        if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                            raise AtlasError(
                                "Historical backup failed SQLite integrity verification."
                            )
                # Windows rename refuses replacement. POSIX rename would overwrite, so use
                # atomic hard-link creation there. Both paths fail if destination appeared.
                if os.name == "nt":
                    os.rename(staged, destination)
                else:
                    os.link(staged, destination)
        except (sqlite3.Error, OSError):
            raise AtlasError(
                "Historical backup failed; use a new private destination on a supported filesystem."
            ) from None

    @staticmethod
    def _identifier(value):
        import re

        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise AtlasError("Expected a historical dataset SHA-256 identifier.")
