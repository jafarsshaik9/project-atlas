"""Independent recipient/event deliveries consuming the existing event outbox."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from atlas.activity import ActivityEvent, Environment, EventType, now_utc
from atlas.config import AtlasError
from atlas.telegram_recipients_store import Recipients, recipient_mode


class RecipientClaimLost(AtlasError):
    pass


def category(event):
    if event.event_type in (EventType.FILL, EventType.PARTIAL):
        return "trades"
    if event.event_type in (EventType.BREACH, EventType.KILL):
        return "risk"
    if event.event_type == EventType.RUN_COMPLETED:
        return "summary"
    if event.event_type in (EventType.ERROR, EventType.TEST):
        return "system"
    return None


class Deliveries:
    def __init__(self, store):
        self.store = store
        self.recipients = Recipients(store)

    @staticmethod
    def _audit(db, row, phase, code, now):
        key = f"{row['delivery_id']}:{row['attempts']}:{phase}"
        db.execute(
            """INSERT OR IGNORE INTO telegram_delivery_audit VALUES(?,?,?,?,?,?)""",
            (
                hashlib.sha256(key.encode()).hexdigest(),
                row["delivery_id"],
                row["attempts"],
                now.isoformat(),
                phase,
                code,
            ),
        )

    @staticmethod
    def _fanout(db, config, now):
        rows = db.execute(
            """SELECT e.sequence,e.content,e.event_id FROM outbox o
            JOIN events e USING(event_id) LEFT JOIN telegram_batches b USING(event_id)
            WHERE b.event_id IS NULL AND e.sequence>? AND o.state='PENDING' AND o.attempts=0
            ORDER BY e.sequence LIMIT 25""",
            (config["activation_sequence"],),
        ).fetchall()
        for source in rows:
            event = ActivityEvent.model_validate_json(source["content"])
            kind = category(event)
            db.execute(
                "INSERT INTO telegram_batches VALUES(?,?)", (source["event_id"], now.isoformat())
            )
            if kind is None or event.environment != Environment.SYNTHETIC:
                continue
            # kind is an internal allowlist value, never a user-supplied SQL identifier.
            recipients = db.execute(
                f"""SELECT recipient_id FROM telegram_recipients
                WHERE state='APPROVED' AND enabled=1 AND synthetic=1 AND {kind}=1
                AND start_sequence<? LIMIT 50""",
                (source["sequence"],),
            ).fetchall()
            for recipient in recipients:
                key = f"{source['event_id']}:telegram:{recipient['recipient_id']}"
                db.execute(
                    """INSERT OR IGNORE INTO telegram_deliveries
                    (delivery_id,event_id,recipient_id,category,next_at) VALUES(?,?,?,?,?)""",
                    (
                        hashlib.sha256(key.encode()).hexdigest(),
                        source["event_id"],
                        recipient["recipient_id"],
                        kind,
                        now.isoformat(),
                    ),
                )

    def claim(self, owner, *, now=None, event_id=None):
        with self.store.connect(write=True) as db:
            now = now or now_utc()
            config = db.execute("SELECT * FROM telegram_config").fetchone()
            if not config["multi"] or config["paused"]:
                return None
            self._fanout(db, config, now)
            exhausted = db.execute(
                """SELECT * FROM telegram_deliveries WHERE
                state='SENDING' AND attempts>=5 AND lease_until<=? LIMIT 100""",
                (now.isoformat(),),
            ).fetchall()
            for row in exhausted:
                db.execute(
                    """UPDATE telegram_deliveries SET state='FAILED',owner=NULL,
                    lease_until=NULL,last_code='FINAL_OUTCOME_UNCERTAIN' WHERE delivery_id=?""",
                    (row["delivery_id"],),
                )
                self._audit(db, row, "finished", "FINAL_OUTCOME_UNCERTAIN", now)
            gate = db.execute("SELECT next_at FROM delivery_gate WHERE id=1").fetchone()
            if gate and gate[0] > now.isoformat():
                return None
            selection = " AND d.event_id=?" if event_id else ""
            params = [now.isoformat(), now.isoformat()]
            if event_id:
                params.append(event_id)
            row = db.execute(
                """SELECT d.*,r.chat_id,r.cards FROM telegram_deliveries d
                JOIN telegram_recipients r USING(recipient_id)
                WHERE r.state='APPROVED' AND r.enabled=1 AND r.synthetic=1 AND d.attempts<5
                AND ((d.state IN ('PENDING','RETRY') AND d.next_at<=?)
                    OR (d.state='SENDING' AND d.lease_until<=?))"""
                + selection
                + " ORDER BY d.attempts,d.next_at,d.rowid LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE telegram_deliveries SET state='SENDING',attempts=attempts+1,
                owner=?,lease_until=? WHERE delivery_id=?""",
                (owner, (now + timedelta(seconds=30)).isoformat(), row["delivery_id"]),
            )
            db.execute(
                "INSERT INTO delivery_gate VALUES(1,?) ON CONFLICT(id) "
                "DO UPDATE SET next_at=excluded.next_at",
                ((now + timedelta(seconds=1)).isoformat(),),
            )
            claimed = dict(row)
            claimed["attempts"] += 1
            self._audit(db, claimed, "claimed", "ATTEMPT", now)
            event = ActivityEvent.model_validate_json(
                db.execute(
                    "SELECT content FROM events WHERE event_id=?", (row["event_id"],)
                ).fetchone()[0]
            )
            return event, claimed

    @staticmethod
    def _owned(db, delivery_id, owner, attempt):
        row = db.execute(
            """SELECT d.* FROM telegram_deliveries d
            JOIN telegram_recipients r USING(recipient_id) JOIN telegram_config c ON c.id=1
            WHERE d.delivery_id=? AND d.owner=? AND d.attempts=? AND d.state='SENDING'
            AND r.state='APPROVED' AND r.enabled=1 AND r.synthetic=1
            AND c.multi=1 AND c.paused=0""",
            (delivery_id, owner, attempt),
        ).fetchone()
        if row is None:
            raise RecipientClaimLost("Recipient delivery changed; stale sender has no authority.")
        return row

    def media_route(self, delivery_id, owner, attempt, want_photo):
        with self.store.connect(write=True) as db:
            row = self._owned(db, delivery_id, owner, attempt)
            media = row["media_state"]
            if media == "PHOTO_DELIVERED":
                return "delivered"
            if media in ("PHOTO_REJECTED", "PHOTO_RATE_LIMIT", "PHOTO_NOT_SENT"):
                return "text"
            if media:
                return "uncertain"
            if not want_photo or attempt != 1:
                return "text"
            db.execute(
                "UPDATE telegram_deliveries SET media_state='PHOTO_UPLOAD_STARTED' "
                "WHERE delivery_id=?",
                (delivery_id,),
            )
            self._audit(db, row, "photo_started", "PHOTO_UPLOAD_STARTED", now_utc())
            return "photo"

    def media_result(self, delivery_id, owner, attempt, code):
        with self.store.connect(write=True) as db:
            row = self._owned(db, delivery_id, owner, attempt)
            if row["media_state"] != "PHOTO_UPLOAD_STARTED":
                raise RecipientClaimLost("Photo outcome already recorded; no overwrite allowed.")
            db.execute(
                "UPDATE telegram_deliveries SET media_state=? WHERE delivery_id=?",
                (code, delivery_id),
            )
            self._audit(db, row, "photo_result", code, now_utc())

    def finish(self, delivery_id, owner, attempt, result, *, now=None):
        with self.store.connect(write=True) as db:
            now = now or now_utc()
            row = self._owned(db, delivery_id, owner, attempt)
            state = (
                "SENT"
                if result.code == "DELIVERED"
                else ("RETRY" if result.retry and attempt < 5 else "FAILED")
            )
            delay = max(result.delay, min(60, 2**attempt)) if result.retry else 0
            db.execute(
                """UPDATE telegram_deliveries SET state=?,last_code=?,next_at=?,
                owner=NULL,lease_until=NULL WHERE delivery_id=?""",
                (state, result.code, (now + timedelta(seconds=delay)).isoformat(), delivery_id),
            )
            self._audit(db, row, "finished", result.code, now)


