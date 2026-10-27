"""One persistent account quota for every Massive endpoint, with conservative fencing."""

import math
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from atlas.market_data.providers import ProviderError


def quota_path():
    return Path(__file__).resolve().parents[3] / "local/market-data/massive-quota.sqlite3"


class MassiveQuota:
    """Short SQLite transactions; never hold a transaction while waiting or using HTTP.

    In-flight authority has no automatic expiry. Its completion timestamp charges
    the whole possible dispatch interval. A crashed owner therefore blocks instead
    of letting a paused request send outside the recorded rolling window.
    """

    def __init__(self, path=None, *, clock=time.monotonic, sleep=time.sleep):
        self.path = Path(path or quota_path()).resolve()
        self.clock, self.sleep = clock, sleep
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_store = not self.path.exists()
        with self.transaction() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                if not new_store:
                    raise ProviderError("MASSIVE_QUOTA_HISTORY_MISSING_REVIEW_REQUIRED")
                self._initialize(db)
            self._validate_schema(db)

    @staticmethod
    def _initialize(db):
        db.execute(
            "CREATE TABLE massive_state ("
            "id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER, "
            "last_tick REAL, blocked_until REAL)"
        )
        db.execute("INSERT INTO massive_state VALUES(1,1,0,0)")
        db.execute(
            "CREATE TABLE massive_attempts ("
            "token TEXT PRIMARY KEY, started REAL NOT NULL, charged REAL, "
            "endpoint TEXT NOT NULL, status INTEGER, rows INTEGER, pilot TEXT, "
            "recorded_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
        )
        db.execute(
            "CREATE TABLE massive_pilots ("
            "id TEXT PRIMARY KEY, used INTEGER NOT NULL, status TEXT NOT NULL)"
        )
        db.execute("CREATE INDEX massive_charge ON massive_attempts(charged)")
        db.execute(
            "CREATE UNIQUE INDEX massive_singleflight "
            "ON massive_attempts((1)) WHERE charged IS NULL"
        )

    @staticmethod
    def _validate_schema(db):
        required = {
            "massive_state": ("id", "version", "last_tick", "blocked_until"),
            "massive_attempts": (
                "token",
                "started",
                "charged",
                "endpoint",
                "status",
                "rows",
                "pilot",
                "recorded_at",
            ),
            "massive_pilots": ("id", "used", "status"),
        }
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(required).issubset(tables):
            raise ProviderError("MASSIVE_QUOTA_HISTORY_MISSING_REVIEW_REQUIRED")
        for table, columns in required.items():
            actual = tuple(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
            if actual != columns:
                raise ProviderError("MASSIVE_QUOTA_SCHEMA_DAMAGED_REVIEW_REQUIRED")
        indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        if not {"massive_charge", "massive_singleflight"}.issubset(indexes):
            raise ProviderError("MASSIVE_QUOTA_SCHEMA_DAMAGED_REVIEW_REQUIRED")
        rows = db.execute("SELECT * FROM massive_state").fetchall()
        if (
            len(rows) != 1
            or rows[0]["id"] != 1
            or rows[0]["version"] != 1
            or not math.isfinite(rows[0]["last_tick"])
            or (
                rows[0]["blocked_until"] is not None and not math.isfinite(rows[0]["blocked_until"])
            )
        ):
            raise ProviderError("MASSIVE_QUOTA_STATE_DAMAGED_REVIEW_REQUIRED")

    @contextmanager
    def transaction(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                try:
                    yield db
                except BaseException:
                    db.rollback()
                    raise
                else:
                    db.commit()
        except sqlite3.Error:
            raise ProviderError("MASSIVE_QUOTA_STORAGE_FAILURE") from None

    def start_pilot(self, identity):
        with self.transaction() as db:
            row = db.execute("SELECT status FROM massive_pilots WHERE id=?", (identity,)).fetchone()
            if row:
                raise ProviderError("MASSIVE_PILOT_ALREADY_STARTED_REVIEW_REQUIRED")
            db.execute("INSERT INTO massive_pilots VALUES(?,0,'ACTIVE')", (identity,))

    def end_pilot(self, identity, *, success=False):
        with self.transaction() as db:
            db.execute(
                "UPDATE massive_pilots SET status=? WHERE id=?",
                ("DONE" if success else "STOPPED", identity),
            )

    def reserve(self, endpoint, *, pilot=None):
        if endpoint not in ("ticker_metadata", "minute_aggregates"):
            raise ProviderError("MASSIVE_ENDPOINT_NOT_ALLOWED")
        with self.transaction() as db:
            now = self.clock()
            state = db.execute("SELECT * FROM massive_state WHERE id=1").fetchone()
            if not math.isfinite(now) or now < state["last_tick"]:
                raise ProviderError("MASSIVE_CLOCK_MOVED_BACKWARDS")
            db.execute("UPDATE massive_state SET last_tick=? WHERE id=1", (now,))
            if state["blocked_until"] is None:
                raise ProviderError("MASSIVE_UNBOUNDED_RETRY_AFTER_REQUIRES_REVIEW")
            if pilot is not None:
                row = db.execute("SELECT * FROM massive_pilots WHERE id=?", (pilot,)).fetchone()
                if row is None or row["status"] != "ACTIVE" or row["used"] >= 4:
                    raise ProviderError("MASSIVE_PILOT_ATTEMPT_BUDGET_EXHAUSTED_OR_STOPPED")
            if db.execute("SELECT 1 FROM massive_attempts WHERE charged IS NULL").fetchone():
                return None, 1.0
            charged = [
                r[0]
                for r in db.execute(
                    "SELECT charged FROM massive_attempts ORDER BY charged DESC LIMIT 4"
                )
            ]
            due = state["blocked_until"]
            if charged:
                due = max(due, charged[0] + 16)
            if len(charged) == 4:
                due = max(due, charged[-1] + 61)
            if now < due:
                return None, min(5.0, due - now)
            token = uuid4().hex
            db.execute(
                "INSERT INTO massive_attempts(token,started,charged,endpoint,status,rows,pilot) "
                "VALUES(?,?,NULL,?,NULL,NULL,?)",
                (token, now, endpoint, pilot),
            )
            if pilot is not None:
                db.execute("UPDATE massive_pilots SET used=used+1 WHERE id=?", (pilot,))
            return token, 0.0

    def acquire(self, endpoint, *, pilot=None, heartbeat=None, max_wait=180):
        waited = 0.0
        while True:
            if heartbeat:
                heartbeat()
            token, delay = self.reserve(endpoint, pilot=pilot)
            if token:
                return token
            if waited + delay > max_wait:
                raise ProviderError("MASSIVE_QUOTA_WAIT_EXCEEDED_OR_UNCERTAIN_OWNER")
            self.sleep(delay)
            waited += delay

    def finish(self, token, *, status=None, rows=None, retry_after=0):
        with self.transaction() as db:
            state = db.execute("SELECT * FROM massive_state WHERE id=1").fetchone()
            row = db.execute("SELECT * FROM massive_attempts WHERE token=?", (token,)).fetchone()
            now = self.clock()
            if (
                row is None
                or row["charged"] is not None
                or not math.isfinite(now)
                or now < max(state["last_tick"], row["started"])
            ):
                raise ProviderError("MASSIVE_ATTEMPT_OWNERSHIP_OR_CLOCK_INVALID")
            db.execute(
                "UPDATE massive_attempts SET charged=?,status=?,rows=? WHERE token=?",
                (now, status, rows, token),
            )
            db.execute(
                "UPDATE massive_state SET last_tick=?,blocked_until=max(blocked_until,?) "
                "WHERE id=1",
                (now, now + retry_after if retry_after is not None else None),
            )

    def audit(self, pilot):
        with self.transaction() as db:
            query = "SELECT endpoint,started,charged,status,rows,recorded_at AS at_utc "
            query += "FROM massive_attempts "
            rows = (
                db.execute(query + "ORDER BY started,token")
                if pilot is None
                else db.execute(query + "WHERE pilot=? ORDER BY started,token", (pilot,))
            )
            return [dict(row) for row in rows]
