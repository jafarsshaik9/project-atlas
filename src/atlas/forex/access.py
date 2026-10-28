"""Scoped local approval and persistent conservative OANDA HTTP attempt budgets."""

import hashlib
import hmac
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from atlas.config import AtlasError
from atlas.market_data.contracts import canonical


@dataclass(frozen=True)
class Permit:
    identity: str
    environment: str
    scope: str
    maximum_attempts: int
    expires: float


def phrase(environment, scope):
    if scope not in ("accounts", "accounts-instruments"):
        raise AtlasError("Phase 6A approval permits discovery only.")
    return f"APPROVE OANDA {environment.upper()} READ ONLY {scope.upper()}"


def signature(config, record):
    text = canonical(record) + "|" + config.account_id
    return hmac.new(config.token.encode(), text.encode(), hashlib.sha256).hexdigest()


def approval_record(config, scope, confirmation, *, now=None):
    now = time.time() if now is None else now
    if confirmation != phrase(config.environment, scope):
        raise AtlasError("Explicit confirmation did not match; approval not recorded.")
    record = {
        "schema": "oanda-read-only-approval-v1",
        "identity": uuid.uuid4().hex,
        "environment": config.environment,
        "scope": scope,
        "maximum_attempts": 1 if scope == "accounts" else 2,
        "created": now,
        "expires": now + 86400,
    }
    return record | {"signature": signature(config, record)}


def validate_approval(config, record, *, now=None):
    now = time.time() if now is None else now
    try:
        content = dict(record)
        signed = content.pop("signature")
        scope = content["scope"]
        phrase(config.environment, scope)
        if (
            set(content)
            != {
                "schema",
                "identity",
                "environment",
                "scope",
                "maximum_attempts",
                "created",
                "expires",
            }
            or content["schema"] != "oanda-read-only-approval-v1"
            or content["environment"] != config.environment
            or not content["created"] <= now < content["expires"] <= content["created"] + 86400
            or content["maximum_attempts"] != (1 if scope == "accounts" else 2)
            or not hmac.compare_digest(signed, signature(config, content))
        ):
            raise ValueError
        return Permit(
            content["identity"],
            config.environment,
            scope,
            content["maximum_attempts"],
            content["expires"],
        )
    except (ValueError, KeyError, TypeError, AtlasError):
        raise AtlasError("OANDA_APPROVAL_INVALID_OR_EXPIRED") from None


class Quota:
    """One local OANDA worker, <=60 attempts/61 seconds, <=1 new connection/sec.

    An uncertain pending request blocks restart for explicit review. Neither a
    crash nor rerunning an approval file resets its lifetime attempt budget.
    """

    def __init__(self, path, *, clock=time.time, sleep=time.sleep):
        self.path, self.clock, self.sleep = Path(path), clock, sleep
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {"oanda_state", "oanda_attempts"}:
                raise AtlasError("Foreign OANDA quota database; migration refused.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS oanda_state(id INTEGER PRIMARY KEY CHECK(id=1),
                    last REAL NOT NULL, blocked REAL NOT NULL);
                INSERT OR IGNORE INTO oanda_state VALUES(1,0,0);
                CREATE TABLE IF NOT EXISTS oanda_attempts(id TEXT PRIMARY KEY, permit TEXT NOT NULL,
                    started REAL NOT NULL, finished REAL, status INTEGER);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def acquire(self, permit):
        while True:
            now = self.clock()
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise AtlasError("OANDA_QUOTA_CORRUPT")
                last, blocked = db.execute(
                    "SELECT last,blocked FROM oanda_state WHERE id=1"
                ).fetchone()
                if not all(math.isfinite(x) and x >= 0 for x in (now, last, blocked)):
                    raise AtlasError("OANDA_QUOTA_CLOCK_INVALID")
                latest = db.execute(
                    "SELECT max(coalesce(finished,started)) FROM oanda_attempts"
                ).fetchone()[0]
                if latest is not None and latest != last:
                    raise AtlasError("OANDA_QUOTA_HISTORY_INCONSISTENT")
                if now < last or now >= permit.expires:
                    raise AtlasError("OANDA_CLOCK_OR_APPROVAL_EXPIRED")
                if db.execute(
                    "SELECT count(*) FROM oanda_attempts WHERE finished IS NULL"
                ).fetchone()[0]:
                    raise AtlasError("OANDA_UNCERTAIN_REQUEST_REQUIRES_REVIEW")
                used = db.execute(
                    "SELECT count(*) FROM oanda_attempts WHERE permit=?", (permit.identity,)
                ).fetchone()[0]
                if used >= permit.maximum_attempts:
                    raise AtlasError("OANDA_APPROVAL_ATTEMPT_CAP_EXHAUSTED")
                recent = db.execute(
                    "SELECT started FROM oanda_attempts WHERE started>? ORDER BY started",
                    (now - 61,),
                ).fetchall()
                wait = max(0, blocked - now, last + 1 - now)
                if len(recent) >= 60:
                    wait = max(wait, recent[-60][0] + 61 - now)
                if wait > 30:
                    raise AtlasError("OANDA_COOLDOWN_PERSISTED_RESUME_LATER")
                if not wait:
                    identity = uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO oanda_attempts VALUES(?,?,?,NULL,NULL)",
                        (identity, permit.identity, now),
                    )
                    db.execute("UPDATE oanda_state SET last=? WHERE id=1", (now,))
                    return identity
            self.sleep(wait)

    def finish(self, identity, status, backoff=0):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE oanda_attempts SET finished=?,status=? "
                "WHERE id=? AND finished IS NULL AND started<=?",
                (now, status, identity, now),
            ).rowcount
            if changed != 1:
                raise AtlasError("OANDA_QUOTA_COMPLETION_INVALID")
            db.execute(
                "UPDATE oanda_state SET last=?,blocked=max(blocked,?) WHERE id=1",
                (now, now + backoff),
            )

    def count(self, permit):
        with self.connect() as db:
            return db.execute(
                "SELECT count(*) FROM oanda_attempts WHERE permit=?", (permit.identity,)
            ).fetchone()[0]


def utc_now():
    return datetime.now(UTC)
