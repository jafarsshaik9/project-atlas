"""Phase 4A source clocks, metadata, freshness and mocked entitlement gates."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from atlas.config import AtlasError
from atlas.domain import QuoteUnit, Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.paper_data.contracts import (
    Delay,
    FeedConfig,
    Freshness,
    FreshnessPolicy,
    MockEntitlementResponse,
    Observation,
    PaperMode,
)
from atlas.paper_data.gates import assess, validate_feed, verify_entitlement
from atlas.paper_data.mock import mock_config, mock_entitlement, mock_script

AT = datetime(2026, 10, 28, 14, tzinfo=UTC)


def changed(model, **values):
    return type(model)(**(model.model_dump() | values))


@pytest.fixture(scope="module")
def calendar():
    return HistoricalCalendar(Venue.NASDAQ, date(2026, 10, 28), date(2026, 10, 28))


@pytest.fixture
def evidence(calendar):
    config = mock_config()
    observation = mock_script(config, AT).events[0].observation
    entitlement = mock_entitlement(config, AT)
    return observation, config, FreshnessPolicy(), entitlement, calendar


def test_observation_is_frozen_and_preserves_all_three_clocks(evidence):
    observation, *_ = evidence
    item = changed(
        observation,
        event_at=AT - timedelta(seconds=2),
        available_at=AT - timedelta(seconds=1),
    )
    assert item.event_at < item.available_at < item.received_at
    assert Observation.model_validate_json(item.model_dump_json()) == item
    assert item.freshness == Freshness.UNCHECKED
    with pytest.raises(ValidationError):
        item.price = "1"


@pytest.mark.parametrize("field", ["event_at", "available_at", "received_at"])
@pytest.mark.parametrize(
    "value",
    [
        "bad",
        "2026-10-28",
        "2026-10-28T14:00:00",
        "2026-10-28T14:00:00+01:00",
        1793196000,
        True,
        datetime(2026, 10, 28, 14),
        datetime(2026, 10, 28, 14, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_malformed_source_timestamps_are_never_coerced(evidence, field, value):
    with pytest.raises((ValueError, AtlasError)):
        changed(evidence[0], **{field: value})


@pytest.mark.parametrize("field", ["event_at", "available_at", "received_at"])
def test_source_timestamps_are_explicit_required_fields(evidence, field):
    values = evidence[0].model_dump(exclude={field})
    with pytest.raises(ValidationError):
        Observation(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"price": "0"},
        {"price": "-1"},
        {"price": "NaN"},
        {"price": "Infinity"},
        {"volume": "-1"},
        {"volume": "NaN"},
        {"quote_unit": "UNKNOWN"},
        {"quote_unit": "GBX"},
        {"quote_currency": "GBP"},
        {"quote_currency": "UNKNOWN"},
        {"exchange": "XLON"},
        {"instrument_id": ""},
        {"provider": "massive"},
        {"provenance": "EXTERNAL"},
        {"freshness": "FRESH"},
        {"event_at": AT + timedelta(seconds=1)},
        {"available_at": AT + timedelta(seconds=1)},
        {"available_at": AT - timedelta(seconds=1)},
        {"finality": "REVISED", "revision": 0},
        {"finality": "FINAL", "revision": 1},
    ],
)
def test_invalid_observation_contracts_fail_closed(evidence, values):
    with pytest.raises((ValueError, AtlasError)):
        changed(evidence[0], **values)


def test_missing_availability_stays_missing_and_is_ineligible(evidence):
    observation, *rest = evidence
    item = changed(observation, available_at=None)
    assert item.available_at is None
    result = assess(item, *rest, AT)
    assert "PROVIDER_AVAILABILITY_MISSING" in result.reasons
    assert result.mode == PaperMode.UNAVAILABLE


def test_valid_mock_replay_and_true_freshness_boundary(evidence):
    observation, config, policy, entitlement, calendar = evidence
    result = assess(*evidence, AT)
    assert result.eligible and result.mode == PaperMode.REPLAY
    assert result.entitlement.scope == "MOCK_ONLY"
    assert result.freshness == Freshness.FRESH
    policy = changed(policy, max_receipt_age_seconds=30)
    assert assess(
        observation, config, policy, entitlement, calendar, AT + timedelta(seconds=30)
    ).eligible
    later = assess(observation, config, policy, entitlement, calendar, AT + timedelta(seconds=31))
    assert later.freshness == Freshness.STALE
    assert set(later.reasons) == {"STALE_EVENT", "STALE_RECEIPT"}


@pytest.mark.parametrize(
    ("changes", "now", "reason"),
    [
        ({"instrument_id": "WRONG"}, AT, "INSTRUMENT_MISMATCH"),
        ({"provider_symbol": "WRONG"}, AT, "PROVIDER_SYMBOL_MISMATCH"),
        ({"exchange": "XNYS"}, AT, "INSTRUMENT_MISMATCH"),
        ({"delay": "UNKNOWN"}, AT, "FEED_DELAY_UNKNOWN_OR_MISMATCH"),
        ({"delay": "UNAVAILABLE"}, AT, "FEED_DELAY_UNKNOWN_OR_MISMATCH"),
        ({"finality": "PROVISIONAL"}, AT, "NONFINAL_OR_REVISED_DATA"),
        ({"finality": "REVISED", "revision": 1}, AT, "NONFINAL_OR_REVISED_DATA"),
        ({}, AT - timedelta(seconds=1), "FUTURE_RECEIPT"),
        ({}, AT + timedelta(seconds=11), "STALE_RECEIPT"),
        ({}, AT.replace(hour=21), "INELIGIBLE_SESSION"),
    ],
)
def test_gate_rejection_reasons(evidence, changes, now, reason):
    result = assess(changed(evidence[0], **changes), *evidence[1:], now)
    assert not result.eligible and result.mode == PaperMode.UNAVAILABLE
    assert reason in result.reasons


def test_transport_delay_and_out_of_order_fail_closed(evidence):
    observation, *rest = evidence
    item = changed(
        observation, event_at=AT - timedelta(seconds=6), available_at=AT - timedelta(seconds=6)
    )
    result = assess(item, *rest, AT, last_event_at=AT)
    assert {"STALE_TRANSPORT", "OUT_OF_ORDER"} <= set(result.reasons)


@pytest.mark.parametrize("actual_delay", [0, 2, 3])
def test_realtime_requires_both_mock_entitlement_and_actual_timing(calendar, actual_delay):
    config = mock_config(delay=Delay.REALTIME)
    observation = mock_script(config, AT).events[0].observation
    observation = changed(observation, event_at=AT - timedelta(seconds=actual_delay))
    entitlement = mock_entitlement(config, AT)
    result = assess(observation, config, FreshnessPolicy(), entitlement, calendar, AT)
    assert result.eligible == (actual_delay <= 2)
    assert result.mode == (PaperMode.REALTIME if actual_delay <= 2 else PaperMode.UNAVAILABLE)
    result = assess(
        observation, config, FreshnessPolicy(), changed(entitlement, verified=False), calendar, AT
    )
    assert result.mode == PaperMode.UNAVAILABLE


def test_delayed_feed_never_promoted_to_realtime(calendar):
    config = mock_config(delay=Delay.DELAYED)
    now = AT + timedelta(minutes=15)
    observation = mock_script(config, now, "delayed").events[0].observation
    entitlement = mock_entitlement(config, now)
    result = assess(observation, config, FreshnessPolicy(), entitlement, calendar, now)
    assert "DELAYED_FEED_DISABLED" in result.reasons
    policy = FreshnessPolicy(allow_delayed=True)
    result = assess(observation, config, policy, entitlement, calendar, now)
    assert result.eligible and result.mode == PaperMode.DELAYED
    dishonest = changed(observation, event_at=now)
    assert (
        "DELAYED_TIMING_INCONSISTENT"
        in assess(dishonest, config, policy, entitlement, calendar, now).reasons
    )
    stale = changed(observation, event_at=now - timedelta(seconds=931))
    assert "STALE_EVENT" in assess(stale, config, policy, entitlement, calendar, now).reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"verified": False}, "ENTITLEMENT_NOT_VERIFIED"),
        ({"paper_use_allowed": False}, "ENTITLEMENT_NOT_VERIFIED"),
        ({"verified_at": AT + timedelta(seconds=1)}, "ENTITLEMENT_EXPIRED_OR_FUTURE"),
        ({"expires_at": AT}, "ENTITLEMENT_EXPIRED_OR_FUTURE"),
        ({"exchanges": (Venue.NYSE,)}, "ENTITLEMENT_SCOPE_MISMATCH"),
        ({"quote_units": (QuoteUnit.GBP,)}, "ENTITLEMENT_SCOPE_MISMATCH"),
        ({"delay": Delay.REALTIME}, "ENTITLEMENT_DELAY_UNVERIFIED"),
        ({"delay": Delay.UNKNOWN}, "ENTITLEMENT_DELAY_UNVERIFIED"),
        ({"delay_seconds": None}, "ENTITLEMENT_DELAY_UNVERIFIED"),
        ({"delay_seconds": 900}, "ENTITLEMENT_DELAY_UNVERIFIED"),
    ],
)
def test_entitlement_harness_rejects_incomplete_evidence(evidence, changes, reason):
    _, config, _, entitlement, _ = evidence
    result = verify_entitlement(config, changed(entitlement, **changes), AT)
    assert not result.verified and reason in result.reasons


@pytest.mark.parametrize(
    "values", [{"network_enabled": True}, {"provider": "massive"}, {"declared_delay_seconds": None}]
)
def test_config_rejects_network_providers_and_unstated_delay(values):
    with pytest.raises(ValueError):
        changed(mock_config(), **values)


def test_config_and_policy_validation_are_strict():
    for value in (-1, True, "30"):
        with pytest.raises(ValidationError):
            FreshnessPolicy(max_event_age_seconds=value)
    config = mock_config()
    with pytest.raises(ValidationError):
        changed(config, delay=Delay.DELAYED, declared_delay_seconds=0)
    with pytest.raises(ValidationError):
        changed(config, instrument=changed(config.instrument, instrument_timezone="Europe/London"))
    with pytest.raises(ValidationError):
        changed(config, instrument=changed(config.instrument, instrument_id=""))
    with pytest.raises(ValidationError):
        FeedConfig(**(config.model_dump() | {"unexpected": "refused"}))
    response = mock_entitlement(config, AT)
    with pytest.raises(ValidationError):
        changed(response, expires_at=response.verified_at)
    with pytest.raises(ValidationError):
        MockEntitlementResponse(**(response.model_dump() | {"verified": "yes"}))


def test_mapping_expiry_replay_disable_and_unknown_calendar_fail_closed(evidence):
    observation, config, policy, entitlement, _ = evidence
    mapping = changed(config.instrument.mappings[0], effective_to=AT)
    config = changed(config, instrument=changed(config.instrument, mappings=(mapping,)))
    calendar = HistoricalCalendar(Venue.NASDAQ, date(2026, 10, 27), date(2026, 10, 27))
    result = assess(
        observation, config, changed(policy, allow_replay=False), entitlement, calendar, AT
    )
    assert {"INSTRUMENT_MAPPING_UNAVAILABLE", "REPLAY_DISABLED", "CALENDAR_UNAVAILABLE"} <= set(
        result.reasons
    )


def test_feed_validation_checks_effective_mapping_and_policy(evidence):
    _, config, policy, _, _ = evidence
    assert validate_feed(config, policy, AT) == ()
    mapping = changed(config.instrument.mappings[0], effective_to=AT)
    expired = changed(config, instrument=changed(config.instrument, mappings=(mapping,)))
    assert validate_feed(expired, policy, AT) == ("INSTRUMENT_MAPPING_UNAVAILABLE",)
    delayed = mock_config(delay=Delay.DELAYED)
    assert set(validate_feed(delayed, FreshnessPolicy(max_delayed_event_age_seconds=100), AT)) == {
        "DELAYED_FEED_DISABLED",
        "DELAY_EXCEEDS_FRESHNESS_POLICY",
    }
    unknown = changed(config, delay=Delay.UNKNOWN)
    assert "FEED_DELAY_UNKNOWN_OR_MISMATCH" in validate_feed(unknown, policy, AT)
