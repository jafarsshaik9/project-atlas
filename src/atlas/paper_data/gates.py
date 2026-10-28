"""Fail-closed read-only entitlement and observation assessment; no order conversion."""

from atlas.config import AtlasError
from atlas.domain import utc
from atlas.paper_data.contracts import (
    Assessment,
    Delay,
    EntitlementStatus,
    Finality,
    Freshness,
    PaperMode,
)


def verify_entitlement(config, response, now):
    utc(now)
    reasons = []
    if not response.verified or not response.paper_use_allowed:
        reasons.append("ENTITLEMENT_NOT_VERIFIED")
    if not response.verified_at <= now < response.expires_at:
        reasons.append("ENTITLEMENT_EXPIRED_OR_FUTURE")
    if (
        response.provider != config.provider
        or config.instrument.venue not in response.exchanges
        or config.instrument.quote_unit not in response.quote_units
    ):
        reasons.append("ENTITLEMENT_SCOPE_MISMATCH")
    if (
        response.delay in (Delay.UNKNOWN, Delay.UNAVAILABLE)
        or response.delay_seconds is None
        or config.declared_delay_seconds is None
        or response.delay != config.delay
        or response.delay_seconds != config.declared_delay_seconds
    ):
        reasons.append("ENTITLEMENT_DELAY_UNVERIFIED")
    return EntitlementStatus(
        verified=not reasons, reasons=tuple(reasons), evidence=response.evidence
    )


def validate_feed(config, policy, now):
    """Configuration diagnostics are independent of whether today's market is open."""
    utc(now)
    reasons = []
    try:
        mapping = config.instrument.mapping(config.provider, now, now)
        if mapping.effective_to is not None and now >= mapping.effective_to:
            raise ValueError("Expired mapping.")
    except ValueError:
        reasons.append("INSTRUMENT_MAPPING_UNAVAILABLE")
    if config.delay in (Delay.UNKNOWN, Delay.UNAVAILABLE):
        reasons.append("FEED_DELAY_UNKNOWN_OR_MISMATCH")
    if config.delay == Delay.DELAYED:
        if not policy.allow_delayed:
            reasons.append("DELAYED_FEED_DISABLED")
        if config.declared_delay_seconds > policy.max_delayed_event_age_seconds:
            reasons.append("DELAY_EXCEEDS_FRESHNESS_POLICY")
    if config.delay == Delay.REPLAY and not policy.allow_replay:
        reasons.append("REPLAY_DISABLED")
    return tuple(reasons)


def assess(observation, config, policy, entitlement, calendar, now, *, last_event_at=None):
    """Freshness is computed at use time; callers cannot assert an observation is fresh."""
    utc(now)
    status = verify_entitlement(config, entitlement, now)
    reasons = list(status.reasons)
    instrument = config.instrument
    if (
        observation.instrument_id != instrument.instrument_id
        or observation.provider != config.provider
        or observation.exchange != instrument.venue
        or observation.quote_unit != instrument.quote_unit
        or observation.quote_currency != instrument.trading_currency
    ):
        reasons.append("INSTRUMENT_MISMATCH")
    try:
        mapping = instrument.mapping(config.provider, observation.event_at, observation.event_at)
        if mapping.effective_to is not None and observation.event_at >= mapping.effective_to:
            raise ValueError("Mapping expired.")
        if observation.provider_symbol != mapping.symbol:
            reasons.append("PROVIDER_SYMBOL_MISMATCH")
    except ValueError:
        reasons.append("INSTRUMENT_MAPPING_UNAVAILABLE")
    if observation.available_at is None:
        reasons.append("PROVIDER_AVAILABILITY_MISSING")
    if observation.received_at > now:
        reasons.append("FUTURE_RECEIPT")
    if observation.delay != config.delay or observation.delay in (Delay.UNKNOWN, Delay.UNAVAILABLE):
        reasons.append("FEED_DELAY_UNKNOWN_OR_MISMATCH")
    if observation.finality != Finality.FINAL:
        reasons.append("NONFINAL_OR_REVISED_DATA")
    if last_event_at is not None and observation.event_at <= utc(last_event_at):
        reasons.append("OUT_OF_ORDER")
    if observation.delay == Delay.DELAYED and not policy.allow_delayed:
        reasons.append("DELAYED_FEED_DISABLED")
    if observation.delay == Delay.REPLAY and not policy.allow_replay:
        reasons.append("REPLAY_DISABLED")
    if observation.available_at is not None:
        actual_delay = (observation.available_at - observation.event_at).total_seconds()
        if observation.delay == Delay.REALTIME and actual_delay > policy.max_realtime_delay_seconds:
            reasons.append("REALTIME_TIMING_UNSUPPORTED")
        if (
            observation.delay == Delay.DELAYED
            and config.declared_delay_seconds is not None
            and actual_delay < config.declared_delay_seconds
        ):
            reasons.append("DELAYED_TIMING_INCONSISTENT")
        if (observation.received_at - observation.available_at).total_seconds() > (
            policy.max_transport_seconds
        ):
            reasons.append("STALE_TRANSPORT")
    age_limit = (
        policy.max_delayed_event_age_seconds
        if observation.delay == Delay.DELAYED
        else policy.max_event_age_seconds
    )
    if (now - observation.event_at).total_seconds() > age_limit:
        reasons.append("STALE_EVENT")
    if (now - observation.received_at).total_seconds() > policy.max_receipt_age_seconds:
        reasons.append("STALE_RECEIPT")
    try:
        session = calendar.session(instrument.venue, now.date())
        # US cash sessions occur on the same UTC/local date. No extended-hours eligibility.
        if (
            session is None
            or not session.contains(now)
            or not session.contains(observation.event_at)
        ):
            reasons.append("INELIGIBLE_SESSION")
    except AtlasError:
        reasons.append("CALENDAR_UNAVAILABLE")
    stale = any(reason.startswith("STALE_") for reason in reasons)
    mode = PaperMode.UNAVAILABLE
    if not reasons:
        mode = {
            Delay.REALTIME: PaperMode.REALTIME,
            Delay.DELAYED: PaperMode.DELAYED,
            Delay.REPLAY: PaperMode.REPLAY,
        }[observation.delay]
    return Assessment(
        eligible=not reasons,
        mode=mode,
        freshness=Freshness.STALE if stale else Freshness.INVALID if reasons else Freshness.FRESH,
        reasons=tuple(reasons),
        assessed_at=now,
        entitlement=status,
    )
