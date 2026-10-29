"""Isolated immutable Forex partitions with atomic page/checkpoint commits."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.models import CandlePlan, FxCandle
from atlas.market_data.contracts import canonical, digest


def windows(plan):
    cursor = plan.start
    while cursor < plan.end:
        end = min(plan.end, cursor + timedelta(minutes=plan.page_minutes))
        yield cursor, end
        cursor = end


class ForexStore:
    def __init__(self, path, *, read_only=False):
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise AtlasError("FOREX_STORE_NOT_FOUND")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names and names != {"fx_jobs", "fx_pages", "fx_datasets"}:
                raise AtlasError("Foreign Forex store; migration refused.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS fx_jobs(id TEXT PRIMARY KEY, plan TEXT NOT NULL,
                    next_page INTEGER NOT NULL, status TEXT NOT NULL, dataset TEXT);
                CREATE TABLE IF NOT EXISTS fx_pages(job TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    content TEXT NOT NULL, checksum TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    PRIMARY KEY(job,ordinal));
                CREATE TABLE IF NOT EXISTS fx_datasets(
                    id TEXT PRIMARY KEY, job TEXT UNIQUE NOT NULL,
                    manifest TEXT NOT NULL, checksum TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro" if self.read_only else self.path,
            uri=self.read_only,
            timeout=1,
        )
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _pages(db, job, plan, count):
        pages = db.execute(
            "SELECT ordinal,content,checksum FROM fx_pages WHERE job=? ORDER BY ordinal", (job,)
        ).fetchall()
        if [p[0] for p in pages] != list(range(count)):
            raise AtlasError("FOREX_CHECKPOINT_CORRUPT")
        bars, hashes = [], []
        bounds = list(windows(plan))
        try:
            for ordinal, text, checksum in pages:
                value = json.loads(text)
                if digest(value) != checksum or ordinal >= len(bounds):
                    raise ValueError
                parsed = [FxCandle.model_validate(b) for b in value]
                start, end = bounds[ordinal]
                for b in parsed:
                    expected = {"M": "mid", "B": "bid", "A": "ask"}
                    if any(
                        (getattr(b, field) is not None) != (code in plan.components)
                        for code, field in expected.items()
                    ):
                        raise ValueError
                    if not start <= b.start < end or (bars and b.start <= bars[-1].start):
                        raise ValueError
                    bars.append(b)
                hashes.append(checksum)
        except (ValueError, TypeError, KeyError, AtlasError):
            raise AtlasError("FOREX_PAGE_INTEGRITY_FAILED") from None
        return bars, hashes

    def collect(self, plan, reader, *, checkpoint_hook=lambda: None):
        """No CLI/network construction here; reader must be separately authorized.

        SQLite writer lock spans one bounded request and its commit. A crash
        before commit may repeat that GET; it cannot publish duplicate pages.
        An accepted job is inspected/reused without contacting the reader.
        """
        if not isinstance(plan, CandlePlan):
            raise AtlasError("FOREX_PLAN_REQUIRED")
        content = plan.model_dump(mode="json")
        job = digest(content)
        bounds = list(windows(plan))
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO fx_jobs VALUES(?,?,0,'PENDING',NULL)",
                (job, canonical(content)),
            )
        while True:
            failed = False
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                stored, count, status, dataset = db.execute(
                    "SELECT plan,next_page,status,dataset FROM fx_jobs WHERE id=?", (job,)
                ).fetchone()
                if stored != canonical(content):
                    raise AtlasError("FOREX_PLAN_CHANGED")
                self._pages(db, job, plan, count)
                if status == "ACCEPTED":
                    self.inspect(dataset, check_database=False)
                    return dataset
                if status == "QUARANTINED":
                    raise AtlasError("FOREX_PARTITION_QUARANTINED_REVIEW_REQUIRED")
                if status != "PENDING" or not 0 <= count <= len(bounds):
                    raise AtlasError("FOREX_CHECKPOINT_INVALID")
                if count < len(bounds):
                    start, end = bounds[count]
                    try:
                        candles = reader.candles(plan, start, end)
                    except AtlasError as exc:
                        if str(exc) != "OANDA_CANDLE_CONTRACT_INVALID":
                            raise
                        db.execute("UPDATE fx_jobs SET status='QUARANTINED' WHERE id=?", (job,))
                        failed = True
                    if not failed:
                        values = [b.model_dump(mode="json") for b in candles]
                        db.execute(
                            "INSERT INTO fx_pages VALUES(?,?,?,?,?)",
                            (
                                job,
                                count,
                                canonical(values),
                                digest(values),
                                datetime.now(UTC).isoformat(),
                            ),
                        )
                        # Revalidate before the page and checkpoint can commit.
                        self._pages(db, job, plan, count + 1)
                        db.execute("UPDATE fx_jobs SET next_page=? WHERE id=?", (count + 1, job))
                        checkpoint_hook()
                else:
                    bars, hashes = self._pages(db, job, plan, count)
                    if (not bars and plan.empty_policy == "QUARANTINE") or any(
                        not b.complete for b in bars
                    ):
                        db.execute("UPDATE fx_jobs SET status='QUARANTINED' WHERE id=?", (job,))
                        failed = True
                    else:
                        manifest = self._manifest(plan, hashes, bars)
                        dataset = digest(manifest)
                        db.execute(
                            "INSERT INTO fx_datasets VALUES(?,?,?,?)",
                            (dataset, job, canonical(manifest), digest(manifest)),
                        )
                        db.execute(
                            "UPDATE fx_jobs SET status='ACCEPTED',dataset=? WHERE id=?",
                            (dataset, job),
                        )
                        return dataset
            if failed:
                raise AtlasError("FOREX_PARTITION_QUARANTINED_REVIEW_REQUIRED")

    @staticmethod
    def _manifest(plan, hashes, bars):
        expected = int((plan.end - plan.start).total_seconds() // 60)
        return {
            "schema": "atlas-forex-history-v1",
            "normalizer": "oanda-m1-v1",
            "plan": plan.model_dump(mode="json"),
            "page_hashes": hashes,
            "bars": len(bars),
            "unobserved_minutes": expected - len(bars),
            "warnings": [
                "PUBLICATION_TIMES_UNKNOWN",
                "SCHEDULE_AND_GAPS_UNVERIFIED",
                "ACTIVITY_IS_NOT_TRADED_VOLUME",
                "CANDLE_COMPONENTS_ARE_NOT_SYNCHRONOUS_EXECUTABLE_QUOTES",
            ],
        }

    def inspect(self, dataset, *, check_database=True):
        with self.connect() as db:
            if check_database and db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AtlasError("FOREX_STORE_CORRUPT")
            row = db.execute(
                "SELECT job,manifest,checksum FROM fx_datasets WHERE id=?", (dataset,)
            ).fetchone()
            if row is None:
                raise AtlasError("FOREX_DATASET_NOT_FOUND")
            job, text, checksum = row
            try:
                manifest = json.loads(text)
                if digest(manifest) != checksum or checksum != dataset:
                    raise ValueError
                plan = CandlePlan.model_validate(manifest["plan"])
                stored, count, status, target = db.execute(
                    "SELECT plan,next_page,status,dataset FROM fx_jobs WHERE id=?", (job,)
                ).fetchone()
                if (
                    digest(plan.model_dump(mode="json")) != job
                    or stored != canonical(manifest["plan"])
                    or status != "ACCEPTED"
                    or target != dataset
                    or count != len(list(windows(plan)))
                ):
                    raise ValueError
                bars, hashes = self._pages(db, job, plan, count)
                if (
                    (not bars and plan.empty_policy == "QUARANTINE")
                    or any(not b.complete for b in bars)
                    or manifest != self._manifest(plan, hashes, bars)
                ):
                    raise ValueError
                return manifest, tuple(bars)
            except (ValueError, KeyError, TypeError):
                raise AtlasError("FOREX_DATASET_INTEGRITY_FAILED") from None
