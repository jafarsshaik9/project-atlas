"""Durable one-photo routing receipts in the existing delivery audit stream.

No schema/lease changes: only notification audit events, under an existing outbox claim.
"""

import json

from atlas.activity import ActivityEvent, EventType, now_utc
from atlas.config import AtlasError


def receipt(source, attempt, kind, code, stage):
    return ActivityEvent.create(
        run_id=source.run_id,
        event_type=kind,
        ordinal=f"visual:{source.event_id}:{stage}",
        at=now_utc(),
        cause=source.event_id,
        payload={"source_event_id": source.event_id, "attempt": attempt, "code": code},
    )


def check_claim(db, event, owner, attempt):
    if (
        db.execute(
            "SELECT 1 FROM outbox WHERE event_id=? AND owner=? AND attempts=? AND state='SENDING'",
            (event.event_id, owner, attempt),
        ).fetchone()
        is None
    ):
        raise AtlasError("Notification lease lost; image upload is disabled.")


def route(store, event, owner, attempt, *, want_photo, delivery_id=None):
    """Commit a receipt before HTTP; an orphaned upload can never trigger a resend."""
    if delivery_id is not None:
        from atlas.telegram_fanout import Deliveries

        return Deliveries(store).media_route(delivery_id, owner, attempt, want_photo)
    started = receipt(event, attempt, EventType.DELIVERY_ATTEMPT, "PHOTO_UPLOAD_STARTED", "start")
    ids = [
        receipt(event, attempt, kind, "LOOKUP", "result").event_id
        for kind in (EventType.DELIVERY_OK, EventType.DELIVERY_FAILED)
    ]
    with store.connect(write=True) as db:
        check_claim(db, event, owner, attempt)
        if db.execute("SELECT 1 FROM events WHERE event_id=?", (started.event_id,)).fetchone():
            outcome = db.execute(
                "SELECT content FROM events WHERE event_id IN (?,?)", ids
            ).fetchone()
            code = json.loads(outcome[0])["payload"]["code"] if outcome else None
            if code == "PHOTO_DELIVERED":
                return "delivered"
            if code in ("PHOTO_REJECTED", "PHOTO_RATE_LIMIT", "PHOTO_NOT_SENT"):
                return "text"
            return "uncertain"
        if not want_photo or attempt != 1:
            return "text"
        store.append(db, started)
    return "photo"


def record_result(store, event, owner, attempt, code, *, delivery_id=None):
    if delivery_id is not None:
        from atlas.telegram_fanout import Deliveries

        return Deliveries(store).media_result(delivery_id, owner, attempt, code)
    kind = EventType.DELIVERY_OK if code == "PHOTO_DELIVERED" else EventType.DELIVERY_FAILED
    with store.connect(write=True) as db:
        check_claim(db, event, owner, attempt)
        store.append(db, receipt(event, attempt, kind, code, "result"))