def deliver_recipient_one(store, provider, *, event_id=None, now=None):
    settings = provider.settings
    if not settings.enabled or not settings.allow_synthetic or settings.discovery_only:
        return "DISABLED"
    deliveries = Deliveries(store)
    deliveries.recipients.validate_bot(settings.token)
    owner = uuid4().hex
    claimed = deliveries.claim(owner, now=now, event_id=event_id)
    if claimed is None:
        return "NOT_DUE"
    event, row = claimed
    target = type(provider)(
        replace(
            settings,
            chat_id=row["chat_id"],
            multi_recipient=False,
            cards_enabled=settings.cards_enabled and bool(row["cards"]),
        ),
        transport=provider.transport,
    )
    try:
        result = target.send_claimed(
            event, store, owner, row["attempts"], delivery_id=row["delivery_id"]
        )
        deliveries.finish(row["delivery_id"], owner, row["attempts"], result, now=now)
    except RecipientClaimLost:
        return "RECIPIENT_CHANGED"
    return result.code


def notification_status(store):
    """No private recipient/chat IDs enter dashboard read models or public events."""
    result = store.notification_status()
    if not recipient_mode(store):
        return result
    with store.connect(snapshot=True) as db:
        result["mode"] = "VERIFIED_RECIPIENTS"
        result["counts"] = dict(
            db.execute("SELECT state,count(*) FROM telegram_deliveries GROUP BY state").fetchall()
        )
        row = db.execute(
            "SELECT state,attempts,last_code FROM telegram_deliveries ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        result["last_delivery"] = dict(row) if row else None
        result["approved_recipients"] = db.execute("""SELECT count(*) FROM telegram_recipients
            WHERE state='APPROVED' AND enabled=1""").fetchone()[0]
        result["paused"] = bool(db.execute("SELECT paused FROM telegram_config").fetchone()[0])
    return result
