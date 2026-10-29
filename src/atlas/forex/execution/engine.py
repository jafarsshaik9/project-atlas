"""Deterministic intraday state machine. No strategies, stores, brokers, clocks or notifications."""

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from importlib.metadata import version
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.audit_calendar import classification
from atlas.forex.execution.calendar import window
from atlas.forex.execution.contracts import (
    MINUTE,
    Frame,
    Intent,
    Observation,
    Overlay,
    Run,
    Settings,
    arithmetic_context,
    instruments_valid,
)
from atlas.forex.execution.market import (
    adverse_price,
    conversion,
    entry_reason,
    quote_stress,
    reason,
    spread,
)
from atlas.forex.models import FxInstrument
from atlas.market_data.contracts import Contract, canonical, digest

ZERO = Decimal(0)
GBP_UNIT = Decimal(".00000001")
EMPTY_OVERLAY = Overlay()


def jsonable(value):
    if isinstance(value, Contract):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value).upper()
    return value


def implementation_hash() -> str:
    folder = Path(__file__).parent
    return digest(
        {
            p.name: hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
            for p in sorted(folder.glob("*.py"))
        }
    )


@dataclass(frozen=True)
class Event:
    kind: str
    at: datetime
    instrument: str
    identity: str
    reason: str
    details: str  # immutable canonical JSON, never a mutable caller-owned mapping


@dataclass(frozen=True)
class Position:
    intent: Intent
    eligible_at: datetime
    opened_at: datetime
    deadline: datetime
    entry: Decimal
    entry_fee_gbp: Decimal
    reserved_gbp: Decimal
    planned_risk_gbp: Decimal


@dataclass(frozen=True)
class Trade:
    position: Position
    exited_at: datetime
    exit_price: Decimal
    reason: str
    ambiguous: bool
    gross_quote: Decimal
    conversion_rate: Decimal
    gross_gbp: Decimal
    exit_fee_gbp: Decimal
    net_gbp: Decimal


@dataclass(frozen=True)
class Result:
    status: str
    assumptions: str
    binding: str
    input_hash: str
    configuration: str
    cash_gbp: Decimal
    net_pnl_gbp: Decimal | None
    trades: tuple[Trade, ...]
    unresolved_positions: tuple[Position, ...]
    events: tuple[Event, ...]
    checksum: str

    def to_dict(self):
        return jsonable(self)


