"""Versioned historical contracts: source time is distinct from ingestion time."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

from atlas.domain import Currency, QuoteUnit, Venue, utc

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:/-]{1,160}$")]
Money = Annotated[Decimal, Field(allow_inf_nan=False)]
Positive = Annotated[Money, Field(gt=0)]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_serializer("*", mode="wrap", when_used="json", check_fields=False)
    def deterministic_decimal_text(self, value, handler):
        # Decimal.__str__ otherwise follows the caller's Context.capitals setting.
        return str(value).upper() if isinstance(value, Decimal) else handler(value)

    @model_validator(mode="after")
    def no_credential_payloads(self):
        text = self.model_dump_json()
        if re.search(
            r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
            r"\d{6,}:[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY)",
            text,
        ):
            raise ValueError("Credential-shaped values are not historical market data.")
        return self


class Origin(StrEnum):
    FIXTURE = "ARTIFICIAL_OFFLINE_FIXTURE"
    HISTORICAL = "EXTERNAL_HISTORICAL_MARKET_DATA"


class Availability(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    ASSUMED = "ASSUMED_BAR_END_PLUS_DELAY"


class Adjustment(StrEnum):
    RAW = "UNADJUSTED"
    SPLITS = "SPLIT_ADJUSTED"
    TOTAL_RETURN = "TOTAL_RETURN_ADJUSTED"
    UNKNOWN = "UNKNOWN"


class SymbolMapping(Contract):
    provider: Identifier
    symbol: Identifier
    mic: Venue
    currency_code: Literal["GBP", "GBX", "USD"]
    effective_from: datetime
    effective_to: datetime | None = None
    metadata_source: str = Field(min_length=1, max_length=300)

    @field_validator("effective_from", "effective_to")
    @classmethod
    def timestamps(cls, value):
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def range(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("Invalid effective symbol range.")
        return self


class HistoricalInstrument(Contract):
    instrument_id: Identifier
    venue: Venue
    trading_currency: Currency
    quote_unit: QuoteUnit
    instrument_timezone: Literal["Europe/London", "America/New_York"]
    exchange_timezone: Literal["Europe/London", "America/New_York"]
    mappings: tuple[SymbolMapping, ...] = Field(min_length=1, max_length=20)
    identifiers: tuple[tuple[str, str], ...] = ()
    asset_class: Literal["equity"] = "equity"
    metadata_evidence: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def metadata(self):
        expected_timezone = "Europe/London" if self.venue == Venue.LSE else "America/New_York"
        if (
            self.exchange_timezone != expected_timezone
            or self.quote_unit.currency != self.trading_currency
        ):
            raise ValueError("Inconsistent exchange timezone, quotation unit or currency metadata.")
        if len(dict(self.identifiers)) != len(self.identifiers):
            raise ValueError("Duplicate instrument identifiers.")
        keys = set()
        for mapping in self.mappings:
            key = (mapping.provider, mapping.effective_from)
            if (
                key in keys
                or mapping.mic != self.venue
                or mapping.currency_code != self.quote_unit.value
            ):
                raise ValueError("Inconsistent or ambiguous provider symbol metadata.")
            keys.add(key)
        for index, first in enumerate(self.mappings):
            for second in self.mappings[index + 1 :]:
                if first.provider == second.provider and (
                    (first.effective_to is None or second.effective_from < first.effective_to)
                    and (second.effective_to is None or first.effective_from < second.effective_to)
                ):
                    raise ValueError("Overlapping effective provider symbol mappings are refused.")
        return self

    def mapping(self, provider, start, end):
        choices = [
            m
            for m in self.mappings
            if m.provider == provider
            and m.effective_from <= start
            and (m.effective_to is None or end <= m.effective_to)
        ]
        if len(choices) != 1:
            raise ValueError("No unique effective provider mapping covers this request.")
        return choices[0]


class CorporateAction(Contract):
    action_id: Identifier
    instrument_id: Identifier
    kind: Literal["split", "dividend", "ticker_change", "delisting"]
    event_at: datetime | None = None
    effective_date: date | None = None
    available_at: datetime | None = None
    source: Identifier
    ratio: Positive | None = None
    cash_amount: Annotated[Money, Field(ge=0)] | None = None
    currency: Currency | None = None
    old_symbol: Identifier | None = None
    new_symbol: Identifier | None = None

    @field_validator("event_at", "available_at")
    @classmethod
    def timestamps(cls, value):
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def fields(self):
        if self.event_at is None and self.effective_date is None:
            raise ValueError(
                "Corporate actions need a sourced timestamp or explicit date-only precision."
            )
        if self.kind == "split" and self.ratio is None:
            raise ValueError("A split requires its explicitly sourced ratio.")
        if self.kind == "dividend" and (self.cash_amount is None or self.currency is None):
            raise ValueError("A dividend requires amount and currency.")
        if self.kind == "ticker_change" and (not self.old_symbol or not self.new_symbol):
            raise ValueError("A ticker change requires both symbols.")
        return self


class DownloadRequest(Contract):
    provider: Identifier
    instrument: HistoricalInstrument
    start: datetime
    end: datetime
    resolution: Literal["1min"] = "1min"
    adjustment: Adjustment = Adjustment.RAW
    assumed_delay_seconds: int | None = Field(default=None, ge=0, le=3600)
    fixture_scenario: Identifier = "normal"
    schema_version: Literal[1] = 1

    @field_validator("start", "end")
    @classmethod
    def timestamps(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def window(self):
        if not self.start < self.end or self.end - self.start > timedelta(days=366):
            raise ValueError("Request must cover a positive interval of at most 366 days.")
        if any(t.second or t.microsecond for t in (self.start, self.end)):
            raise ValueError("Historical requests require minute-aligned boundaries.")
        self.instrument.mapping(self.provider, self.start, self.end)
        return self


class HistoricalBar(Contract):
    instrument_id: Identifier
    venue: Venue
    provider: Identifier
    start: datetime
    end: datetime
    event_at: datetime
    source_available_at: datetime | None
    availability: Availability
    permitted_at: datetime | None
    ingested_at: datetime
    provider_timestamp: str = Field(min_length=1, max_length=80)
    provider_timestamp_timezone: str = Field(min_length=1, max_length=80)
    interval_convention: Literal["START_INCLUSIVE_END_EXCLUSIVE"] = "START_INCLUSIVE_END_EXCLUSIVE"
    open: Positive
    high: Positive
    low: Positive
    close: Positive
    volume: Annotated[Money, Field(ge=0)]
    currency: Currency
    unit: QuoteUnit
    adjustment: Adjustment
    complete: StrictBool
    quality: tuple[Identifier, ...] = ()
    dataset_version: str = "pending"
    schema_version: Literal[1] = 1

    @field_validator(
        "start", "end", "event_at", "source_available_at", "permitted_at", "ingested_at"
    )
    @classmethod
    def timestamps(cls, value):
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def relationships(self):
        if self.end - self.start != timedelta(minutes=1) or self.event_at != self.end:
            raise ValueError("Invalid one-minute bar/event interval.")
        if self.start.second or self.start.microsecond:
            raise ValueError("Bar interval is not minute-aligned.")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Impossible OHLC relationship.")
        if self.unit.currency != self.currency:
            raise ValueError("Bar quote unit/currency mismatch.")
        if self.end > self.ingested_at and self.complete:
            raise ValueError("A future-ending bar cannot be complete at ingestion.")
        if self.availability == Availability.UNKNOWN:
            if self.source_available_at is not None or self.permitted_at is not None:
                raise ValueError("Unknown availability must not invent a boundary.")
        elif self.availability == Availability.KNOWN:
            if self.source_available_at is None or self.permitted_at != self.source_available_at:
                raise ValueError("Known availability requires its actual source boundary.")
        elif self.source_available_at is not None or self.permitted_at is None:
            raise ValueError(
                "Assumed availability must stay separate from actual source availability."
            )
        for value in (self.source_available_at, self.permitted_at):
            if value is not None and value < self.end:
                raise ValueError("Completed bar cannot be available before its end.")
        if self.source_available_at is not None and self.source_available_at > self.ingested_at:
            raise ValueError("Source availability cannot be after actual ingestion.")
        if self.dataset_version != "pending" and not re.fullmatch(
            r"[a-f0-9]{64}", self.dataset_version
        ):
            raise ValueError("Invalid dataset version.")
        return self

    def visible_at(self, decision_at, *, allow_assumed=False):
        utc(decision_at)
        return (
            self.complete
            and not self.quality
            and self.permitted_at is not None
            and self.permitted_at <= decision_at
            and (self.availability == Availability.KNOWN or allow_assumed)
        )

    def content(self):
        return self.model_dump(mode="json", exclude={"ingested_at", "dataset_version"})


class ProviderDescriptor(Contract):
    provider_id: Identifier
    origin: Origin
    exchanges: tuple[Venue, ...]
    resolutions: tuple[Literal["1min"], ...] = ("1min",)
    authentication: str
    coverage: str
    pagination: str
    rate_limit: str
    retry_policy: str
    licensing: str
    network_required: bool
    activation_status: str
    access_reference: str = "NOT_CONFIGURED"
    generation_parameters: tuple[tuple[str, str], ...] = ()


class RawPage(Contract):
    body: dict
    fetched_at: datetime
    next_cursor: str | None = Field(default=None, max_length=8192)
    wire_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    http_status: int = 200
    normalization_order: Literal["ascending", "descending"]
    original_availability: Literal["fixture_known", "unknown"]
    sanitization: Literal["ALLOWLISTED_MARKET_PAYLOAD_V1"] = "ALLOWLISTED_MARKET_PAYLOAD_V1"

    @field_validator("fetched_at")
    @classmethod
    def timestamp(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def no_private_response_keys(self):
        def inspect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.lower() in (
                        "apikey",
                        "api_key",
                        "api_secret",
                        "token",
                        "password",
                        "authorization",
                        "chat_id",
                        "account_id",
                    ):
                        raise ValueError("Private response fields are not historical market data.")
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        inspect(self.body)
        return self


class Finding(Contract):
    severity: Literal["WARNING", "FAIL"]
    code: Identifier
    count: int = Field(ge=1)
    detail: str = Field(max_length=400)


class QualityReport(Contract):
    status: Literal["PASS", "WARNING", "FAIL"]
    findings: tuple[Finding, ...]
    expected_bars: int
    valid_bars: int
    raw_rows: int
    policy_version: Literal["historical-quality-v1"] = "historical-quality-v1"


class HistoricalAccessError(ValueError):
    """Attempted visibility without the required original/assumed availability boundary."""
