"""Private recipient configuration, separate from public research/event payloads.

The owner is the local workspace administrator, authenticated with DashboardAuth.
No bot token is persisted. IDs live only in this private, Git-ignored database.
"""

import hashlib
import re
from datetime import datetime, timedelta
from uuid import uuid4

from atlas.activity import now_utc
from atlas.config import AtlasError
from atlas.history import secret_hash

RECIPIENT_SCHEMA = 1
ALERTS = frozenset({"trades", "risk", "system", "summary"})


def bot_reference(token):
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token):
        raise AtlasError("Invalid Telegram configuration; enter it locally.")
    # Public bot identity is stable when BotFather rotates the secret suffix.
    return hashlib.sha256(token.split(":", 1)[0].encode()).hexdigest()


def recipient_mode(store):
    """Read-only detection; ordinary research/dashboard access creates no registry."""
    with store.connect() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_config'"
        ).fetchone():
            return False
        return bool(db.execute("SELECT multi FROM telegram_config WHERE id=1").fetchone()[0])


class Recipients:
    def __init__(self, store):
        self.store = store
        with store.connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_config (
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                multi INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 1,
                bot_reference TEXT, activation_sequence INTEGER,
                update_offset INTEGER NOT NULL DEFAULT 0)""")
            db.execute(
                "INSERT OR IGNORE INTO telegram_config(id,version) VALUES(1,?)", (RECIPIENT_SCHEMA,)
            )
            if db.execute("SELECT version FROM telegram_config").fetchone()[0] != RECIPIENT_SCHEMA:
                raise AtlasError("Unsupported Telegram recipient schema; operation stopped.")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_recipients (
                recipient_id TEXT PRIMARY KEY, alias TEXT UNIQUE NOT NULL, chat_id TEXT UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('PENDING','APPROVED','REMOVED')),
                enabled INTEGER NOT NULL DEFAULT 0, synthetic INTEGER NOT NULL DEFAULT 0,
                cards INTEGER NOT NULL DEFAULT 0, trades INTEGER NOT NULL DEFAULT 1,
                risk INTEGER NOT NULL DEFAULT 1, system INTEGER NOT NULL DEFAULT 1,
                summary INTEGER NOT NULL DEFAULT 0, verified_at TEXT NOT NULL,
                approved_at TEXT, start_sequence INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_batches (
                event_id TEXT PRIMARY KEY REFERENCES events(event_id), created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_deliveries (
                delivery_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id),
                recipient_id TEXT NOT NULL REFERENCES telegram_recipients(recipient_id),
                category TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL,
                lease_until TEXT, owner TEXT, last_code TEXT, media_state TEXT,
                UNIQUE(event_id,recipient_id))""")
            db.execute("""CREATE INDEX IF NOT EXISTS telegram_due
                ON telegram_deliveries(state,next_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_delivery_audit (
                audit_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES telegram_deliveries,
                attempt INTEGER NOT NULL, at TEXT NOT NULL,
                phase TEXT NOT NULL, code TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS telegram_admin_audit (
                sequence INTEGER PRIMARY KEY, at TEXT NOT NULL,
                recipient_id TEXT, action TEXT NOT NULL)""")

    @staticmethod
    def _admin(db, session):
        if (
            not session
            or not db.execute(
                "SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?",
                (secret_hash(session), now_utc().isoformat()),
            ).fetchone()
        ):
            raise AtlasError("Owner authentication required for recipient administration.")

    @staticmethod
    def _audit(db, action, recipient_id=None):
        db.execute(
            "INSERT INTO telegram_admin_audit(at,recipient_id,action) VALUES(?,?,?)",
            (now_utc().isoformat(), recipient_id, action),
        )

    @staticmethod
    def _sequence(db):
        return db.execute("SELECT coalesce(max(sequence),0) FROM events").fetchone()[0]

    @staticmethod
    def _row(db, alias):
        row = db.execute(
            "SELECT * FROM telegram_recipients WHERE alias=? AND state!='REMOVED'", (alias,)
        ).fetchone()
        if row is None:
            raise AtlasError("Recipient alias was not found.")
        return row

    @staticmethod
    def _cancel(db, recipient_id):
        db.execute(
            """UPDATE telegram_deliveries SET state='CANCELLED',owner=NULL,lease_until=NULL,
            last_code='SUBSCRIPTION_CHANGED' WHERE recipient_id=?
            AND state IN ('PENDING','RETRY','SENDING')""",
            (recipient_id,),
        )

    def register_verified(self, alias, chat_id, reference, session):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", alias or ""):
            raise AtlasError(
                "Use a recipient alias of 1–40 letters, digits, underscores or hyphens."
            )
        if (
            not isinstance(chat_id, str)
            or not re.fullmatch(r"[1-9]\d{0,19}", chat_id)
            or not re.fullmatch(r"[a-f0-9]{64}", reference or "")
        ):
            raise AtlasError("A verified private chat and valid bot identity are required.")
        with self.store.connect(write=True) as db:
            self._admin(db, session)
            bound = db.execute("SELECT bot_reference FROM telegram_config").fetchone()[0]
            if bound and bound != reference:
                raise AtlasError("This registry belongs to another bot; no recipient was changed.")
            if db.execute(
                "SELECT 1 FROM telegram_recipients WHERE alias=? OR chat_id=?", (alias, chat_id)
            ).fetchone():
                raise AtlasError("Recipient alias or verified chat is already registered.")
            if (
                db.execute(
                    "SELECT count(*) FROM telegram_recipients WHERE state!='REMOVED'"
                ).fetchone()[0]
                >= 50
            ):
                raise AtlasError("The local recipient limit of 50 was reached.")
            recipient_id = uuid4().hex
            db.execute("UPDATE telegram_config SET bot_reference=? WHERE id=1", (reference,))
            db.execute(
                """INSERT INTO telegram_recipients
                (recipient_id,alias,chat_id,state,verified_at) VALUES(?,?,?,'PENDING',?)""",
                (recipient_id, alias, chat_id, now_utc().isoformat()),
            )
            self._audit(db, "VERIFIED_PENDING", recipient_id)
        return recipient_id

    def approve(self, alias, session, *, synthetic=False, cards=False, alerts=ALERTS - {"summary"}):
        if not set(alerts) <= ALERTS or type(synthetic) is not bool or type(cards) is not bool:
            raise AtlasError("Invalid recipient subscription preferences.")
        with self.store.connect(write=True) as db:
            self._admin(db, session)
            row = self._row(db, alias)
            if row["state"] != "PENDING":
                raise AtlasError("Use preferences to explicitly change an approved recipient.")
            if row["verified_at"] < (now_utc() - timedelta(hours=24)).isoformat():
                raise AtlasError("Verification expired; remove and verify the recipient again.")
            db.execute(
                """UPDATE telegram_recipients SET state='APPROVED',enabled=1,
                approved_at=?,synthetic=?,cards=?,trades=?,risk=?,system=?,summary=?,start_sequence=?
                WHERE recipient_id=?""",
                (
                    now_utc().isoformat(),
                    int(synthetic),
                    int(cards),
                    *(int(kind in alerts) for kind in ("trades", "risk", "system", "summary")),
                    self._sequence(db),
                    row["recipient_id"],
                ),
            )
            self._audit(db, "OWNER_APPROVED", row["recipient_id"])

    def preferences(self, alias, session, *, synthetic, cards, alerts):
        if not set(alerts) <= ALERTS or type(synthetic) is not bool or type(cards) is not bool:
            raise AtlasError("Invalid recipient subscription preferences.")
        with self.store.connect(write=True) as db:
            self._admin(db, session)
            row = self._row(db, alias)
            if row["state"] != "APPROVED":
                raise AtlasError("Approve the verified recipient before setting preferences.")
            self._cancel(db, row["recipient_id"])
            db.execute(
                """UPDATE telegram_recipients SET enabled=1,synthetic=?,cards=?,
                trades=?,risk=?,system=?,summary=?,start_sequence=?,approved_at=?
                WHERE recipient_id=?""",
                (
                    int(synthetic),
                    int(cards),
                    *(int(kind in alerts) for kind in ("trades", "risk", "system", "summary")),
                    self._sequence(db),
                    now_utc().isoformat(),
                    row["recipient_id"],
                ),
            )
            self._audit(db, "OWNER_PREFERENCES", row["recipient_id"])

    def disable(self, alias, session, *, remove=False):
        with self.store.connect(write=True) as db:
            self._admin(db, session)
            row = self._row(db, alias)
            self._cancel(db, row["recipient_id"])
            if remove:
                db.execute(
                    """UPDATE telegram_recipients SET enabled=0,chat_id=NULL,state='REMOVED',
                    alias=? WHERE recipient_id=?""",
                    ("removed-" + row["recipient_id"], row["recipient_id"]),
                )
            else:
                db.execute(
                    "UPDATE telegram_recipients SET enabled=0 WHERE recipient_id=?",
                    (row["recipient_id"],),
                )
            self._audit(db, "REMOVED" if remove else "DISABLED", row["recipient_id"])

    def mode(self, session, *, paused=False):
        with self.store.connect(write=True) as db:
            self._admin(db, session)
            row = db.execute("SELECT * FROM telegram_config").fetchone()
            if not row["bot_reference"]:
                raise AtlasError("Verify and approve recipients before enabling recipient mode.")
            if (
                not paused
                and not db.execute(
                    "SELECT 1 FROM telegram_recipients WHERE state='APPROVED' AND enabled=1"
                ).fetchone()
            ):
                raise AtlasError("At least one explicitly approved recipient is required.")
            db.execute(
                """UPDATE telegram_config SET multi=1,paused=?,
                activation_sequence=coalesce(activation_sequence,?) WHERE id=1""",
                (int(paused), self._sequence(db)),
            )
            self._audit(db, "PAUSED" if paused else "MULTI_ENABLED")

    def listing(self, session):
        with self.store.connect(snapshot=True) as db:
            self._admin(db, session)
            return [
                dict(row)
                for row in db.execute("""SELECT alias,recipient_id,state,enabled,
                synthetic,cards,trades,risk,system,summary FROM telegram_recipients
                WHERE state!='REMOVED' ORDER BY alias LIMIT 50""")
            ]

    def delivery_listing(self, session):
        with self.store.connect(snapshot=True) as db:
            self._admin(db, session)
            return [
                dict(row)
                for row in db.execute("""SELECT r.alias,d.delivery_id,d.event_id,
                d.state,d.attempts,d.last_code FROM telegram_deliveries d
                JOIN telegram_recipients r USING(recipient_id) ORDER BY d.rowid DESC LIMIT 100""")
            ]

    def validate_bot(self, token):
        with self.store.connect() as db:
            reference = db.execute("SELECT bot_reference FROM telegram_config").fetchone()[0]
        if reference != bot_reference(token):
            raise AtlasError("The configured bot does not match this recipient registry.")

    def opt_out_updates(self, updates):
        """Only authenticated Telegram private /stop messages can self-disable; never enroll."""
        now = now_utc()
        count = 0
        with self.store.connect(write=True) as db:
            offset = db.execute("SELECT update_offset FROM telegram_config").fetchone()[0]
            latest = offset
            for update in updates[:100]:
                if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                    continue
                if update["update_id"] < offset:
                    continue
                latest = max(latest, update["update_id"] + 1)
                message = update.get("message", {})
                if not isinstance(message, dict):
                    continue
                chat, sender = message.get("chat", {}), message.get("from", {})
                if (
                    not isinstance(chat, dict)
                    or not isinstance(sender, dict)
                    or chat.get("type") != "private"
                    or sender.get("is_bot") is True
                    or type(chat.get("id")) is not int
                    or type(sender.get("id")) is not int
                    or chat["id"] <= 0
                    or chat["id"] != sender.get("id")
                    or not isinstance(message.get("text"), str)
                    or message["text"].strip() not in ("/stop", "/atlas_stop")
                    or type(message.get("date")) is not int
                ):
                    continue
                row = db.execute(
                    """SELECT recipient_id,approved_at FROM telegram_recipients
                    WHERE chat_id=? AND state='APPROVED' AND enabled=1""",
                    (str(chat["id"]),),
                ).fetchone()
                if row is None:
                    continue
                if (
                    not int(datetime.fromisoformat(row["approved_at"]).timestamp())
                    <= message["date"]
                    <= int(now.timestamp()) + 60
                ):
                    continue
                db.execute(
                    "UPDATE telegram_recipients SET enabled=0 WHERE recipient_id=?",
                    (row["recipient_id"],),
                )
                self._cancel(db, row["recipient_id"])
                self._audit(db, "RECIPIENT_OPT_OUT", row["recipient_id"])
                count += 1
            db.execute("UPDATE telegram_config SET update_offset=? WHERE id=1", (latest,))
        return count
