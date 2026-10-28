"""Deterministic cursor-based current feed. Polling never mutates provider state."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pydantic import field_validator, model_validator

from atlas.config import AtlasError
from atlas.domain import Currency, QuoteUnit, Venue, utc
from atlas.market_data.contracts import Contract, HistoricalInstrument, Identifier, SymbolMapping
from atlas.market_data.contracts import digest as content_digest
from atlas.paper_data.contracts import (
    Delay,
    FeedConfig,
    Finality,
    MockEntitlementResponse,
    Observation,
    timestamp,
)


class EventKind(StrEnum):
    OBSERVATION = "OBSERVATION"
    MISSING = "MISSING"
    HALT = "HALT"
    RESUME = "RESUME"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    RESTART = "RESTART"
    SESSION_TRANSITION = "SESSION_TRANSITION"


class MockEvent(Contract):
    at: datetime
    kind: EventKind
    epoch: Identifier = "mock-process-1"
    observation: Observation | None = None

    @field_validator("at", mode="before")
    @classmethod
    def timestamps(cls, value):
        return timestamp(value)

    @model_validator(mode="after")
    def payload(self):
        if (self.kind == EventKind.OBSERVATION) != (self.observation is not None):
            raise ValueError("Only observation events carry an observation.")
        if self.observation is not None and (
            self.at != self.observation.received_at or self.epoch != self.observation.provider_epoch
        ):
            raise ValueError("Mock delivery timestamp/epoch must match its observation.")
        return self


class CurrentDataProvider(Protocol):
    """Read-only protocol. Phase 4A scheduler accepts only the concrete offline mock."""

    @property
    def checksum(self) -> str: ...

    def poll(self, cursor: int, now: datetime) -> MockEvent | None: ...


class MockCurrentProvider:
    def __init__(self, events: tuple[MockEvent, ...]):
        self.events = tuple(events)
        if any(not isinstance(event, MockEvent) for event in self.events):
            raise AtlasError("Mock scripts require validated events.")
        if any(a.at > b.at for a, b in zip(self.events, self.events[1:], strict=False)):
            raise AtlasError("Mock delivery times must be ordered; market event times may regress.")
        self._checksum = content_digest([event.model_dump(mode="json") for event in self.events])

    @property
    def checksum(self):
        return self._checksum

    def poll(self, cursor, now):
        utc(now)
        if type(cursor) is not int or not 0 <= cursor <= len(self.events):
            raise AtlasError("Invalid durable mock cursor.")
        return (
            self.events[cursor]
            if cursor < len(self.events) and self.events[cursor].at <= now
            else None
        )


def mock_config(venue=Venue.NASDAQ, delay=Delay.REPLAY):
    return FeedConfig(
        instrument=HistoricalInstrument(
            instrument_id=f"MOCK-{venue.value}-001",
            venue=venue,
            trading_currency=Currency.USD,
            quote_unit=QuoteUnit.USD,
            instrument_timezone="America/New_York",
            exchange_timezone="America/New_York",
            mappings=(
                SymbolMapping(
                    provider="mock-current-v1",
                    symbol="FAKE",
                    mic=venue,
                    currency_code="USD",
                    effective_from=datetime(2000, 1, 1, tzinfo=UTC),
                    metadata_source="Artificial Phase 4A mapping; no real listing",
                ),
            ),
            metadata_evidence="Original offline mock; no external observations",
        ),
        delay=delay,
        declared_delay_seconds=900 if delay == Delay.DELAYED else 0,
    )


def mock_entitlement(config, at, *, verified=True):
    return MockEntitlementResponse(
        verified=verified,
        paper_use_allowed=True,
        delay=config.delay,
        delay_seconds=config.declared_delay_seconds,
        exchanges=(config.instrument.venue,),
        quote_units=(config.instrument.quote_unit,),
        verified_at=at - timedelta(days=1),
        expires_at=at + timedelta(days=1),
        evidence="artificial-mock-entitlement-v1",
    )


SCENARIOS = (
    "ordered",
    "delayed",
    "missing",
    "duplicate",
    "out-of-order",
    "halt",
    "connection-failure",
    "provider-restart",
    "session-transition",
    "stale",
    "missing-availability",
    "unknown-delay",
)


def mock_script(config, start, scenario="ordered"):
    """Reproducible fault scripts with explicit, independently generated source clocks."""
    utc(start)
    if scenario not in SCENARIOS:
        raise AtlasError("Unknown offline mock scenario.")
    epoch = "mock-process-1"

    def observation(index, at, *, event_at=None, observation_id=None, **changes):
        delay = config.declared_delay_seconds or 0
        market_at = event_at if event_at is not None else at - timedelta(seconds=delay)
        values = dict(
            observation_id=observation_id or f"mock-{index}",
            instrument_id=config.instrument.instrument_id,
            provider_symbol=config.instrument.mapping(config.provider, market_at, market_at).symbol,
            provider_epoch=epoch,
            exchange=config.instrument.venue,
            quote_currency=config.instrument.trading_currency,
            quote_unit=config.instrument.quote_unit,
            price="100.00",
            volume="100",
            event_at=market_at,
            available_at=at,
            received_at=at,
            delay=config.delay,
            finality=Finality.FINAL,
            source_reference=f"mock-script/{scenario}/{index}",
        )
        values.update(changes)
        item = Observation(**values)
        return MockEvent(at=at, kind=EventKind.OBSERVATION, epoch=epoch, observation=item)

    first = observation(0, start)
    second_at = start + timedelta(seconds=1)
    if scenario == "duplicate":
        repeated = first.observation.model_dump()
        repeated["received_at"] = second_at
        duplicate = MockEvent(
            at=second_at, kind=EventKind.OBSERVATION, observation=Observation(**repeated)
        )
        return MockCurrentProvider(
            (first, duplicate, observation(2, second_at + timedelta(seconds=1)))
        )
    if scenario == "out-of-order":
        return MockCurrentProvider(
            (first, observation(1, second_at, event_at=start - timedelta(seconds=1)))
        )
    if scenario in ("halt", "connection-failure", "provider-restart", "missing"):
        kind = {
            "halt": EventKind.HALT,
            "connection-failure": EventKind.CONNECTION_FAILURE,
            "provider-restart": EventKind.RESTART,
            "missing": EventKind.MISSING,
        }[scenario]
        epoch = "mock-process-2" if kind == EventKind.RESTART else epoch
        events = [first, MockEvent(at=second_at, kind=kind, epoch=epoch)]
        events.append(observation(2, start + timedelta(seconds=2)))
        if scenario in ("halt", "connection-failure"):
            recovery = EventKind.RESUME if scenario == "halt" else EventKind.RESTART
            events.append(MockEvent(at=start + timedelta(seconds=3), kind=recovery, epoch=epoch))
            events.append(observation(4, start + timedelta(seconds=4)))
        return MockCurrentProvider(tuple(events))
    if scenario == "session-transition":
        return MockCurrentProvider(
            (first, MockEvent(at=second_at, kind=EventKind.SESSION_TRANSITION))
        )
    if scenario == "stale":
        return MockCurrentProvider((observation(0, start, event_at=start - timedelta(minutes=5)),))
    if scenario == "missing-availability":
        return MockCurrentProvider((observation(0, start, available_at=None),))
    if scenario == "unknown-delay":
        return MockCurrentProvider((observation(0, start, delay=Delay.UNKNOWN),))
    return MockCurrentProvider((first, observation(1, second_at)))
