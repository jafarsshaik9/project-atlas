"""Independent, process-shared Alpaca attempt ledger. Uncertain requests fail closed."""

import sqlite3
import time
import uuid
from pathlib import Path

from atlas.config import AtlasError

DEFAULT_QUOTA = Path(__file__).resolve().parents[3] / "local/market-data/alpaca-quota.sqlite3"


class AlpacaQuota:
    def __init__(self, path=DEFAULT_QUOTA, *, clock=time.time, sleep=time.sleep):
        self.path, self.clock, self.sleep = Path(path).resolve(), clock, sleep
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            names = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if names and names != {"state", "attempts", "pilots"}:
                raise AtlasError("Foreign or incomplete Alpaca quota schema; migration refused.")
            if not names:
                db.executescript("""
                CREATE TABLE IF NOT EXISTS state(
                    id INTEGER PRIMARY KEY CHECK(id=1), last REAL NOT NULL,
                    blocked REAL NOT NULL, pending TEXT);
                INSERT OR IGNORE INTO state VALUES(1,0,0,NULL);
                CREATE TABLE IF NOT EXISTS attempts(
                    id TEXT PRIMARY KEY, started REAL NOT NULL, finished REAL,
                    status INTEGER, pilot TEXT);
                CREATE TABLE IF NOT EXISTS pilots(
                    id TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK(used BETWEEN 0 AND 2));
                """)
            self.validate(db)

    @staticmethod
    def validate(db):
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise AtlasError("Alpaca quota integrity check failed.")
        state = db.execute("SELECT last,blocked,pending FROM state WHERE id=1").fetchone()
        pending = [r[0] for r in db.execute("SELECT id FROM attempts WHERE finished IS NULL")]
        latest = db.execute(
            "SELECT max(max(started,coalesce(finished,started))) FROM attempts"
        ).fetchone()[0]
        if (
            not state
            or (latest is not None and state[0] != latest)
            or len(db.execute("SELECT id FROM state").fetchall()) != 1
            or pending != ([state[2]] if state[2] else [])
            or db.execute(
                "SELECT count(*) FROM pilots p WHERE used != "
                "(SELECT count(*) FROM attempts a WHERE a.pilot=p.id)"
            ).fetchone()[0]
            or db.execute(
                "SELECT count(*) FROM attempts WHERE started<0 OR "
                "(finished IS NOT NULL AND finished<started) OR "
                "(pilot IS NOT NULL AND pilot NOT IN (SELECT id FROM pilots))"
            ).fetchone()[0]
        ):
            raise AtlasError("Alpaca quota history is inconsistent; reset refused.")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def start_pilot(self, identity):
        with self.connect() as db:
            try:
                db.execute("INSERT INTO pilots VALUES(?,0)", (identity,))
            except sqlite3.IntegrityError:
                raise AtlasError(
                    "Alpaca pilot already started; its budget cannot be reset."
                ) from None

    def acquire(self, pilot=None, heartbeat=lambda: None):
        while True:
            now = self.clock()
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self.validate(db)
                last, blocked, pending = db.execute(
                    "SELECT last,blocked,pending FROM state WHERE id=1"
                ).fetchone()
                if now < last or pending:
                    raise AtlasError("Alpaca quota has clock regression or an uncertain request.")
                recent = db.execute(
                    "SELECT started FROM attempts WHERE started>? ORDER BY started", (now - 61,)
                ).fetchall()
                wait = max(0, blocked - now, last + 0.35 - now)
                if len(recent) >= 180:
                    wait = max(wait, recent[-180][0] + 61 - now)
                if not wait:
                    if pilot is not None:
                        changed = db.execute(
                            "UPDATE pilots SET used=used+1 WHERE id=? AND used<2", (pilot,)
                        ).rowcount
                        if changed != 1:
                            raise AtlasError(
                                "Alpaca pilot's two-attempt lifetime budget is exhausted."
                            )
                    identity = uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO attempts VALUES(?,?,NULL,NULL,?)", (identity, now, pilot)
                    )
                    db.execute("UPDATE state SET last=?,pending=? WHERE id=1", (now, identity))
                    return identity
                if wait > 300:
                    raise AtlasError("Alpaca cooldown requires later resume; quota was not reset.")
            heartbeat()
            self.sleep(min(wait, 5))

    def finish(self, identity, status, *, backoff=0):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT last,pending FROM state WHERE id=1").fetchone()
            if row is None or row[1] != identity or now < row[0]:
                raise AtlasError("Alpaca quota completion ownership or clock mismatch.")
            db.execute(
                "UPDATE attempts SET finished=?,status=? WHERE id=?", (now, status, identity)
            )
            db.execute(
                "UPDATE state SET last=?,blocked=?,pending=NULL WHERE id=1", (now, now + backoff)
            )

    def attempt_count(self, pilot=None):
        with self.connect() as db:
            if pilot is None:
                return db.execute("SELECT count(*) FROM attempts").fetchone()[0]
            return db.execute("SELECT used FROM pilots WHERE id=?", (pilot,)).fetchone()[0]