class _Engine:
    def __init__(
        self, settings: Settings, run: Run, instruments: tuple[FxInstrument, ...], overlay: Overlay
    ):
        if not instruments_valid(instruments):
            raise AtlasError("FX_SUPPORTED_INSTRUMENT_METADATA_REQUIRED")
        self.settings, self.run, self.overlay = settings, run, overlay
        self.instruments = {i.name: i for i in instruments}
        self.window = window(run, settings)
        self.cash = settings.starting_capital_gbp
        self.positions: dict[str, Position] = {}
        self.unresolved: dict[str, Position] = {}
        self.stressed: set[str] = set()
        self.pending: dict[str, Intent] = {}
        self.history: dict[tuple[str, datetime], Observation] = {}
        self.events: list[Event] = []
        self.trades: list[Trade] = []
        self.invalid = False
        self.now = self.window.opening
        self.configuration = {
            "settings": settings.model_dump(mode="json"),
            "run": run.model_dump(mode="json"),
            "instruments": [
                i.model_dump(mode="json") for i in sorted(instruments, key=lambda i: i.name)
            ],
            "overlay": overlay.model_dump(mode="json"),
            "implementation": implementation_hash(),
            "decimal_precision": 34,
            "gbp_settlement": "FLOOR_PNL_CEILING_FEES_1E_MINUS_8_V1",
            "tzdata_version": version("tzdata"),
            "session_window": jsonable(self.window),
        }
        self.binding = digest(self.configuration)
        self.chain = self.binding

    def emit(self, kind, name="", identity="", code="", **details):
        self.events.append(
            Event(kind, self.now, name, identity, code, canonical(jsonable(details)))
        )

    def reject(self, intent, code):
        self.emit("REJECT", intent.instrument, intent.identity, code)

    def block(self, name, code):
        p = self.positions.pop(name)
        self.unresolved[name] = p
        self.invalid = True
        self.emit(
            "UNRESOLVED",
            name,
            p.intent.identity,
            code,
            deadline=p.deadline,
            source_path_unknown=True,
        )

    def conversion_available(self, name, available):
        try:
            conversion(self.instruments[name].quote, Decimal(-1), available, at=self.now)
            return True
        except AtlasError:
            return False

    def signal(self, intent, available):
        if self.invalid:
            return self.reject(intent, "FX_SCENARIO_INVALID")
        if not self.window.opening < intent.signal_at <= self.window.last_signal_at:
            return self.reject(intent, "FX_SIGNAL_OUTSIDE_ENTRY_WINDOW")
        cursor = intent.evidence_start
        if cursor < self.window.opening:
            return self.reject(intent, "FX_SIGNAL_EVIDENCE_OUTSIDE_SESSION")
        while cursor < intent.information_cutoff:
            observation = self.history.get((intent.instrument, cursor))
            issue = reason(observation, self.overlay)
            if issue is None:
                issue = entry_reason(
                    observation, self.instruments[intent.instrument], self.settings
                )
            if issue:
                return self.reject(intent, "FX_SIGNAL_WINDOW_INVALID:" + issue)
            cursor += MINUTE
        if not self.conversion_available(intent.instrument, available):
            return self.reject(intent, "FX_CONVERSION_UNAVAILABLE")
        self.pending[intent.identity] = intent
        self.emit(
            "ORDER_ELIGIBILITY",
            intent.instrument,
            intent.identity,
            "FX_NEXT_CLOSE_ASSUMED",
            signal_at=intent.signal_at,
            information_cutoff=intent.information_cutoff,
            eligible_at=intent.signal_at + timedelta(seconds=self.settings.order_latency_seconds),
            scheduled_execution=intent.signal_at + MINUTE,
        )

    def enter(self, intent, available):
        name = intent.instrument
        o = available.get(name)
        if self.invalid:
            return self.reject(intent, "FX_SCENARIO_INVALID")
        if o is None or not self.conversion_available(name, available):
            return self.reject(intent, "FX_SCHEDULED_EXECUTION_UNAVAILABLE")
        if issue := entry_reason(o, self.instruments[name], self.settings):
            return self.reject(intent, issue)
        if self.now > self.window.flatten_at - MINUTE:
            return self.reject(intent, "FX_NO_COMPLETE_EXECUTION_INTERVAL")
        if name in self.positions or len(self.positions) >= self.settings.max_positions:
            return self.reject(intent, "FX_POSITION_LIMIT")
        meta, bar, costs = self.instruments[name], o.candle, self.settings.costs
        if (
            intent.units < meta.minimum_units
            or intent.units % meta.unit_step
            or (meta.maximum_units is not None and intent.units > meta.maximum_units)
        ):
            return self.reject(intent, "FX_UNITS_INVALID")
        raw = bar.ask.c if intent.side == "long" else bar.bid.c
        try:
            price, adjustment = adverse_price(raw, intent.side, True, bar, meta, costs)
        except AtlasError:
            return self.reject(intent, "FX_NONPOSITIVE_STRESSED_PRICE")
        if not (
            intent.stop < price < intent.target
            if intent.side == "long"
            else intent.target < price < intent.stop
        ):
            return self.reject(intent, "FX_STOP_TARGET_INVALID")
        stress = quote_stress(bar, meta, costs)
        if (intent.side == "long" and intent.stop >= bar.bid.c - stress) or (
            intent.side == "short" and intent.stop <= bar.ask.c + stress
        ):
            return self.reject(intent, "FX_STOP_ALREADY_BREACHED_AT_ENTRY")
        rate = conversion(meta.quote, Decimal(-1), available, at=self.now)
        fee = (intent.units * costs.commission_gbp_per_unit_per_side).quantize(
            GBP_UNIT, rounding=ROUND_CEILING
        )
        reserve = intent.units * (bar.ask.c + adjustment) * rate
        risk = intent.units * (abs(price - intent.stop) + adjustment) * rate + 2 * fee
        reserved = sum((p.reserved_gbp for p in self.positions.values()), ZERO)
        exposure, equity = self.mark(available)
        reserved = max(reserved, exposure)
        total_risk = sum((p.planned_risk_gbp for p in self.positions.values()), ZERO) + risk
        if (
            risk > self.settings.risk_per_trade_gbp
            or total_risk > self.settings.max_concurrent_risk_gbp
            or reserve + reserved > self.settings.max_exposure_gbp
            or reserve + reserved + fee > min(self.cash, equity)
            or self.settings.starting_capital_gbp - equity >= self.settings.daily_loss_limit_gbp
        ):
            return self.reject(intent, "FX_CAPITAL_EXPOSURE_OR_RISK_LIMIT")
        eligible = intent.signal_at + timedelta(seconds=self.settings.order_latency_seconds)
        if not intent.information_cutoff == intent.signal_at < eligible <= self.now:
            return self.reject(intent, "FX_INFORMATION_TIME_INVALID")
        position = Position(
            intent,
            eligible,
            self.now,
            min(
                self.now + timedelta(minutes=self.settings.max_holding_minutes),
                self.window.flatten_at,
            ),
            price,
            fee,
            reserve,
            risk,
        )
        self.positions[name] = position
        self.cash -= fee
        self.emit(
            "ENTRY",
            name,
            intent.identity,
            "FX_NEXT_CLOSE_ASSUMED",
            position=position,
            indicative_spread_pips=spread(bar, meta),
            price_event_at=bar.start + MINUTE,
            dataset=o.dataset_id,
            quality=o.quality_id,
            costs=costs,
        )

    def close(self, name, available, raw, code, ambiguous=False, *, contingent=False):
        p, o = self.positions[name], available[name]
        meta, costs = self.instruments[name], self.settings.costs
        try:
            if contingent:
                slip = costs.slippage_pips * meta.pip
                price = raw - slip if p.intent.side == "long" else raw + slip
                if price <= 0:
                    raise AtlasError("FX_NONPOSITIVE_EXIT_PRICE")
            else:
                price, _ = adverse_price(raw, p.intent.side, False, o.candle, meta, costs)
        except AtlasError:
            return self.block(name, "FX_NONPOSITIVE_EXIT_PRICE")
        gross = (price - p.entry) * p.intent.units * (1 if p.intent.side == "long" else -1)
        rate = conversion(meta.quote, gross, available, at=self.now)
        fee = (p.intent.units * costs.commission_gbp_per_unit_per_side).quantize(
            GBP_UNIT, rounding=ROUND_CEILING
        )
        settled = (gross * rate).quantize(GBP_UNIT, rounding=ROUND_FLOOR)
        net = settled - p.entry_fee_gbp - fee
        self.cash += settled - fee
        trade = Trade(p, self.now, price, code, ambiguous, gross, rate, settled, fee, net)
        self.trades.append(trade)
        del self.positions[name]
        self.emit(
            "EXIT",
            name,
            p.intent.identity,
            code,
            trade=trade,
            dataset=o.dataset_id,
            quality=o.quality_id,
            exact_intrabar_exit_time="UNKNOWN",
        )

    def manage(self, name, available):
        p = self.positions[name]
        if name not in available or not self.conversion_available(name, available):
            return self.block(name, "FX_OPEN_POSITION_DATA_OR_CONVERSION_GAP")
        if self.now > p.deadline:
            return self.block(name, "FX_LATE_FLATTENING")
        o = available[name]
        bar = o.candle
        if bar.start < p.opened_at:
            return self.block(name, "FX_PRE_ENTRY_RANGE_UNUSABLE")
        component = bar.bid if p.intent.side == "long" else bar.ask
        stress = quote_stress(bar, self.instruments[name], self.settings.costs)
        shift = -stress if p.intent.side == "long" else stress
        stop = (
            component.l + shift <= p.intent.stop
            if p.intent.side == "long"
            else component.h + shift >= p.intent.stop
        )
        target = (
            component.h + shift >= p.intent.target
            if p.intent.side == "long"
            else component.l + shift <= p.intent.target
        )
        if stop and target and self.settings.collision == "INVALIDATE_V1":
            return self.block(name, "FX_AMBIGUOUS_STOP_TARGET")
        if stop:
            raw = (
                min(p.intent.stop, component.o + shift)
                if p.intent.side == "long"
                else max(p.intent.stop, component.o + shift)
            )
            return self.close(
                name,
                available,
                raw,
                "FX_STOP_FIRST_AMBIGUOUS" if target else "FX_STOP",
                bool(target),
                contingent=True,
            )
        if target:
            return self.close(
                name, available, p.intent.target, "FX_TARGET_NO_PRICE_IMPROVEMENT", contingent=True
            )
        if self.now == p.deadline:
            return self.close(
                name,
                available,
                component.c,
                "FX_MANDATORY_FLAT" if p.deadline == self.window.flatten_at else "FX_MAX_HOLDING",
            )

    def mark(self, available):
        exposure, equity = ZERO, self.cash
        for name, p in sorted(self.positions.items()):
            b, meta = available[name].candle, self.instruments[name]
            raw = b.bid.c if p.intent.side == "long" else b.ask.c
            exit_price, _ = adverse_price(raw, p.intent.side, False, b, meta, self.settings.costs)
            pnl = (exit_price - p.entry) * p.intent.units * (1 if p.intent.side == "long" else -1)
            equity += (pnl * conversion(meta.quote, pnl, available, at=self.now)).quantize(
                GBP_UNIT, rounding=ROUND_FLOOR
            )
            equity -= (
                p.intent.units * self.settings.costs.commission_gbp_per_unit_per_side
            ).quantize(GBP_UNIT, rounding=ROUND_CEILING)
            ask, _ = adverse_price(b.ask.c, "long", True, b, meta, self.settings.costs)
            exposure += (
                p.intent.units * ask * conversion(meta.quote, Decimal(-1), available, at=self.now)
            )
        return exposure, equity

    def process(self, frame, signals):
        self.now = frame.start + MINUTE
        ordered = tuple(sorted(frame.observations, key=lambda o: o.instrument))
        self.chain = digest(
            [self.chain, Frame(start=frame.start, observations=ordered).model_dump(mode="json")]
        )
        available = {}
        for name in sorted(self.instruments):
            o = next((x for x in ordered if x.instrument == name), None)
            if issue := reason(o, self.overlay):
                self.emit(
                    "DATA_INELIGIBLE",
                    name,
                    code=issue,
                    original_absence=classification(frame.start, observed=False),
                    dataset=o.dataset_id if o else None,
                    overlay_version=self.overlay.version,
                )
            else:
                available[name] = o
                self.history[name, frame.start] = o
        for name in sorted(tuple(self.positions)):
            self.manage(name, available)
        for name in sorted(self.unresolved):
            if name in available and name not in self.stressed:
                p = self.unresolved[name]
                b = available[name].candle
                raw = b.bid.c if p.intent.side == "long" else b.ask.c
                try:
                    stressed, _ = adverse_price(
                        raw, p.intent.side, False, b, self.instruments[name], self.settings.costs
                    )
                except AtlasError:
                    continue
                self.emit(
                    "RESUMPTION_STRESS_ONLY",
                    name,
                    p.intent.identity,
                    "FX_NOT_A_BOOKED_EXIT",
                    adverse_price=stressed,
                    unknown_path=True,
                )
                self.stressed.add(name)
        # Revalue outstanding exposure using current adverse conversion; never stale prices.
        try:
            exposure, equity = self.mark(available)
        except AtlasError:
            for name in sorted(tuple(self.positions)):
                self.block(name, "FX_MARK_UNAVAILABLE")
            exposure, equity = ZERO, self.cash
        breach = (
            "FX_DAILY_EQUITY_LOSS_EXIT"
            if self.settings.starting_capital_gbp - equity >= self.settings.daily_loss_limit_gbp
            else "FX_EXPOSURE_LIMIT_EXIT"
            if exposure > min(self.settings.max_exposure_gbp, equity)
            else None
        )
        if breach:
            for name in sorted(tuple(self.positions)):
                p = self.positions[name]
                b = available[name].candle
                self.close(
                    name,
                    available,
                    b.bid.c if p.intent.side == "long" else b.ask.c,
                    breach,
                )
        for key in sorted(tuple(self.pending)):
            intent = self.pending[key]
            if intent.signal_at + MINUTE == self.now:
                del self.pending[key]
                self.enter(intent, available)
        for intent in signals:
            self.signal(intent, available)


