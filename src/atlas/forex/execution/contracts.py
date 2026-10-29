"""Immutable execution inputs. V1 deliberately uses assumed instantaneous bar-close availability."""

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, DivisionByZero, InvalidOperation, Overflow
from typing import Literal

from pydantic import Field, field_validator, model_validator

from atlas.domain import utc
from atlas.forex.audit_calendar import VERSION
from atlas.forex.models import FxCandle, FxInstrument
from atlas.market_data.contracts import Contract, Identifier, Money, Positive

MINUTE = timedelta(minutes=1)
SYMBOLS = ("EUR_USD", "GBP_USD", "USD_JPY")


def arithmetic_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


class Costs(Contract):
    version: Literal["FX_CLOSE_COSTS_V1"] = "FX_CLOSE_COSTS_V1"
    provenance: Identifier
    known_at: datetime
    slippage_pips: Money = Field(ge=0, le=100)
    commission_gbp_per_unit_per_side: Money = Field(ge=0, le=1)
    spread_floor_pips: Money = Field(ge=0, le=100)
    additional_spread_pips: Money = Field(ge=0, le=100)
    max_entry_spread_pips: Positive = Field(default=Decimal(5), le=100)

    @field_validator("known_at")
    @classmethod
    def timestamp(cls, value):
        return utc(value)


class Settings(Contract):
    version: Literal["FOREX_M1_CONSERVATIVE_REPLAY_V1"] = "FOREX_M1_CONSERVATIVE_REPLAY_V1"
    costs: Costs
    starting_capital_gbp: Positive = Field(default=Decimal("1000"), le=Decimal("1000000000"))
    max_exposure_gbp: Positive = Decimal("1000")
    risk_per_trade_gbp: Positive = Decimal("5")
    max_concurrent_risk_gbp: Positive = Decimal("10")
    daily_loss_limit_gbp: Positive = Decimal("10")
    max_positions: int = Field(default=1, strict=True, ge=1, le=3)
    max_holding_minutes: int = Field(default=60, strict=True, ge=1, le=240)
    order_latency_seconds: int = Field(default=1, strict=True, ge=1, le=60)
    # Unknown publication times: zero is an explicit assumption, not a factual claim.
    publication_delay_seconds: Literal[0] = 0
    collision: Literal["STOP_FIRST_V1", "INVALIDATE_V1"] = "STOP_FIRST_V1"
    flat_buffer_minutes: int = Field(default=5, strict=True, ge=1, le=60)
    max_entry_range_pips: Positive = Decimal(100)

    @model_validator(mode="after")
    def limits(self):
        if self.starting_capital_gbp.as_tuple().exponent < -8:
            raise ValueError("Starting cash requires at most eight GBP decimal places.")
        if not (
            self.max_exposure_gbp <= self.starting_capital_gbp
            and self.risk_per_trade_gbp <= self.max_concurrent_risk_gbp <= self.starting_capital_gbp
            and self.daily_loss_limit_gbp <= self.starting_capital_gbp
        ):
            raise ValueError("Unleveraged capital/risk bounds required.")
        return self


class Run(Contract):
    day: date
    session: Literal["LONDON", "NEW_YORK", "OVERLAP"]
    coverage_start: datetime
    coverage_end: datetime
    code_version: Identifier
    calendar_provenance: Identifier
    origin: Literal["ARTIFICIAL_OFFLINE_FIXTURE", "EXTERNAL_HISTORICAL_MARKET_DATA"]

    @model_validator(mode="after")
    def bounds(self):
        utc(self.coverage_start)
        utc(self.coverage_end)
        if not self.coverage_start < self.coverage_end:
            raise ValueError("Coverage must be an explicit nonempty interval.")
        for t in (self.coverage_start, self.coverage_end):
            if t.second or t.microsecond:
                raise ValueError("Coverage must be minute aligned.")
        return self


class Annotation(Contract):
    instrument: Literal["EUR_USD", "GBP_USD", "USD_JPY"]
    start: datetime
    end: datetime
    reason: Identifier
    evidence: Identifier

    @model_validator(mode="after")
    def interval(self):
        utc(self.start)
        utc(self.end)
        if self.start >= self.end:
            raise ValueError("Invalid annotation interval.")
        return self


class Overlay(Contract):
    version: Literal[VERSION] = VERSION
    annotations: tuple[Annotation, ...] = ()

    @field_validator("annotations")
    @classmethod
    def ordered(cls, values):
        return tuple(
            sorted(values, key=lambda a: (a.instrument, a.start, a.end, a.reason, a.evidence))
        )


class Observation(Contract):
    instrument: Literal["EUR_USD", "GBP_USD", "USD_JPY"]
    candle: FxCandle
    dataset_id: Identifier
    quality_id: Identifier


class Frame(Contract):
    start: datetime
    observations: tuple[Observation, ...]

    @model_validator(mode="after")
    def consistent(self):
        utc(self.start)
        if self.start.second or self.start.microsecond:
            raise ValueError("Minute-aligned frame required.")
        names = [o.instrument for o in self.observations]
        if len(names) != len(set(names)) or any(
            o.candle.start != self.start for o in self.observations
        ):
            raise ValueError("Duplicate or misaligned frame observations.")
        return self


class Intent(Contract):
    identity: Identifier
    instrument: Literal["EUR_USD", "GBP_USD", "USD_JPY"]
    side: Literal["long", "short"]
    units: Positive
    stop: Positive
    target: Positive
    signal_at: datetime
    information_cutoff: datetime
    evidence_start: datetime

    @model_validator(mode="after")
    def chronology(self):
        for t in (self.signal_at, self.information_cutoff, self.evidence_start):
            utc(t)
            if t.second or t.microsecond:
                raise ValueError("V1 signals must be on completed minute boundaries.")
        if not self.evidence_start < self.information_cutoff == self.signal_at:
            raise ValueError("V1 signal cutoff must equal the assumed bar-close availability.")
        return self


def instruments_valid(instruments: tuple[FxInstrument, ...]) -> bool:
    return (
        len(instruments) == 3
        and {i.name for i in instruments} == set(SYMBOLS)
        and all(
            i.kind == "CURRENCY" and i.pip_location == (-2 if i.name == "USD_JPY" else -4)
            for i in instruments
        )
    )
