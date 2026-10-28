"""Current observations retain three independent source/availability/receipt clocks."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from atlas.domain import Currency, QuoteUnit, Venue, utc
from atlas.market_data.contracts import (
    Contract,
    HistoricalInstrument,
    Identifier,
    Money,
    Positive,
    digest,
)

PROVIDER = "mock-current-v1"


def timestamp(value):
    """No numeric epochs, naive dates, local times or implicit receipt substitutions."""
    if isinstance(value, str):
        if "T" not in value:
            raise ValueError("An explicit ISO UTC datetime is required.")
        value = datetime.fromisoformat(value)
    return utc(value)


class Delay(StrEnum):
    REALTIME = "REALTIME"
    DELAYED = "DELAYED"
    REPLAY = "REPLAY"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


class PaperMode(StrEnum):
    REALTIME = "PAPER_REALTIME"
    DELAYED = "PAPER_DELAYED"
    REPLAY = "PAPER_REPLAY"
    UNAVAILABLE = "PAPER_UNAVAILABLE"


class Freshness(StrEnum):
    UNCHECKED = "UNCHECKED"
    FRESH = "FRESH"
    STALE = "STALE"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


class Finality(StrEnum):
    FINAL = "FINAL"
    PROVISIONAL = "PROVISIONAL"
    REVISED = "REVISED"


class Observation(Contract):
    observation_id: Identifier
    instrument_id: Identifier
    provider: Literal["mock-current-v1"] = PROVIDER
    provider_symbol: Identifier
    provider_epoch: Identifier
    exchange: Venue
    quote_currency: Currency
    quote_unit: QuoteUnit
    price: Positive
    volume: Money = Field(ge=0)
    event_at: datetime
    available_at: datetime | None  # Required field: explicit None is auditable missing evidence.
    received_at: datetime
    delay: Delay
    finality: Finality
    revision: int = Field(default=0, strict=True, ge=0)
    provenance: Literal["ARTIFICIAL_OFFLINE_MOCK"] = "ARTIFICIAL_OFFLINE_MOCK"
    source_reference: Identifier
    freshness: Literal[Freshness.UNCHECKED] = Freshness.UNCHECKED
    schema_version: Literal[1] = 1

    @field_validator("event_at", "available_at", "received_at", mode="before")
    @classmethod
    def timestamps(cls, value):
        return timestamp(value) if value is not None else None

    @model_validator(mode="after")
    def relationships(self):
        if self.exchange not in (Venue.NYSE, Venue.NASDAQ):
            raise ValueError("Phase 4A supports US cash equity observations only.")
        if self.quote_unit != QuoteUnit.USD or self.quote_currency != Currency.USD:
            raise ValueError("US observations require explicit USD units and currency.")
        if self.event_at > self.received_at or (
            self.available_at is not None
            and not self.event_at <= self.available_at <= self.received_at
        ):
            raise ValueError("Event <= provider availability <= local receipt is required.")
        if (self.finality == Finality.REVISED) != (self.revision > 0):
            raise ValueError("Revision number and finality are inconsistent.")
        return self

    @property
    def identity(self):
        # Receipt and provider process epoch never change a source observation's identity.
        return digest([self.provider, self.instrument_id, self.observation_id, self.revision])

    @property
    def content_checksum(self):
        return digest(self.model_dump(mode="json", exclude={"received_at", "provider_epoch"}))


class FeedConfig(Contract):
    provider: Literal["mock-current-v1"] = PROVIDER
    instrument: HistoricalInstrument
    delay: Delay = Delay.REPLAY
    declared_delay_seconds: int | None = Field(default=0, strict=True, ge=0, le=86400)
    scan_interval_seconds: int = Field(default=1, strict=True, ge=1, le=3600)
    network_enabled: Literal[False] = False

    @model_validator(mode="after")
    def scope(self):
        if self.instrument.venue not in (Venue.NYSE, Venue.NASDAQ):
            raise ValueError("Paper scheduling supports NYSE/NASDAQ only.")
        if self.instrument.quote_unit != QuoteUnit.USD:
            raise ValueError("US paper feeds require USD quotations.")
        if self.instrument.instrument_timezone != "America/New_York":
            raise ValueError("US paper instruments require America/New_York metadata.")
        if not any(m.provider == self.provider for m in self.instrument.mappings):
            raise ValueError("Instrument has no mock provider mapping.")
        if self.delay == Delay.DELAYED and not self.declared_delay_seconds:
            raise ValueError("Delayed feeds require an explicit positive delay.")
        if self.delay in (Delay.REALTIME, Delay.REPLAY) and self.declared_delay_seconds != 0:
            raise ValueError("Realtime/replay feeds require an explicit zero declared delay.")
        return self


class FreshnessPolicy(Contract):
    max_event_age_seconds: int = Field(default=30, strict=True, ge=0, le=86400)
    max_delayed_event_age_seconds: int = Field(default=930, strict=True, ge=0, le=86400)
    max_receipt_age_seconds: int = Field(default=10, strict=True, ge=0, le=3600)
    max_transport_seconds: int = Field(default=5, strict=True, ge=0, le=3600)
    max_realtime_delay_seconds: int = Field(default=2, strict=True, ge=0, le=60)
    allow_delayed: StrictBool = False
    allow_replay: StrictBool = True


class MockEntitlementResponse(Contract):
    """Artificial evidence only; this can never verify an external subscription."""

    provider: Literal["mock-current-v1"] = PROVIDER
    verified: StrictBool
    paper_use_allowed: StrictBool
    delay: Delay
    delay_seconds: int | None = Field(strict=True, ge=0, le=86400)
    exchanges: tuple[Venue, ...]
    quote_units: tuple[QuoteUnit, ...]
    verified_at: datetime
    expires_at: datetime
    evidence: Identifier
    origin: Literal["MOCK_RESPONSE"] = "MOCK_RESPONSE"

    @field_validator("verified_at", "expires_at", mode="before")
    @classmethod
    def timestamps(cls, value):
        return timestamp(value)

    @model_validator(mode="after")
    def window(self):
        if self.expires_at <= self.verified_at:
            raise ValueError("Entitlement evidence requires a positive validity window.")
        return self


class EntitlementStatus(Contract):
    verified: StrictBool
    reasons: tuple[str, ...]
    scope: Literal["MOCK_ONLY"] = "MOCK_ONLY"
    evidence: Identifier


class Assessment(Contract):
    eligible: StrictBool
    mode: PaperMode
    freshness: Freshness
    reasons: tuple[str, ...]
    assessed_at: datetime
    entitlement: EntitlementStatus
