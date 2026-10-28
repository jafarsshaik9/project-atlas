"""Bounded session ticks persist read-only eligibility evidence; never execute strategies."""

from datetime import datetime, timedelta

from atlas.config import AtlasError
from atlas.market_data.contracts import canonical, digest
from atlas.paper_data.contracts import Assessment, Freshness, FreshnessPolicy, PaperMode
from atlas.paper_data.gates import assess
from atlas.paper_data.mock import EventKind, MockCurrentProvider
from atlas.paper_data.store import time_text


class SessionScheduler:
    def __init__(self, store, stream_id, config, calendar, provider, entitlement, policy=None):
        if type(provider) is not MockCurrentProvider:
            raise AtlasError("Phase 4A accepts only the offline mock current-data provider.")
        self.store, self.stream_id = store, stream_id
        self.config, self.calendar, self.provider = config, calendar, provider
        self.entitlement = entitlement
        self.policy = policy if policy is not None else FreshnessPolicy()
        binding = {
            "schema": "paper-scheduler-v1",
            "feed": config.model_dump(mode="json"),
            "policy": self.policy.model_dump(mode="json"),
            "calendar": calendar.provenance(),
            "mock_script_checksum": provider.checksum,
        }
        self.binding = digest(binding)
        store.register(stream_id, self.binding, binding)

    def _session(self, now):
        try:
            session = self.calendar.session(self.config.instrument.venue, now.date())
            return session if session is not None and session.contains(now) else None
        except AtlasError:
            return None

    def scan(self, lease):
        if lease.stream_id != self.stream_id:
            raise AtlasError("Worker token belongs to another stream.")
        with self.store.connect(write=True) as db:
            row, now = self.store.owned(db, lease)
            if row["binding"] != self.binding:
                raise AtlasError("Scheduler binding mismatch.")
            if self._session(now) is None:
                db.execute(
                    """UPDATE paper_streams SET status='WAITING_SESSION',last_clock=?,
                    last_scan_at=?,last_scan_outcome='WAITING_SESSION'
                    WHERE stream_id=?""",
                    (time_text(now), time_text(now), self.stream_id),
                )
                return {"outcome": "WAITING_SESSION", "cursor": row["cursor"]}
            if row["next_scan_at"] and now < datetime.fromisoformat(row["next_scan_at"]):
                return {"outcome": "NOT_DUE", "cursor": row["cursor"]}
            event = self.provider.poll(row["cursor"], now)
            # Recheck expiry and session after reading, before validation and any durable result.
            row, now = self.store.owned(db, lease)
            if self._session(now) is None:
                db.execute(
                    """UPDATE paper_streams SET status='WAITING_SESSION',last_clock=?,
                    last_scan_at=?,last_scan_outcome='WAITING_SESSION'
                    WHERE stream_id=?""",
                    (time_text(now), time_text(now), self.stream_id),
                )
                return {"outcome": "WAITING_SESSION", "cursor": row["cursor"]}
            next_scan = now + timedelta(seconds=self.config.scan_interval_seconds)
            if event is None:
                status = (
                    "HALTED"
                    if row["halted"]
                    else "DISCONNECTED"
                    if not row["connected"]
                    else "NO_DATA"
                )
                db.execute(
                    """UPDATE paper_streams SET status=?,next_scan_at=?,last_clock=?,
                    last_scan_at=?,last_scan_outcome='NO_DATA'
                    WHERE stream_id=?""",
                    (status, time_text(next_scan), time_text(now), time_text(now), self.stream_id),
                )
                return {
                    "outcome": "NO_DATA",
                    "cursor": row["cursor"],
                    "mode": PaperMode.UNAVAILABLE.value,
                    "freshness": Freshness.UNAVAILABLE.value,
                    "reasons": ["MISSING_OBSERVATION"],
                }
            result = {
                "ordinal": row["cursor"],
                "at": time_text(now),
                "kind": event.kind.value,
                "outcome": event.kind.value,
                "mode": PaperMode.UNAVAILABLE.value,
                "reasons": [],
                "fence": lease.fence,
            }
            halted, connected, epoch = row["halted"], row["connected"], row["provider_epoch"]
            last_event = row["last_event_at"]
            if event.kind == EventKind.HALT:
                halted = 1
            elif event.kind == EventKind.RESUME:
                if event.epoch == epoch:
                    halted = 0
                else:
                    result["outcome"] = "REJECTED"
                    result["reasons"] = ["PROVIDER_EPOCH_MISMATCH"]
            elif event.kind == EventKind.CONNECTION_FAILURE:
                connected = 0
            elif event.kind == EventKind.RESTART:
                # Restart recovers transport only; an exchange halt still needs explicit resume.
                connected, epoch = 1, event.epoch
            elif event.kind == EventKind.OBSERVATION:
                observation = event.observation
                assessment = assess(
                    observation,
                    self.config,
                    self.policy,
                    self.entitlement,
                    self.calendar,
                    now,
                    last_event_at=datetime.fromisoformat(last_event) if last_event else None,
                )
                reasons = list(assessment.reasons)
                if halted:
                    reasons.append("MARKET_HALTED")
                if not connected:
                    reasons.append("PROVIDER_DISCONNECTED")
                if event.epoch != epoch:
                    reasons.append("PROVIDER_EPOCH_MISMATCH")
                seen = db.execute(
                    "SELECT checksum FROM paper_seen WHERE stream_id=? AND identity=?",
                    (self.stream_id, observation.identity),
                ).fetchone()
                if seen is not None:
                    reasons.append(
                        "DUPLICATE_OBSERVATION"
                        if seen[0] == observation.content_checksum
                        else "OBSERVATION_ID_CONFLICT"
                    )
                else:
                    db.execute(
                        "INSERT INTO paper_seen VALUES(?,?,?)",
                        (self.stream_id, observation.identity, observation.content_checksum),
                    )
                eligible = not reasons
                assessment = Assessment(
                    **(
                        assessment.model_dump()
                        | {
                            "eligible": eligible,
                            "reasons": tuple(reasons),
                            "mode": assessment.mode if eligible else PaperMode.UNAVAILABLE,
                            "freshness": assessment.freshness
                            if eligible or assessment.reasons
                            else Freshness.INVALID,
                        }
                    )
                )
                result.update(
                    observation=observation.model_dump(mode="json"),
                    assessment=assessment.model_dump(mode="json"),
                    entitlement_response=self.entitlement.model_dump(mode="json"),
                    reasons=reasons,
                    outcome="ELIGIBLE" if eligible else "REJECTED",
                    mode=assessment.mode.value if eligible else PaperMode.UNAVAILABLE.value,
                )
                if eligible:
                    last_event = time_text(observation.event_at)
            if event.kind != EventKind.OBSERVATION and not result["reasons"]:
                result["reasons"] = [event.kind.value]
            # Cursor, deduplication, high-water mark, controls and audit are one transaction.
            self.store.owned(db, lease)
            db.execute(
                "INSERT INTO paper_results VALUES(?,?,?,?,?)",
                (
                    self.stream_id,
                    row["cursor"],
                    time_text(now),
                    result["outcome"],
                    canonical(result),
                ),
            )
            status = (
                "HALTED"
                if halted
                else "DISCONNECTED"
                if not connected
                else "READY"
                if result["outcome"] == "ELIGIBLE"
                else "DEGRADED"
            )
            db.execute(
                """UPDATE paper_streams SET cursor=cursor+1,last_event_at=?,next_scan_at=?,
                last_clock=?,halted=?,connected=?,provider_epoch=?,status=?,last_scan_at=?,
                last_scan_outcome=? WHERE stream_id=?""",
                (
                    last_event,
                    time_text(next_scan),
                    time_text(now),
                    halted,
                    connected,
                    epoch,
                    status,
                    time_text(now),
                    result["outcome"],
                    self.stream_id,
                ),
            )
            return result
