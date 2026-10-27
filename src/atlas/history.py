"""Transactional SQLite activity history, run leases and notification outbox."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from atlas.activity import ActivityEvent, Environment, EventType, canonical, now_utc
from atlas.config import AtlasError

SCHEMA_VERSION = 3


def public_json(value):
    """Reject credential-shaped fields at the persistence boundary."""

    def check(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if any(
                    word in key.lower()
                    for word in (
                        "password",
                        "token",
                        "secret",
                        "api_key",
                        "chat_id",
                        "account_id",
                        "authorization",
                        "broker_payload",
                        "headers",
                    )
                ):
                    raise AtlasError("Private configuration is not research history.")
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)
        elif isinstance(item, str) and re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}", item):
            raise AtlasError("Private configuration is not research history.")

    check(value)
    return canonical(value)


def history_path() -> Path:
    default = Path(__file__).resolve().parents[2] / "local" / "atlas-history.sqlite3"
    return Path(os.environ.get("ATLAS_HISTORY_DB", str(default))).resolve()


class HistoryStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else history_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.migrate()
        except (OSError, sqlite3.Error):
            raise AtlasError(
                "History database is unavailable; check local path and permissions."
            ) from None

    @contextmanager
    def connect(self, *, write=False, snapshot=False):
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        except sqlite3.Error:
            raise AtlasError(
                "History database is unavailable; no credentials are logged."
            ) from None
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            elif snapshot:
                connection.execute("BEGIN")
            yield connection
            if write:
                connection.commit()
        except sqlite3.Error:
            if write:
                connection.rollback()
            raise AtlasError("History database operation failed; no secrets are logged.") from None
        finally:
            connection.close()

    def migrate(self):
        # DDL is transactional; never executescript() across a transaction boundary.
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
        statements = [
            """CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, lease_until TEXT, owner TEXT,
                metadata TEXT NOT NULL, report TEXT, error_code TEXT)""",
            """CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id), event_type TEXT NOT NULL,
                event_at TEXT NOT NULL, processed_at TEXT NOT NULL,
                causation_id TEXT REFERENCES events(event_id), content TEXT NOT NULL)""",
            "CREATE INDEX event_run_type ON events(run_id,event_type,sequence)",
            "CREATE INDEX event_processing ON events(processed_at,sequence)",
            """CREATE TABLE outbox (
                event_id TEXT PRIMARY KEY REFERENCES events(event_id),
                state TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
                next_at TEXT NOT NULL, lease_until TEXT, owner TEXT, last_code TEXT)""",
            "CREATE TABLE delivery_gate "
            "(id INTEGER PRIMARY KEY CHECK(id=1), next_at TEXT NOT NULL)",
            """CREATE TABLE auth (
                id INTEGER PRIMARY KEY CHECK(id=1), salt TEXT NOT NULL, digest TEXT NOT NULL)""",
            """CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL)""",
            """CREATE TABLE login_limits (
                key TEXT PRIMARY KEY, failures INTEGER NOT NULL, until_at TEXT NOT NULL)""",
        ]
        with self.connect(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise AtlasError(
                    "History schema is newer than this application; migration stopped."
                )
            if version == 0:
                for statement in statements:
                    db.execute(statement)
                db.execute("PRAGMA user_version=1")
            if version < 2:
                db.execute("""CREATE TABLE notification_configuration (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), seen_at TEXT NOT NULL)""")
                db.execute("PRAGMA user_version=2")
            if version < 3:
                # Attempt identities cannot be reused after expiry/restart (the ABA case).
                db.execute("""CREATE TABLE IF NOT EXISTS run_attempts (
                    run_id TEXT NOT NULL REFERENCES runs(run_id), owner TEXT NOT NULL,
                    claimed_at TEXT NOT NULL, PRIMARY KEY(run_id,owner))""")
                db.execute("""INSERT OR IGNORE INTO run_attempts
                    SELECT run_id,owner,updated_at FROM runs WHERE owner IS NOT NULL""")
                db.execute("PRAGMA user_version=3")

    def claim_run(self, run_id, metadata, owner, now=None) -> bool:
        if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner):
            raise AtlasError("A fresh research attempt identity is required.")
        metadata_json = public_json(metadata)
        with self.connect(write=True) as db:
            now = now or now_utc()
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row and row["status"] == "COMPLETED":
                return False
            if row and row["status"] == "RUNNING" and row["lease_until"] > now.isoformat():
                raise AtlasError("This research run is already active.")
            if db.execute(
                "SELECT 1 FROM run_attempts WHERE run_id=? AND owner=?", (run_id, owner)
            ).fetchone():
                raise AtlasError(
                    "Research attempt identity was already used; use a fresh identity."
                )
            db.execute(
                """INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET status='RUNNING',updated_at=excluded.updated_at,
                lease_until=excluded.lease_until,owner=excluded.owner,error_code=NULL""",
                (
                    run_id,
                    "RUNNING",
                    now.isoformat(),
                    now.isoformat(),
                    (now + timedelta(seconds=30)).isoformat(),
                    owner,
                    metadata_json,
                    None,
                    None,
                ),
            )
            db.execute("INSERT INTO run_attempts VALUES (?,?,?)", (run_id, owner, now.isoformat()))
        return True

    def heartbeat(self, run_id, owner):
        with self.connect(write=True) as db:
            now = now_utc()
            changed = db.execute(
                "UPDATE runs SET updated_at=?,lease_until=? "
                "WHERE run_id=? AND owner=? AND status='RUNNING' AND lease_until>?",
                (
                    now.isoformat(),
                    (now + timedelta(seconds=30)).isoformat(),
                    run_id,
                    owner,
                    now.isoformat(),
                ),
            ).rowcount
            if not changed:
                raise AtlasError("Research lease lost; history commit is disabled.")

    @staticmethod
    def _owns_run(db, run_id, owner, now):
        """Only call inside BEGIN IMMEDIATE: ownership and writes share one fence."""
        return (
            db.execute(
                "SELECT 1 FROM runs WHERE run_id=? AND owner=? "
                "AND status='RUNNING' AND lease_until>?",
                (run_id, owner, now.isoformat()),
            ).fetchone()
            is not None
        )

    @staticmethod
    def append(db, event: ActivityEvent) -> bool:
        # Revalidate: frozen models do not make nested dictionaries immutable.
        event = ActivityEvent.model_validate(event.model_dump())
        if event.environment != Environment.SYNTHETIC:
            raise AtlasError("Only SYNTHETIC activity is supported in this phase.")
        existing = db.execute(
            "SELECT content FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        if existing:
            old = json.loads(existing[0])
            new = event.model_dump(mode="json")
            old.pop("processed_at")
            new.pop("processed_at")
            if old != new:
                raise AtlasError("Event identity conflict; history was not overwritten.")
            return False
        if event.causation_id:
            parent = db.execute(
                "SELECT run_id FROM events WHERE event_id=?", (event.causation_id,)
            ).fetchone()
            if parent is None or parent[0] != event.run_id:
                raise AtlasError("Missing or cross-run event cause.")
        db.execute(
            """INSERT INTO events
            (event_id,run_id,event_type,event_at,processed_at,causation_id,content)
            VALUES (?,?,?,?,?,?,?)""",
            (
                event.event_id,
                event.run_id,
                event.event_type.value,
                event.event_at.isoformat(),
                event.processed_at.isoformat(),
                event.causation_id,
                event.model_dump_json(),
            ),
        )
        return True

    def complete_run(self, run_id, owner, events, report, *, notify=False, notify_summary=False):
        from atlas.notifications import NOTIFIABLE

        report_json = public_json(report)
        with self.connect(write=True) as db:
            now = now_utc()
            if not self._owns_run(db, run_id, owner, now):
                raise AtlasError("Research lease lost; history commit is disabled.")
            terminal_count = 0
            for event in events:
                if event.run_id != run_id:
                    raise AtlasError("Research batch contains a cross-run event.")
                if event.event_type == EventType.RUN_COMPLETED:
                    terminal_count += 1
                if terminal_count > 1 or (
                    event.event_type == EventType.ERROR
                    and event.payload.get("code") == "RESEARCH_FAILED"
                ):
                    raise AtlasError("Research completion requires one consistent terminal event.")
                self.append(db, event)
                if notify and (
                    event.event_type in NOTIFIABLE
                    or (notify_summary and event.event_type == EventType.RUN_COMPLETED)
                ):
                    db.execute(
                        "INSERT OR IGNORE INTO outbox(event_id,next_at) VALUES (?,?)",
                        (event.event_id, now.isoformat()),
                    )
            if terminal_count != 1:
                raise AtlasError("Research completion requires one consistent terminal event.")
            db.execute(
                """UPDATE runs SET status='COMPLETED',updated_at=?,lease_until=NULL,
                owner=NULL,report=? WHERE run_id=?""",
                (now.isoformat(), report_json, run_id),
            )

    def fail_run(self, run_id, owner, at, *, notify=False) -> bool:
        """Record a current owner's failure; obsolete/repeated callbacks have no effects."""
        with self.connect(write=True) as db:
            now = now_utc()
            if not self._owns_run(db, run_id, owner, now):
                return False
            event = ActivityEvent.create(
                run_id=run_id,
                event_type=EventType.ERROR,
                ordinal="failure-" + owner,
                at=at,
                payload={"code": "RESEARCH_FAILED"},
            )
            self.append(db, event)
            if notify:
                db.execute(
                    "INSERT OR IGNORE INTO outbox(event_id,next_at) VALUES (?,?)",
                    (event.event_id, now.isoformat()),
                )
            db.execute(
                """UPDATE runs SET status='ERROR',error_code='RESEARCH_FAILED',updated_at=?,
                lease_until=NULL,owner=NULL WHERE run_id=? AND owner=?""",
                (now.isoformat(), run_id, owner),
            )
        return True

    def run(self, run_id=None):
        with self.connect() as db:
            if run_id:
                row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC,run_id LIMIT 1"
                ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        result["report"] = json.loads(result["report"]) if result["report"] else None
        if result["status"] == "RUNNING" and result["lease_until"] <= now_utc().isoformat():
            result["status"] = "INTERRUPTED"
        return result

    def runs(self, limit=100, offset=0):
        with self.connect(snapshot=True) as db:
            rows = db.execute(
                "SELECT run_id FROM runs ORDER BY created_at DESC,run_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            count = db.execute("SELECT count(*) FROM runs").fetchone()[0]
        return [self.run(row[0]) for row in rows], count

    def events(self, run_id=None, *, kinds=(), limit=50, offset=0, descending=True):
        if not 1 <= limit <= 1000 or not 0 <= offset <= 1_000_000:
            raise AtlasError("History query is outside bounded limits.")
        where, params = [], []
        if run_id:
            where.append("run_id=?")
            params.append(run_id)
        if kinds:
            where.append("event_type IN (" + ",".join("?" for _ in kinds) + ")")
            params.extend(str(kind) for kind in kinds)
        clause = " WHERE " + " AND ".join(where) if where else ""
        order = "DESC" if descending else "ASC"
        with self.connect(snapshot=True) as db:
            count = db.execute("SELECT count(*) FROM events" + clause, params).fetchone()[0]
            rows = db.execute(
                "SELECT content FROM events"
                + clause
                + f" ORDER BY sequence {order} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [json.loads(row[0]) for row in rows], count

    def backup(self, destination: Path):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise AtlasError("Backup requires a new destination distinct from the database.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)

    def notification_status(self):
        with self.connect() as db:
            configuration = db.execute(
                "SELECT enabled,seen_at FROM notification_configuration WHERE id=1"
            ).fetchone()
            counts = {
                row[0]: row[1]
                for row in db.execute("SELECT state,count(*) FROM outbox GROUP BY state")
            }
            last = db.execute(
                "SELECT state,attempts,last_code FROM outbox ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return {
            "counts": counts,
            "last_delivery": dict(last) if last else None,
            "last_sender_configuration": dict(configuration) if configuration else None,
        }

    def record_notification_configuration(self, enabled):
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO notification_configuration VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,seen_at=excluded.seen_at",
                (int(enabled), now_utc().isoformat()),
            )

    def claim_notification(self, owner, now=None, *, event_id=None):
        now = now or now_utc()
        with self.connect(write=True) as db:
            exhausted = db.execute(
                """SELECT event_id,attempts FROM outbox WHERE state='SENDING'
                AND attempts>=5 AND lease_until<=? LIMIT 100""",
                (now.isoformat(),),
            ).fetchall()
            for old in exhausted:
                source = ActivityEvent.model_validate_json(
                    db.execute(
                        "SELECT content FROM events WHERE event_id=?", (old["event_id"],)
                    ).fetchone()[0]
                )
                db.execute(
                    """UPDATE outbox SET state='FAILED',owner=NULL,lease_until=NULL,
                    last_code='FINAL_OUTCOME_UNCERTAIN' WHERE event_id=?""",
                    (old["event_id"],),
                )
                self.delivery_event(
                    db,
                    source,
                    old["attempts"],
                    EventType.DELIVERY_FAILED,
                    "FINAL_OUTCOME_UNCERTAIN",
                )
            gate = db.execute("SELECT next_at FROM delivery_gate WHERE id=1").fetchone()
            if gate and gate[0] > now.isoformat():
                return None
            selection = " AND event_id=?" if event_id else ""
            parameters = [now.isoformat(), now.isoformat()]
            if event_id:
                parameters.append(event_id)
            row = db.execute(
                """SELECT * FROM outbox WHERE attempts<5 AND
                ((state IN ('PENDING','RETRY') AND next_at<=?) OR
                 (state='SENDING' AND lease_until<=?))"""
                + selection
                + " ORDER BY rowid LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE outbox SET state='SENDING',attempts=attempts+1,owner=?,lease_until=?
                WHERE event_id=?""",
                (owner, (now + timedelta(seconds=30)).isoformat(), row["event_id"]),
            )
            db.execute(
                "INSERT INTO delivery_gate VALUES(1,?) ON CONFLICT(id) "
                "DO UPDATE SET next_at=excluded.next_at",
                ((now + timedelta(seconds=1)).isoformat(),),
            )
            event = ActivityEvent.model_validate_json(
                db.execute(
                    "SELECT content FROM events WHERE event_id=?", (row["event_id"],)
                ).fetchone()[0]
            )
            self.delivery_event(
                db, event, row["attempts"] + 1, EventType.DELIVERY_ATTEMPT, "ATTEMPT"
            )
            return event, row["attempts"] + 1

    @staticmethod
    def delivery_event(db, event, attempt, kind, code):
        audit = ActivityEvent.create(
            run_id=event.run_id,
            event_type=kind,
            ordinal=f"{event.event_id}:{attempt}",
            at=now_utc(),
            cause=event.event_id,
            payload={"source_event_id": event.event_id, "attempt": attempt, "code": code},
        )
        HistoryStore.append(db, audit)

    def finish_notification(self, event, owner, attempt, *, code, retry=False, delay=0, now=None):
        now = now or now_utc()
        state = "SENT" if code == "DELIVERED" else "RETRY" if retry and attempt < 5 else "FAILED"
        with self.connect(write=True) as db:
            changed = db.execute(
                """UPDATE outbox SET state=?,next_at=?,lease_until=NULL,owner=NULL,last_code=?
                WHERE event_id=? AND owner=? AND state='SENDING' AND attempts=?""",
                (
                    state,
                    (now + timedelta(seconds=delay)).isoformat(),
                    code,
                    event.event_id,
                    owner,
                    attempt,
                ),
            ).rowcount
            if not changed:
                raise AtlasError("Notification lease lost; delivery result was not overwritten.")
            kind = EventType.DELIVERY_OK if state == "SENT" else EventType.DELIVERY_FAILED
            self.delivery_event(db, event, attempt, kind, code)


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