def replay(
    settings: Settings,
    run: Run,
    instruments: tuple[FxInstrument, ...],
    frames: Iterable[Frame],
    intents: tuple[Intent, ...],
    *,
    overlay: Overlay = EMPTY_OVERLAY,
) -> Result:
    """One session, streaming chronological frames; caller supplies signals, never a strategy.

    Missing frames become clock ticks with zero observations, never fabricated prices.
    Inputs outside this one-session scope or unordered timestamps are rejected.
    """
    with localcontext(arithmetic_context()):
        engine = _Engine(settings, run, instruments, overlay)
        if len(intents) > 1000 or len({i.identity for i in intents}) != len(intents):
            raise AtlasError("FX_DUPLICATE_OR_EXCESSIVE_ORDER_IDENTITIES")
        signals = {}
        for intent in sorted(intents, key=lambda i: (i.signal_at, i.identity)):
            if not engine.window.opening < intent.signal_at <= engine.window.last_signal_at:
                engine.reject(intent, "FX_SIGNAL_OUTSIDE_ENTRY_WINDOW")
            else:
                signals.setdefault(intent.signal_at, []).append(intent)
        engine.configuration["intents"] = [
            i.model_dump(mode="json") for i in sorted(intents, key=lambda i: i.identity)
        ]
        engine.binding = digest(engine.configuration)
        engine.chain = engine.binding
        cursor = engine.window.opening
        for frame in frames:
            if not isinstance(frame, Frame) or not cursor <= frame.start < engine.window.flatten_at:
                raise AtlasError("FX_FRAME_OUT_OF_ORDER_OR_SCOPE")
            while cursor < frame.start:
                engine.process(
                    Frame(start=cursor, observations=()), signals.get(cursor + MINUTE, ())
                )
                cursor += MINUTE
            engine.process(frame, signals.get(cursor + MINUTE, ()))
            cursor += MINUTE
        while cursor < engine.window.flatten_at:
            engine.process(Frame(start=cursor, observations=()), signals.get(cursor + MINUTE, ()))
            cursor += MINUTE
        if engine.pending or engine.positions:
            raise AtlasError("FX_TERMINAL_STATE_INVARIANT")
        valid = not engine.invalid
        payload = {
            "status": "VALID_ASSUMED_EXECUTION" if valid else "INVALID_UNRESOLVED_PATH",
            "assumptions": "CLOSE_ENDPOINTS_AND_CONTINGENT_OHLC_EXITS_ASSUMED_NOT_BROKER_FILLS",
            "binding": engine.binding,
            "input_hash": engine.chain,
            "configuration": canonical(engine.configuration),
            "cash_gbp": engine.cash,
            "net_pnl_gbp": engine.cash - settings.starting_capital_gbp if valid else None,
            "trades": tuple(engine.trades),
            "unresolved_positions": tuple(engine.unresolved[k] for k in sorted(engine.unresolved)),
            "events": tuple(engine.events),
        }
        if valid and engine.cash != settings.starting_capital_gbp + sum(
            (t.net_gbp for t in engine.trades), ZERO
        ):
            raise AtlasError("FX_ACCOUNTING_RECONCILIATION_FAILED")
        return Result(**payload, checksum=digest(jsonable(payload)))
