"""Versioned, allowlisted activity contracts. No account payloads or credentials."""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas.domain import utc

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")]
Money = Annotated[Decimal, Field(allow_inf_nan=False)]
Positive = Annotated[Money, Field(gt=0)]


class Environment(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    PAPER = "PAPER"
    LIVE = "LIVE"


class EventType(StrEnum):
    RUN_STARTED = "research.started"
    RUN_COMPLETED = "research.completed"
    SCAN_STARTED = "scan.started"
    SCAN_COMPLETED = "scan.completed"
    SIGNAL = "signal.generated"
    RISK_ACCEPTED = "risk.accepted"
    RISK_REJECTED = "risk.rejected"
    INTENT = "simulated.intent"
    FILL = "simulated.fill"
    PARTIAL = "simulated.partial_fill"
    OPENED = "position.opened"
    INCREASED = "position.increased"
    REDUCED = "position.reduced"
    CLOSED = "position.closed"
    VALUATION = "portfolio.valuation"
    BREACH = "risk.breached"
    KILL = "kill_switch.activated"
    ERROR = "system.error"
    DELIVERY_ATTEMPT = "notification.attempted"
    DELIVERY_OK = "notification.succeeded"
    DELIVERY_FAILED = "notification.failed"
    TEST = "notification.test"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def no_secrets(self):
        # Restrict string-bearing payloads even when supplied by local callers.
        encoded = self.model_dump_json()
        if re.search(
            r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
            r"\d{6,}:[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY)",
            encoded,
        ):
            raise ValueError("Sensitive values are not activity data.")
        return self


class PositionView(Contract):
    instrument_id: Identifier
    venue: Literal["XLON", "XNYS", "XNAS"]
    direction: Literal["LONG"] = "LONG"
    quantity: Positive
    quote_unit: Literal["GBP", "GBX", "USD"]
    currency: Literal["GBP", "USD"]
    average_entry_native: Money | None
    average_carrying_cost_gbp: Money
    mark_native: Money | None
    market_value_gbp: Money
    unrealized_pnl_gbp: Money
    planned_risk_gbp: Positive
    stressed_risk_gbp: Positive
    strategy: Identifier
    opened_at: datetime
    mark_at: datetime
    unavailable_reason: str | None = None

    @field_validator("opened_at", "mark_at")
    @classmethod
    def timestamps(cls, value):
        return utc(value)


class Valuation(Contract):
    initial_capital_gbp: Positive
    cash_gbp: Money
    available_cash_gbp: Money
    reserved_cash_gbp: Money
    invested_value_gbp: Money
    equity_gbp: Money
    realized_pnl_gbp: Money
    unrealized_pnl_gbp: Money
    daily_pnl_gbp: Money
    daily_realized_pnl_gbp: Money
    concurrent_planned_risk_gbp: Money
    planned_open_risk_gbp: Money | None = None
    positions: tuple[PositionView, ...]
    snapshot_version: int = Field(ge=0)
    consistent: Literal[True]
    kill_switch: bool
    drawdown_gbp: Money | None = None

    @model_validator(mode="after")
    def accounting(self):
        tolerance = Decimal("1e-16")
        if (
            min(self.cash_gbp, self.available_cash_gbp, self.reserved_cash_gbp) < 0
            or abs(self.cash_gbp - self.available_cash_gbp - self.reserved_cash_gbp) > tolerance
            or abs(self.equity_gbp - self.cash_gbp - self.invested_value_gbp) > tolerance
            or abs(
                self.equity_gbp
                - self.initial_capital_gbp
                - self.realized_pnl_gbp
                - self.unrealized_pnl_gbp
            )
            > tolerance
        ):
            raise ValueError("Authoritative snapshot does not reconcile.")
        if (
            len({p.instrument_id for p in self.positions}) != len(self.positions)
            or abs(
                sum((p.market_value_gbp for p in self.positions), Decimal(0))
                - self.invested_value_gbp
            )
            > tolerance
            or abs(
                sum((p.unrealized_pnl_gbp for p in self.positions), Decimal(0))
                - self.unrealized_pnl_gbp
            )
            > tolerance
            or (
                self.planned_open_risk_gbp is not None
                and abs(
                    sum((p.planned_risk_gbp for p in self.positions), Decimal(0))
                    - self.planned_open_risk_gbp
                )
                > tolerance
            )
        ):
            raise ValueError("Positions do not reconcile to the authoritative snapshot.")
        return self


class RunPayload(Contract):
    dataset: Identifier
    venue: Literal["XLON", "XNYS", "XNAS"]
    dataset_checksum: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    configuration_checksum: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    initial_capital_gbp: Positive
    ending_equity_gbp: Money | None = None
    net_pnl_gbp: Money | None = None
    completed_trades: int | None = Field(default=None, ge=0)


class SignalPayload(Contract):
    intent_id: Identifier
    side: Literal["buy", "sell"]
    entry: Positive
    stop: Positive
    target: Positive
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def timestamp(cls, value):
        return utc(value)


class RiskPayload(Contract):
    intent_id: Identifier
    status: Literal["approved", "rejected", "research_only"]
    reasons: tuple[Identifier, ...]
    proposed_quantity: Money
    planned_risk_gbp: Money
    stressed_risk_gbp: Money
    capital_usage_gbp: Money
    policy_version: Identifier
    snapshot_version: int


class IntentPayload(Contract):
    intent_id: Identifier
    quantity: Positive


class FillPayload(Contract):
    fill_id: Identifier
    intent_id: Identifier
    side: Literal["buy", "sell"]
    execution_status: Literal["SIMULATED_CONFIRMED"] = "SIMULATED_CONFIRMED"
    quantity: Positive
    price: Positive
    quote_unit: Literal["GBP", "GBX", "USD"]
    currency: Literal["GBP", "USD"]
    venue: Literal["XLON", "XNYS", "XNAS"]
    fx_gbp_per_unit: Positive | None
    gross_gbp: Money | None
    fees_gbp: Money
    realized_pnl_gbp: Money | None
    components: tuple[tuple[Identifier, Money], ...]
    partial: bool
    unavailable_reason: str | None = None


class PositionPayload(Contract):
    quantity_before: Money
    quantity_after: Money


class AlertPayload(Contract):
    code: Identifier
    reasons: tuple[Identifier, ...] = ()
    planned_risk_gbp: Money | None = None
    actual_loss_gbp: Money | None = None


class DeliveryPayload(Contract):
    source_event_id: Identifier
    attempt: int = Field(ge=1, le=5)
    code: Identifier


PAYLOADS = {
    EventType.RUN_STARTED: RunPayload,
    EventType.RUN_COMPLETED: RunPayload,
    EventType.SCAN_STARTED: AlertPayload,
    EventType.SCAN_COMPLETED: AlertPayload,
    EventType.SIGNAL: SignalPayload,
    EventType.RISK_ACCEPTED: RiskPayload,
    EventType.RISK_REJECTED: RiskPayload,
    EventType.INTENT: IntentPayload,
    EventType.FILL: FillPayload,
    EventType.PARTIAL: FillPayload,
    EventType.OPENED: PositionPayload,
    EventType.INCREASED: PositionPayload,
    EventType.REDUCED: PositionPayload,
    EventType.CLOSED: PositionPayload,
    EventType.VALUATION: Valuation,
    EventType.BREACH: AlertPayload,
    EventType.KILL: AlertPayload,
    EventType.ERROR: AlertPayload,
    EventType.DELIVERY_ATTEMPT: DeliveryPayload,
    EventType.DELIVERY_OK: DeliveryPayload,
    EventType.DELIVERY_FAILED: DeliveryPayload,
    EventType.TEST: AlertPayload,
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class ActivityEvent(Contract):
    event_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    event_type: EventType
    environment: Environment = Environment.SYNTHETIC
    event_at: datetime
    available_at: datetime
    processed_at: datetime
    run_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    strategy: Identifier | None = None
    instrument_id: Identifier | None = None
    correlation_id: Identifier
    causation_id: Identifier | None
    schema_version: Literal[1] = 1
    clock_domain: Literal["RESEARCH", "WALL"] = "RESEARCH"
    payload: dict

    @field_validator("event_at", "available_at", "processed_at")
    @classmethod
    def timestamps(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.available_at < self.event_at:
            raise ValueError("Availability precedes event.")
        validated = PAYLOADS[self.event_type].model_validate(self.payload)
        object.__setattr__(self, "payload", validated.model_dump(mode="json"))
        return self

    @classmethod
    def create(
        cls,
        *,
        run_id,
        event_type,
        ordinal,
        at,
        payload,
        cause=None,
        strategy=None,
        instrument_id=None,
        processed_at=None,
    ):
        event_type = EventType(event_type)
        key = f"{run_id}:{event_type}:{ordinal}"
        return cls(
            event_id=hashlib.sha256(key.encode()).hexdigest(),
            event_type=event_type,
            event_at=at,
            available_at=at,
            processed_at=processed_at or now_utc(),
            run_id=run_id,
            correlation_id=run_id,
            causation_id=cause,
            strategy=strategy,
            instrument_id=instrument_id,
            payload=payload,
            clock_domain="WALL"
            if event_type
            in (
                EventType.DELIVERY_ATTEMPT,
                EventType.DELIVERY_OK,
                EventType.DELIVERY_FAILED,
                EventType.TEST,
            )
            else "RESEARCH",
        )
