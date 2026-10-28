"""Provider-specific observations, not equity bars or executable midpoint prices."""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from atlas.config import AtlasError
from atlas.domain import utc
from atlas.market_data.contracts import Contract, Positive


class FxInstrument(Contract):
    name: str = Field(pattern=r"^[A-Z0-9]{2,12}_[A-Z0-9]{2,12}$")
    kind: Literal["CURRENCY", "METAL", "CFD"]
    display_precision: int = Field(strict=True, ge=0, le=12)
    pip_location: int = Field(strict=True, ge=-12, le=12)
    units_precision: int = Field(strict=True, ge=0, le=12)
    minimum_units: Positive
    maximum_units: Positive | None = None
    margin_rate: Positive | None = Field(default=None, le=1)
    # Canonical JSON of documented non-account instrument fields only.
    provider_metadata: str = "{}"
    availability: Literal["LISTED_FOR_ACCOUNT_NOT_CURRENT_TRADING_STATUS"] = (
        "LISTED_FOR_ACCOUNT_NOT_CURRENT_TRADING_STATUS"
    )

    @model_validator(mode="after")
    def ranges(self):
        metadata = json.loads(self.provider_metadata)
        allowed = {
            "maximumPositionSize",
            "minimumTrailingStopDistance",
            "maximumTrailingStopDistance",
            "minimumGuaranteedStopLossDistance",
            "guaranteedStopLossOrderExecutionPremium",
        }
        if not isinstance(metadata, dict) or set(metadata) - allowed:
            raise ValueError("Unsupported instrument metadata fields.")
        for value in metadata.values():
            parsed = Decimal(value)
            if not parsed.is_finite() or parsed < 0:
                raise ValueError("Invalid restriction metadata.")
        if self.maximum_units is not None and self.maximum_units < self.minimum_units:
            raise ValueError("Invalid instrument size range.")
        if self.kind == "CURRENCY" and not re.fullmatch(r"[A-Z]{3}_[A-Z]{3}", self.name):
            raise ValueError("Invalid currency pair.")
        return self

    @property
    def base(self):
        return self.name.split("_")[0] if self.kind == "CURRENCY" else None

    @property
    def quote(self):
        return self.name.split("_")[1] if self.kind == "CURRENCY" else None

    @property
    def pip(self):
        return Decimal(10) ** self.pip_location

    @property
    def unit_step(self):
        return Decimal(10) ** -self.units_precision


class OHLC(Contract):
    o: Positive
    h: Positive
    l: Positive  # noqa: E741 -- documented OANDA OHLC wire field
    c: Positive

    @field_validator("o", "h", "l", "c", mode="before")
    @classmethod
    def exact_decimal(cls, value):
        if not isinstance(value, (str, Decimal)):
            raise ValueError("Provider prices must retain exact decimal strings.")
        return value

    @model_validator(mode="after")
    def relationships(self):
        if self.l > min(self.o, self.c) or self.h < max(self.o, self.c):
            raise ValueError("Impossible OHLC.")
        return self


class FxCandle(Contract):
    source_time: str
    start: datetime
    complete: StrictBool
    activity_count: int = Field(strict=True, ge=0)
    bid: OHLC | None = None
    ask: OHLC | None = None
    mid: OHLC | None = None
    source_available_at: None = None
    availability: Literal["UNKNOWN"] = "UNKNOWN"
    volume_semantics: Literal["NUMBER_OF_PRICES_NOT_TRADED_VOLUME"] = (
        "NUMBER_OF_PRICES_NOT_TRADED_VOLUME"
    )

    @model_validator(mode="after")
    def times(self):
        utc(self.start)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?Z", self.source_time):
            raise ValueError("M1 source timestamp must be UTC and minute aligned.")
        if datetime.fromisoformat(self.source_time) != self.start:
            raise ValueError("Source timestamp mismatch.")
        if not any((self.bid, self.ask, self.mid)):
            raise ValueError("No pricing component.")
        return self


class CandlePlan(Contract):
    provider: Literal["oanda-v20"] = "oanda-v20"
    origin: Literal["ARTIFICIAL_OFFLINE_FIXTURE", "EXTERNAL_HISTORICAL_MARKET_DATA"]
    environment: Literal["live", "practice"]
    instrument: FxInstrument
    start: datetime
    end: datetime
    as_of: datetime
    components: Literal["MBA", "BA", "M", "B", "A"] = "MBA"
    page_minutes: int = Field(default=5000, strict=True, ge=1, le=5000)
    granularity: Literal["M1"] = "M1"
    smooth: Literal[False] = False
    empty_policy: Literal["QUARANTINE", "DOCUMENTED_CLOSURE_ASSUMPTION"] = "QUARANTINE"
    schedule_status: Literal["UNVERIFIED_NO_GAP_CLASSIFICATION"] = (
        "UNVERIFIED_NO_GAP_CLASSIFICATION"
    )

    @model_validator(mode="after")
    def intervals(self):
        for stamp in (self.start, self.end, self.as_of):
            utc(stamp)
        if not self.start < self.end <= self.as_of:
            raise ValueError("Historical interval must end by explicit as-of time.")
        if any(x.second or x.microsecond for x in (self.start, self.end)):
            raise ValueError("Historical interval must be minute aligned.")
        if self.end - self.start > timedelta(days=366 * 3):
            raise ValueError("Partition range exceeds research bound.")
        if self.empty_policy != "QUARANTINE":
            from atlas.forex.quality import open_minutes

            if self.end - self.start > timedelta(days=1) or open_minutes(self.start, self.end):
                raise ValueError("Empty policy requires an entirely documented closure window.")
        return self


def parse_candles(body, plan, start, end):
    try:
        if body["instrument"] != plan.instrument.name or body["granularity"] != "M1":
            raise ValueError
        values = body["candles"]
        if not isinstance(values, list) or len(values) > plan.page_minutes:
            raise ValueError
        required = {"M": "mid", "B": "bid", "A": "ask"}
        result = []
        for raw in values:
            components = {required[c]: raw[required[c]] for c in plan.components}
            candle = FxCandle(
                source_time=raw["time"],
                start=datetime.fromisoformat(raw["time"]).astimezone(UTC),
                complete=raw["complete"],
                activity_count=raw["volume"],
                **components,
            )
            if not start <= candle.start < end:
                raise ValueError
            if result and candle.start <= result[-1].start:
                raise ValueError
            result.append(candle)
        return tuple(result)
    except (ValueError, KeyError, TypeError, AttributeError, AtlasError):
        raise AtlasError("OANDA_CANDLE_CONTRACT_INVALID") from None
