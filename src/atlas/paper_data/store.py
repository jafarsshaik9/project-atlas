"""Separate SQLite checkpoints with atomic cursor/results and fenced worker leases."""

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas.config import AtlasError
from atlas.domain import utc
from atlas.market_data.contracts import canonical

SCHEMA_VERSION = 1


def time_text(at):
    return utc(at).isoformat(timespec="microseconds")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value):
        raise AtlasError("A bounded scheduler identifier is required.")


def lease_duration(value):
    if not isinstance(value, timedelta) or not timedelta(0) < value <= timedelta(hours=1):
        raise AtlasError("Worker leases must be positive and at most one hour.")
    return value


class OwnershipLost(AtlasError):
    """The supplied acquisition token cannot mutate this stream."""


@dataclass(frozen=True)
class Lease:
    stream_id: str
    owner: str
    fence: int


class SchedulerStore:
    def __init__(self, path, *, clock=lambda: datetime.now(UTC), read_only=False):
        self.path = Path(path).resolve()
        if self.path.suffix.lower() not in (".sqlite3", ".db"):
            raise AtlasError("Paper checkpoints require a dedicated .sqlite3 or .db file.")
        self.clock = clock
        self.read_only = read_only
        try:
            if read_only:
                with self.connect() as db:
                    self._schema(db)
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Identification and initialization share a lock. Foreign databases are never migrated.
            with self.connect(write=True, initialize=True) as db:
                tables = {
                    r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if tables:
                    self._schema(db)
                else:
                    db.execute("CREATE TABLE paper_meta(version INTEGER NOT NULL)")
                    db.execute("INSERT INTO paper_meta VALUES(?)", (SCHEMA_VERSION,))
                    db.execute("""CREATE TABLE paper_streams(
                        stream_id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                        binding_json TEXT NOT NULL,
                        cursor INTEGER NOT NULL DEFAULT 0, last_event_at TEXT, next_scan_at TEXT,
                        last_scan_at TEXT, last_scan_outcome TEXT,
                        last_clock TEXT, owner TEXT, fence INTEGER NOT NULL DEFAULT 0,
                        lease_until TEXT, status TEXT NOT NULL DEFAULT 'IDLE',
                        halted INTEGER NOT NULL DEFAULT 0, connected INTEGER NOT NULL DEFAULT 1,
                        provider_epoch TEXT NOT NULL DEFAULT 'mock-process-1')""")
                    db.execute("""CREATE TABLE paper_attempts(
                        stream_id TEXT NOT NULL REFERENCES paper_streams, owner TEXT NOT NULL,
                        fence INTEGER NOT NULL, PRIMARY KEY(stream_id, owner))""")
                    db.execute("""CREATE TABLE paper_results(
                        stream_id TEXT NOT NULL REFERENCES paper_streams, ordinal INTEGER NOT NULL,
                        at TEXT NOT NULL, outcome TEXT NOT NULL, content TEXT NOT NULL,
                        PRIMARY KEY(stream_id, ordinal))""")
                    db.execute("""CREATE TABLE paper_seen(
                        stream_id TEXT NOT NULL REFERENCES paper_streams, identity TEXT NOT NULL,
                        checksum TEXT NOT NULL, PRIMARY KEY(stream_id, identity))""")
            with self.connect(write=True) as db:
                # DELETE journaling + FULL synchronous gives atomic crash recovery without
                # auxiliary WAL files; suitable for a single-host, bounded offline scanner.
                if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                    raise AtlasError("Paper checkpoint journal mode must be DELETE.")
        except OSError:
            raise AtlasError("Paper checkpoint path is unavailable.") from None

    @staticmethod
    def _schema(db):
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"paper_meta", "paper_streams", "paper_attempts", "paper_results", "paper_seen"}
        if not required <= tables:
            raise AtlasError("Not an Atlas paper checkpoint database; migration refused.")
        rows = db.execute("SELECT version FROM paper_meta").fetchall()
        if len(rows) != 1 or rows[0][0] != SCHEMA_VERSION:
            raise AtlasError("Unsupported paper checkpoint schema; migration refused.")

    @contextmanager
    def connect(self, *, write=False, initialize=False):
        if write and self.read_only:
            raise AtlasError("Paper diagnostics opened the checkpoint read-only.")
        db = None
        try:
            mode = "rwc" if initialize else "rw" if write else "ro"
            db = sqlite3.connect(
                self.path.as_uri() + f"?mode={mode}", uri=True, timeout=5, isolation_level=None
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            if write:
                db.commit()
        except sqlite3.Error:
            raise AtlasError(
                "Paper checkpoint transaction failed; no payload is disclosed."
            ) from None
        finally:
            if db is not None:
                # Also rolls back non-SQL exceptions, including lease failures.
                db.close()

    def now(self, row=None):
        now = utc(self.clock())  # Sample only after BEGIN IMMEDIATE for ownership mutations.
        if (
            row is not None
            and row["last_clock"]
            and now < datetime.fromisoformat(row["last_clock"])
        ):
            raise AtlasError("Scheduler clock regressed; scan and ownership changes refused.")
        return now

    def register(self, stream_id, binding, content):
        identifier(stream_id)
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT binding FROM paper_streams WHERE stream_id=?", (stream_id,)
            ).fetchone()
            if row is not None and row[0] != binding:
                raise AtlasError("Checkpoint configuration/calendar/mock script mismatch.")
            db.execute(
                "INSERT OR IGNORE INTO paper_streams(stream_id,binding,binding_json) VALUES(?,?,?)",
                (stream_id, binding, canonical(content)),
            )

    def claim(self, stream_id, owner, ttl=timedelta(seconds=30)):
        identifier(owner)
        lease_duration(ttl)
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT * FROM paper_streams WHERE stream_id=?", (stream_id,)
            ).fetchone()
            if row is None:
                raise AtlasError("Register the paper stream before claiming it.")
            now = self.now(row)
            if db.execute(
                "SELECT 1 FROM paper_attempts WHERE stream_id=? AND owner=?", (stream_id, owner)
            ).fetchone():
                raise AtlasError(
                    "Worker attempt identity already used; use a new attempt identifier."
                )
            if row["owner"] and datetime.fromisoformat(row["lease_until"]) > now:
                return None
            fence = row["fence"] + 1
            db.execute("INSERT INTO paper_attempts VALUES(?,?,?)", (stream_id, owner, fence))
            db.execute(
                """UPDATE paper_streams SET owner=?,fence=?,lease_until=?,last_clock=?,status='IDLE'
                WHERE stream_id=?""",
                (owner, fence, time_text(now + ttl), time_text(now), stream_id),
            )
            return Lease(stream_id, owner, fence)

    def owned(self, db, lease):
        row = db.execute(
            "SELECT * FROM paper_streams WHERE stream_id=?", (lease.stream_id,)
        ).fetchone()
        if row is None:
            raise OwnershipLost("Paper worker lease lost.")
        now = self.now(row)
        if (
            row["owner"] != lease.owner
            or row["fence"] != lease.fence
            or row["lease_until"] is None
            or datetime.fromisoformat(row["lease_until"]) <= now
        ):
            raise OwnershipLost("Paper worker lease lost or expired.")
        return row, now

    def heartbeat(self, lease, ttl=timedelta(seconds=30)):
        lease_duration(ttl)
        with self.connect(write=True) as db:
            _, now = self.owned(db, lease)
            db.execute(
                "UPDATE paper_streams SET lease_until=?,last_clock=? WHERE stream_id=?",
                (time_text(now + ttl), time_text(now), lease.stream_id),
            )

    def shutdown(self, lease):
        with self.connect(write=True) as db:
            _, now = self.owned(db, lease)
            db.execute(
                """UPDATE paper_streams SET status='STOPPED', owner=NULL, lease_until=NULL,
                last_clock=? WHERE stream_id=?""",
                (time_text(now), lease.stream_id),
            )

    def snapshot(self, stream_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise AtlasError("Diagnostic result limits must be between 1 and 1000.")
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM paper_streams WHERE stream_id=?", (stream_id,)
            ).fetchone()
            if row is None:
                raise AtlasError("Unknown paper stream.")
            result = dict(row)
            result["binding_json"] = json.loads(result["binding_json"])
            now = self.now()
            result["owner_active"] = bool(
                row["owner"]
                and row["lease_until"]
                and datetime.fromisoformat(row["lease_until"]) > now
                and (not row["last_clock"] or now >= datetime.fromisoformat(row["last_clock"]))
            )
            result["results"] = [
                json.loads(r[0])
                for r in db.execute(
                    """SELECT content FROM paper_results WHERE stream_id=?
                    ORDER BY ordinal DESC LIMIT ?""",
                    (stream_id, limit),
                )
            ]
            result["counts"] = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT outcome,count(*) FROM paper_results WHERE stream_id=? GROUP BY outcome",
                    (stream_id,),
                )
            }
            return result
