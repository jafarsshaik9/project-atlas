"""Transactional in-memory simulated accounting. This module cannot contact a broker."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from threading import RLock

from atlas.config import AtlasError
from atlas.costs import validate_fx
from atlas.domain import (
    ZERO,
    AuditEvent,
    Currency,
    FxRate,
    Instrument,
    PortfolioSnapshot,
    Position,
    StrategySignal,
    number,
    utc,
)
from atlas.research import ResearchSettings


@dataclass(frozen=True)
class Reservation:
    intent_id: str
    instrument: Instrument
    signal: StrategySignal
    original_quantity: Decimal
    remaining_quantity: Decimal
    remaining_cash_gbp: Decimal
    planned_risk_gbp: Decimal
    stressed_risk_gbp: Decimal
    entry_price_ceiling: Decimal | None = None
    fx_ceiling: Decimal | None = None
    remaining_fee_budget_gbp: Decimal | None = None


@dataclass(frozen=True)
class FillRecord:
    fill_id: str
    intent_id: str
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal
    fx_rate: Decimal
    fees_gbp: Decimal
    timestamp: datetime
    realized_pnl_gbp: Decimal = ZERO
    gross_pnl_gbp: Decimal = ZERO
    cost_components: tuple[tuple[str, Decimal], ...] = ()


class PortfolioLedger:
    """Average GBP carrying-cost convention, including entry costs.

    USD trades convert immediately to/from GBP; USD settled cash remains zero.
    Unconfirmed exits never create spendable cash. All mutating operations share
    a reentrant lock; failed fills leave state unchanged.
    """

    def __init__(self, settings: ResearchSettings) -> None:
        self.settings = settings
        self.lock = RLock()
        self._cash = settings.starting_capital_gbp
        self._positions: dict[str, Position] = {}
        self._reservations: dict[str, Reservation] = {}
        self._seen_intents: set[str] = set()
        self._fills: list[FillRecord] = []
        self._events: list[AuditEvent] = []
        self._fill_ids: set[str] = set()
        self._version = 0
        self._day: date | None = None
        self._last_time: datetime | None = None
        self._realized = ZERO
        self._daily_realized = ZERO
        self._daily_start_equity = settings.starting_capital_gbp
        self._fees = ZERO

    def _roll_day(self, now: datetime) -> None:
        utc(now)
        if self._last_time is not None and now < self._last_time:
            raise AtlasError("Ledger timestamps cannot move backwards.")
        self._last_time = now
        day = utc(now).date()
        if self._day is not None and day < self._day:
            raise AtlasError("Ledger cannot travel backwards across daily accounting boundaries.")
        if day != self._day:
            self._daily_start_equity = self._cash + sum(
                (position.mark_value_gbp for position in self._positions.values()), ZERO
            )
            self._day = day
            self._daily_realized = ZERO
            self._version += 1

    @property
    def history(self) -> tuple[FillRecord, ...]:
        with self.lock:
            return tuple(self._fills)

    @property
    def audit(self) -> tuple[AuditEvent, ...]:
        with self.lock:
            return tuple(self._events)

    @property
    def total_fees_gbp(self) -> Decimal:
        with self.lock:
            return self._fees

    @property
    def fx_conversion_costs_gbp(self) -> Decimal:
        with self.lock:
            return sum(
                (
                    amount
                    for fill in self._fills
                    for name, amount in fill.cost_components
                    if name.endswith("_fx")
                ),
                ZERO,
            )

    def seen(self, intent_id: str) -> bool:
        with self.lock:
            return intent_id in self._seen_intents

    def has_pending_instrument(self, instrument_id: str) -> bool:
        with self.lock:
            return any(
                reservation.instrument.instrument_id == instrument_id
                for reservation in self._reservations.values()
            )

    def reconcile(self) -> bool:
        reserved = sum((r.remaining_cash_gbp for r in self._reservations.values()), ZERO)
        basis = sum((p.cost_basis_gbp for p in self._positions.values()), ZERO)
        tolerance = Decimal("0.000000000000000001")
        return (
            ZERO <= reserved <= self._cash
            and all(
                p.quantity > 0
                and p.planned_risk_gbp > 0
                and p.stressed_risk_gbp >= p.planned_risk_gbp
                for p in self._positions.values()
            )
            and all(
                r.stressed_risk_gbp >= r.planned_risk_gbp >= 0
                and (
                    r.planned_risk_gbp > 0
                    or (
                        (position := self._positions.get(r.instrument.instrument_id)) is not None
                        and position.signal.intent_id == r.intent_id
                        and position.planned_risk_gbp > 0
                    )
                )
                for r in self._reservations.values()
            )
            and abs(self._cash + basis - self.settings.starting_capital_gbp - self._realized)
            <= tolerance
        )

    def snapshot(self, now: datetime) -> PortfolioSnapshot:
        with self.lock:
            self._roll_day(now)
            positions = tuple(self._positions[key] for key in sorted(self._positions))
            reserved = sum((r.remaining_cash_gbp for r in self._reservations.values()), ZERO)
            unrealized = sum((p.mark_value_gbp - p.cost_basis_gbp for p in positions), ZERO)
            concurrent = sum((p.planned_risk_gbp for p in positions), ZERO) + sum(
                (r.planned_risk_gbp for r in self._reservations.values()), ZERO
            )
            return PortfolioSnapshot(
                self._version,
                now,
                self.settings.starting_capital_gbp,
                ((Currency.GBP, self._cash), (Currency.USD, ZERO)),
                reserved,
                positions,
                self._realized,
                unrealized,
                self._daily_realized,
                self._cash + sum((p.mark_value_gbp for p in positions), ZERO),
                concurrent,
                tuple(sorted(self._reservations)),
                self.reconcile(),
                self._daily_start_equity,
            )

    def reserve(
        self,
        instrument: Instrument,
        signal: StrategySignal,
        quantity: Decimal,
        cash_gbp: Decimal,
        planned_risk_gbp: Decimal,
        stressed_risk_gbp: Decimal,
        now: datetime,
        expected_version: int,
        *,
        entry_price_ceiling: Decimal | None = None,
        fx_ceiling: Decimal | None = None,
        entry_fee_budget_gbp: Decimal | None = None,
    ) -> Reservation:
        """Accounting primitive; research risk authorization is RiskEngine.authorize."""
        with self.lock:
            snapshot = self.snapshot(now)
            if snapshot.version != expected_version or not snapshot.consistent:
                raise AtlasError("SNAPSHOT_INCONSISTENT")
            if self.seen(signal.intent_id):
                raise AtlasError("DUPLICATE_INTENT")
            if signal.instrument_id != instrument.instrument_id or now >= signal.expires_at:
                raise AtlasError("INVALID_OR_EXPIRED_INTENT")
            if not instrument.valid_quantity(quantity):
                raise AtlasError("INVALID_QUANTITY")
            for amount in (cash_gbp, planned_risk_gbp, stressed_risk_gbp):
                number(amount)
            if planned_risk_gbp <= 0 or stressed_risk_gbp < planned_risk_gbp:
                raise AtlasError("UNKNOWN_OR_INVALID_RISK")
            if cash_gbp <= 0 or cash_gbp > snapshot.available_cash_gbp:
                raise AtlasError("INSUFFICIENT_CASH")
            for limit in (entry_price_ceiling, fx_ceiling, entry_fee_budget_gbp):
                if limit is not None:
                    number(limit)
            reservation = Reservation(
                signal.intent_id,
                instrument,
                signal,
                quantity,
                quantity,
                cash_gbp,
                planned_risk_gbp,
                stressed_risk_gbp,
                entry_price_ceiling,
                fx_ceiling,
                entry_fee_budget_gbp,
            )
            self._reservations[signal.intent_id] = reservation
            self._seen_intents.add(signal.intent_id)
            self._version += 1
            self._events.append(AuditEvent(now, "ledger", "reserved", signal.intent_id))
            return reservation

    def cancel(self, intent_id: str, now: datetime) -> None:
        with self.lock:
            self._roll_day(now)
            if intent_id in self._reservations:
                del self._reservations[intent_id]
                self._version += 1
                self._events.append(AuditEvent(now, "ledger", "released", intent_id))

    def _fill_check(
        self,
        fill_id: str,
        quantity: Decimal,
        price: Decimal,
        fees: Decimal,
        components: tuple[tuple[str, Decimal], ...],
    ) -> None:
        if not fill_id or fill_id in self._fill_ids:
            raise AtlasError("DUPLICATE_OR_INVALID_FILL")
        number(quantity, positive=True)
        number(price, positive=True)
        number(fees)
        for _, amount in components:
            number(amount)
        if components and sum((amount for _, amount in components), ZERO) != fees:
            raise AtlasError("FILL_COST_COMPONENTS_DO_NOT_RECONCILE")

    def entry_fill(
        self,
        intent_id: str,
        fill_id: str,
        quantity: Decimal,
        price: Decimal,
        rate: FxRate,
        fees_gbp: Decimal,
        now: datetime,
        *,
        cost_components: tuple[tuple[str, Decimal], ...] = (),
    ) -> None:
        with self.lock:
            self._roll_day(now)
            self._fill_check(fill_id, quantity, price, fees_gbp, cost_components)
            reservation = self._reservations.get(intent_id)
            if reservation is None or now >= reservation.signal.expires_at:
                raise AtlasError("NO_ACTIVE_RESERVATION")
            if not self.reconcile():
                raise AtlasError("SNAPSHOT_INCONSISTENT")
            inst = reservation.instrument
            validate_fx(rate, inst, now)
            if (
                (
                    reservation.entry_price_ceiling is not None
                    and price > reservation.entry_price_ceiling
                )
                or (
                    reservation.fx_ceiling is not None
                    and rate.gbp_per_unit > reservation.fx_ceiling
                )
                or (
                    reservation.remaining_fee_budget_gbp is not None
                    and fees_gbp > reservation.remaining_fee_budget_gbp
                )
            ):
                raise AtlasError("FILL_REQUIRES_NEW_RISK_CHECK")
            if not inst.valid_quantity(quantity) or not inst.valid_price(price):
                raise AtlasError("INVALID_QUANTITY_OR_TICK")
            notional = inst.quote_unit.major(price) * quantity * rate.gbp_per_unit
            debit = notional + fees_gbp
            if quantity > reservation.remaining_quantity or debit > reservation.remaining_cash_gbp:
                raise AtlasError("FILL_EXCEEDS_RESERVATION")
            # Carry the entire original risk on the first partial fill. Releasing
            # an unfilled remainder must not understate fixed exit costs.
            planned = reservation.planned_risk_gbp
            stressed = reservation.stressed_risk_gbp
            existing = self._positions.get(inst.instrument_id)
            position = Position(
                inst,
                quantity,
                notional,
                fees_gbp,
                notional,
                now,
                now,
                planned,
                stressed,
                reservation.signal,
            )
            if existing is not None:
                if existing.signal.intent_id != intent_id:
                    raise AtlasError("POSITION_ALREADY_OPEN")
                position = replace(
                    position,
                    quantity=existing.quantity + quantity,
                    entry_notional_gbp=existing.entry_notional_gbp + notional,
                    entry_cost_gbp=existing.entry_cost_gbp + fees_gbp,
                    mark_value_gbp=existing.mark_value_gbp + notional,
                    opened_at=existing.opened_at,
                    planned_risk_gbp=existing.planned_risk_gbp + planned,
                    stressed_risk_gbp=existing.stressed_risk_gbp + stressed,
                )
            self._cash -= debit
            self._fees += fees_gbp
            self._positions[inst.instrument_id] = position
            remaining = reservation.remaining_quantity - quantity
            if remaining:
                self._reservations[intent_id] = replace(
                    reservation,
                    remaining_quantity=remaining,
                    remaining_cash_gbp=reservation.remaining_cash_gbp - debit,
                    planned_risk_gbp=reservation.planned_risk_gbp - planned,
                    stressed_risk_gbp=reservation.stressed_risk_gbp - stressed,
                    remaining_fee_budget_gbp=(
                        reservation.remaining_fee_budget_gbp - fees_gbp
                        if reservation.remaining_fee_budget_gbp is not None
                        else None
                    ),
                )
            else:
                del self._reservations[intent_id]
            self._record(
                FillRecord(
                    fill_id,
                    intent_id,
                    inst.instrument_id,
                    "buy",
                    quantity,
                    price,
                    rate.gbp_per_unit,
                    fees_gbp,
                    now,
                    cost_components=cost_components,
                )
            )

    def exit_fill(
        self,
        instrument_id: str,
        fill_id: str,
        quantity: Decimal,
        price: Decimal,
        rate: FxRate,
        fees_gbp: Decimal,
        now: datetime,
        *,
        cost_components: tuple[tuple[str, Decimal], ...] = (),
    ) -> None:
        with self.lock:
            self._roll_day(now)
            self._fill_check(fill_id, quantity, price, fees_gbp, cost_components)
            position = self._positions.get(instrument_id)
            if position is None or quantity > position.quantity:
                raise AtlasError("UNSUPPORTED_SHORT_OR_MISSING_POSITION")
            inst = position.instrument
            validate_fx(rate, inst, now)
            if not inst.valid_quantity(quantity) or not inst.valid_price(price):
                raise AtlasError("INVALID_QUANTITY_OR_TICK")
            proceeds = inst.quote_unit.major(price) * quantity * rate.gbp_per_unit
            if fees_gbp > proceeds:
                raise AtlasError("EXIT_FEES_EXCEED_PROCEEDS")
            # The first partial entry carries the full approved risk envelope.
            # Any exit invalidates its unfilled remainder before risk can be
            # released, so that remainder can never reopen an unbudgeted position.
            self.cancel(position.signal.intent_id, now)
            fraction = quantity / position.quantity
            basis = position.cost_basis_gbp * fraction
            net_pnl = proceeds - fees_gbp - basis
            gross_pnl = proceeds - position.entry_notional_gbp * fraction
            self._cash += proceeds - fees_gbp
            self._realized += net_pnl
            self._daily_realized += net_pnl
            self._fees += fees_gbp
            if quantity == position.quantity:
                del self._positions[instrument_id]
            else:
                self._positions[instrument_id] = replace(
                    position,
                    quantity=position.quantity - quantity,
                    entry_notional_gbp=position.entry_notional_gbp * (1 - fraction),
                    entry_cost_gbp=position.entry_cost_gbp * (1 - fraction),
                    mark_value_gbp=position.mark_value_gbp * (1 - fraction),
                    # Keep the conservative envelope until flat. Proportional
                    # release could undercount fixed costs of the remaining exit.
                    planned_risk_gbp=position.planned_risk_gbp,
                    stressed_risk_gbp=position.stressed_risk_gbp,
                )
            self._record(
                FillRecord(
                    fill_id,
                    position.signal.intent_id,
                    instrument_id,
                    "sell",
                    quantity,
                    price,
                    rate.gbp_per_unit,
                    fees_gbp,
                    now,
                    net_pnl,
                    gross_pnl,
                    cost_components,
                )
            )

    def _record(self, fill: FillRecord) -> None:
        self._fills.append(fill)
        self._fill_ids.add(fill.fill_id)
        self._version += 1
        self._events.append(
            AuditEvent(fill.timestamp, "ledger", f"simulated_{fill.side}", fill.fill_id)
        )
        if not self.reconcile():
            raise AssertionError("Internal ledger invariant failed")

    def mark(self, instrument_id: str, price: Decimal, rate: FxRate, now: datetime) -> None:
        with self.lock:
            self._roll_day(now)
            position = self._positions.get(instrument_id)
            if position is None:
                return
            number(price, positive=True)
            if not position.instrument.valid_price(price):
                raise AtlasError("INVALID_MARK_TICK")
            validate_fx(rate, position.instrument, now)
            value = (
                position.instrument.quote_unit.major(price) * position.quantity * rate.gbp_per_unit
            )
            self._positions[instrument_id] = replace(position, mark_value_gbp=value, mark_time=now)
            self._version += 1
