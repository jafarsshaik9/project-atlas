"""One atomic trading-inbox transition, including simulated execution and accounting."""

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from atlas.config import AtlasError
from atlas.costs import CostEngine, validate_fx
from atlas.domain import (
    ZERO,
    Currency,
    DecisionStatus,
    FxRate,
    Instrument,
    MarketBar,
    MarketQuote,
    PriceBasis,
    QuoteUnit,
    TradeIntent,
    floor_step,
    number,
)
from atlas.market_data.contracts import digest
from atlas.paper_data.contracts import Observation
from atlas.paper_data.gates import assess
from atlas.paper_execution.codec import decode, dump
from atlas.paper_execution.ledger import (
    initialize,
    journal_applied,
    load,
    record,
    rows,
    save_seal,
    write_state,
)
from atlas.risk import RiskEngine, RiskPolicy
from atlas.strategy import ORBConfig, ORBStrategy


@dataclass(frozen=True)
class ExecutionPolicy:
    latency: timedelta = timedelta(seconds=1)
    participation: Decimal = Decimal("0.1")
    fx_max_age: timedelta = timedelta(seconds=60)
    volume_convention: str = "INCREMENTAL_OBSERVATION_SHARES"

    def __post_init__(self):
        if not timedelta(seconds=1) <= self.latency <= timedelta(seconds=60):
            raise AtlasError("Explicit paper latency must be between 1 and 60 seconds.")
        if not ZERO < number(self.participation) <= 1:
            raise AtlasError("Invalid liquidity participation.")
        if not timedelta(0) < self.fx_max_age <= timedelta(seconds=60):
            raise AtlasError("Invalid paper FX freshness limit.")
        if self.volume_convention != "INCREMENTAL_OBSERVATION_SHARES":
            raise AtlasError("Unknown liquidity convention.")


class PaperEngine:
    def __init__(self, scheduler, costs: CostEngine, policy=None, risk_policy=None):
        self.scheduler, self.store = scheduler, scheduler.store
        self.costs = costs
        self.policy = policy or ExecutionPolicy()
        self.risk_policy = risk_policy or RiskPolicy()
        self.strategy = ORBStrategy(ORBConfig(opening_range_minutes=5))
        meta = scheduler.config.instrument
        self.instrument = Instrument(
            meta.instrument_id,
            meta.venue,
            QuoteUnit.USD,
            Decimal("0.01"),
            Decimal("1"),
            Decimal("1"),
            ((scheduler.config.provider, "FAKE"),),
        )
        # Bind all assumptions, including actual artificial price/FX conventions.
        self.binding = digest(
            {
                "version": "paper-execution-v1",
                "scheduler": scheduler.binding,
                "policy": dump(self.policy.__dict__),
                "risk": dump(asdict(self.risk_policy)),
                "strategy": dump(asdict(self.strategy.config)),
                "costs": dump([asdict(s) for s in costs.schedules]),
                "instrument": dump(self.instrument),
                "capital": "1000.00",
                "fx": "explicit-observation-input",
            }
        )
        initialize(self.store, scheduler.stream_id, self.binding, self.store.clock())

    def _quote(self, observation, now):
        fee = self.costs.schedule(self.instrument, now)
        if fee.spread_bps >= 10000 or fee.slippage_bps >= 10000:
            raise AtlasError("Inconsistent paper spread/slippage assumptions.")
        half = observation.price * fee.spread_bps / Decimal("20000")
        tick = self.instrument.tick_size
        bid = floor_step(observation.price - half, tick)
        ask = ((observation.price + half) / tick).to_integral_value(rounding=ROUND_CEILING) * tick
        volume = floor_step(
            observation.volume * self.policy.participation, self.instrument.quantity_step
        )
        return MarketQuote(
            observation.instrument_id,
            "artificial-paper-spread-v1",
            QuoteUnit.USD,
            observation.event_at,
            observation.available_at,
            observation.received_at,
            bid,
            ask,
            volume,
            volume,
        )

    def _rate(self, rate, now):
        if not isinstance(rate, FxRate) or rate.source != "ARTIFICIAL_USD_GBP":
            raise AtlasError("MISSING_OR_NONMOCK_FX")
        validate_fx(rate, self.instrument, now)
        if rate.source_currency != Currency.USD or now - rate.event_time > self.policy.fx_max_age:
            raise AtlasError("FX_UNAVAILABLE_OR_STALE")

    def _bars(self, saved, observation, session, now):
        """Minute-grid mock samples only. Missing grid points never produce a complete bar."""
        if saved["day"] != session.local_date:
            saved.update(day=session.local_date, bars=(), samples=())
        samples = list(saved["samples"])
        bars = list(saved["bars"])
        event = observation.event_at
        if samples:
            start = samples[0][0]
            end = start + timedelta(minutes=5)
            if event >= end:
                expected = tuple(start + timedelta(minutes=i) for i in range(5))
                if tuple(sample[0] for sample in samples) == expected:
                    prices = [sample[1] for sample in samples]
                    bars.append(
                        MarketBar(
                            self.instrument.instrument_id,
                            "ARTIFICIAL_MINUTE_GRID",
                            QuoteUnit.USD,
                            start,
                            end,
                            end,
                            now,
                            now,
                            prices[0],
                            max(prices),
                            min(prices),
                            prices[-1],
                            sum((sample[2] for sample in samples), ZERO),
                        )
                    )
                samples = []
        offset = (event - session.open).total_seconds()
        if offset % 60 == 0:
            start = session.open + timedelta(seconds=int(offset // 300) * 300)
            if samples or event == start:
                samples.append((event, observation.price, observation.volume))
        saved["samples"], saved["bars"] = tuple(samples), tuple(bars)
        return tuple(bars)

    @staticmethod
    def _put_order(db, order, *, new=False):
        if new:
            db.execute(
                "INSERT INTO execution_orders VALUES(?,?,?)",
                (order["id"], order["signal"].intent_id, dump(order)),
            )
        else:
            db.execute(
                "UPDATE execution_orders SET content=? WHERE id=?", (dump(order), order["id"])
            )

    def _create(self, db, signal, side, observation, now, quantity=None):
        key = digest(["paper-order-v1", self.scheduler.stream_id, signal.intent_id, side])
        order = {
            "id": key,
            "signal": signal,
            "side": side,
            "status": "PENDING",
            "created_at": now,
            "source_event": observation.event_at,
            "source_observation": observation.identity,
            "quantity": quantity,
            "remaining": quantity,
            "approved": False,
            "reason": None,
        }
        existing = db.execute("SELECT content FROM execution_orders WHERE id=?", (key,)).fetchone()
        if existing:
            prior = decode(json.loads(existing[0]))
            # A safety cancellation must not strand held exposure forever. Re-arm the
            # same disposal identity on fresh trigger evidence; preserve filled quantity.
            # Creation time resets, so this observation still cannot fill the disposal.
            if side == "sell" and prior["status"] == "CANCELLED":
                if prior["remaining"] != quantity:
                    raise AtlasError("Exit remainder does not match held position.")
                prior.update(
                    status="PENDING",
                    reason=None,
                    approved=False,
                    created_at=now,
                    source_event=observation.event_at,
                    source_observation=observation.identity,
                )
                self._put_order(db, prior)
            return  # Stable key plus UNIQUE index is the idempotency boundary.
        self._put_order(db, order, new=True)

    def _cancel(self, db, ledger, order, now, reason):
        if order["side"] == "buy":
            record(db, ledger, "cancel", order["signal"].intent_id, now)
        order.update(status="CANCELLED", reason=reason)
        self._put_order(db, order)

    def _controls(self, db, ledger, saved, now, session, *, freeze=False):
        for order in rows(db, "execution_orders"):
            if order["status"] != "PENDING":
                continue
            reason = None
            if saved["kill"]:
                reason = "KILL_SWITCH"
            elif freeze:
                reason = "DATA_OR_MARKET_CONTROL"
            elif session is None or not session.contains(now):
                reason = "MARKET_CLOSED"
            elif order["created_at"].date() != now.date():
                reason = "SESSION_EXPIRED"
            elif order["side"] == "buy" and now >= order["signal"].expires_at:
                reason = "SIGNAL_EXPIRED"
            if reason:
                self._cancel(db, ledger, order, now, reason)

    def _execute(self, db, ledger, saved, observation, quote, rate, session, now):
        risk = RiskEngine(ledger, self.costs, self.scheduler.calendar, self.risk_policy)
        risk.kill_switch = saved["kill"]
        available = quote.ask_size
        for order in sorted(rows(db, "execution_orders"), key=lambda o: (o["created_at"], o["id"])):
            if order["status"] != "PENDING":
                continue
            if (
                observation.identity == order["source_observation"]
                or observation.event_at <= order["source_event"]
                or observation.event_at <= order["created_at"]
                or observation.available_at <= order["created_at"]
                or now < order["created_at"] + self.policy.latency
            ):
                continue
            if available < self.instrument.minimum_quantity:
                order["reason"] = "INSUFFICIENT_LIQUIDITY"
                self._put_order(db, order)
                continue
            signal = order["signal"]
            if order["side"] == "buy" and not order["approved"]:
                qty = risk.size(signal, self.instrument, quote, rate, now)
                if qty == ZERO:
                    self._cancel(db, ledger, order, now, "NO_FEASIBLE_RISK_SIZE")
                    continue
                # The existing gate itself recomputes and reserves; never accept supplied approval.
                decision = risk.authorize(
                    TradeIntent(signal, qty),
                    self.instrument,
                    quote,
                    saved["bars"],
                    session,
                    rate,
                    now,
                )
                saved["decisions"].append(decision)
                if decision.status != DecisionStatus.APPROVED:
                    self._cancel(db, ledger, order, now, ",".join(decision.reasons))
                    continue
                order["approved"] = True
                estimate = self.costs.estimate(
                    self.instrument,
                    qty,
                    quote.ask,
                    signal.stop,
                    rate,
                    now,
                    entry_basis=PriceBasis.EXECUTABLE,
                )
                journal_applied(
                    db,
                    "reserve",
                    self.instrument,
                    signal,
                    qty,
                    decision.capital_usage_gbp,
                    decision.planned_risk_gbp,
                    decision.stressed_risk_gbp,
                    now,
                    decision.snapshot_version,
                    entry_price_ceiling=quote.ask,
                    fx_ceiling=rate.gbp_per_unit,
                    entry_fee_budget_gbp=estimate.entry_cost_gbp,
                )
                order.update(quantity=qty, remaining=qty, approved=True)
            qty = min(order["remaining"], available)
            price = quote.ask if order["side"] == "buy" else quote.bid
            estimate = self.costs.estimate(
                self.instrument,
                qty,
                price,
                price,
                rate,
                now,
                entry_basis=PriceBasis.EXECUTABLE,
                exit_basis=PriceBasis.EXECUTABLE,
            )
            side_label = "entry" if order["side"] == "buy" else "exit"
            components = tuple((k, v) for k, v in estimate.components if k.startswith(side_label))
            fees = sum((amount for _, amount in components), ZERO)
            fill_id = digest(["paper-fill-v1", order["id"], observation.identity])
            if order["side"] == "sell":
                decision = risk.evaluate_exit(
                    order["id"], self.instrument, qty, quote, session, rate, now
                )
                saved["decisions"].append(decision)
                if decision.status != DecisionStatus.APPROVED:
                    self._cancel(db, ledger, order, now, ",".join(decision.reasons))
                    continue
                order["approved"] = True
            # Existing accounting ceilings remain mandatory, including partial-fill fee budgets.
            # These three entry errors occur before any accounting mutation. Cancel the
            # unfilled remainder rather than relaxing ceilings or blocking the inbox forever.
            if order["side"] == "buy":
                try:
                    record(
                        db,
                        ledger,
                        "entry_fill",
                        signal.intent_id,
                        fill_id,
                        qty,
                        price,
                        rate,
                        fees,
                        now,
                        cost_components=components,
                    )
                except AtlasError as error:
                    if str(error) not in {
                        "FILL_REQUIRES_NEW_RISK_CHECK",
                        "FILL_EXCEEDS_RESERVATION",
                        "NO_ACTIVE_RESERVATION",
                    }:
                        raise
                    self._cancel(db, ledger, order, now, str(error))
                    continue
            else:
                record(
                    db,
                    ledger,
                    "exit_fill",
                    self.instrument.instrument_id,
                    fill_id,
                    qty,
                    price,
                    rate,
                    fees,
                    now,
                    cost_components=components,
                )
            fill = ledger.history[-1]
            document = {
                "fill_id": fill_id,
                "order_id": order["id"],
                "side": fill.side,
                "quantity": qty,
                "price_usd": price,
                "mid_usd": observation.price,
                "spread_cost_gbp": abs(price - observation.price) * qty * rate.gbp_per_unit,
                "fx_gbp_per_usd": rate.gbp_per_unit,
                "fees_gbp": fees,
                "cost_components": components,
                "timestamp": now,
                "realised_pnl_gbp": fill.realized_pnl_gbp,
                "gross_pnl_gbp": fill.gross_pnl_gbp,
                "observation_id": observation.identity,
                "risk_approved": True,
                "simulated": True,
            }
            db.execute(
                "INSERT INTO execution_fills VALUES(?,?,?,?)",
                (fill_id, order["id"], observation.identity, dump(document)),
            )
            available -= qty
            order["remaining"] -= qty
            order["status"] = "FILLED" if order["remaining"] == 0 else "PENDING"
            order["reason"] = None
            self._put_order(db, order)

    def _finish(self, db, ledger, saved, now):
        saved["last_time"] = now
        saved["snapshot"] = record(db, ledger, "snapshot", now)
        if not saved["snapshot"].consistent or not ledger.reconcile():
            raise AtlasError("Paper account reconciliation failure.")
        write_state(db, saved)
        save_seal(db)

    def process(self, lease, rate: FxRate | None, *, fault=lambda stage: None):
        """Trading ack is independent of scheduler cursor. Retry after uncertain commits is safe."""
        with self.store.connect(write=True) as db:
            owner, now = self.store.owned(db, lease)
            if (
                lease.stream_id != self.scheduler.stream_id
                or owner["binding"] != self.scheduler.binding
            ):
                raise AtlasError("Wrong execution stream/binding.")
            ledger, saved = load(db)
            if saved["binding"] != self.binding:
                raise AtlasError("Paper execution binding mismatch.")
            if now < saved["last_time"]:
                raise AtlasError("Paper trading clock regressed.")
            row = db.execute(
                "SELECT content FROM paper_results WHERE stream_id=? AND ordinal=?",
                (lease.stream_id, saved["ack"] + 1),
            ).fetchone()
            session = self.scheduler._session(now)
            self._controls(db, ledger, saved, now, session)
            outcome = "NO_TRADING_INPUT"
            if not row and saved.get("last_eligible_at") is not None:
                if (now - saved["last_eligible_at"]).total_seconds() > (
                    self.scheduler.policy.max_receipt_age_seconds
                ):
                    self._controls(db, ledger, saved, now, session, freeze=True)
                    outcome = "STALE_DATA"
            if row:
                source = json.loads(row[0])
                outcome = source["outcome"]
                if outcome == "ELIGIBLE" and not saved["kill"]:
                    observation = Observation.model_validate(source["observation"])
                    check = assess(
                        observation,
                        self.scheduler.config,
                        self.scheduler.policy,
                        self.scheduler.entitlement,
                        self.scheduler.calendar,
                        now,
                    )
                    if check.eligible and not owner["halted"] and owner["connected"]:
                        try:
                            self._rate(rate, now)
                            quote = self._quote(observation, now)
                        except AtlasError:
                            outcome = "EXECUTION_INPUTS_REJECTED"
                            self._controls(db, ledger, saved, now, session, freeze=True)
                        else:
                            saved["last_eligible_at"] = observation.received_at
                            record(
                                db,
                                ledger,
                                "mark",
                                self.instrument.instrument_id,
                                quote.bid,
                                rate,
                                now,
                            )
                            self._execute(db, ledger, saved, observation, quote, rate, session, now)
                            # Entry accounting initially carries the new lot at execution price.
                            # Persist the known bid mark for the entire remaining position.
                            record(
                                db,
                                ledger,
                                "mark",
                                self.instrument.instrument_id,
                                quote.bid,
                                rate,
                                now,
                            )
                            bars = self._bars(saved, observation, session, now)
                            positions = ledger.snapshot(now).positions
                            if positions:
                                position = positions[0]
                                signal = position.signal
                                if (
                                    quote.bid <= signal.stop
                                    or quote.bid >= signal.target
                                    or now >= signal.time_exit
                                ):
                                    for order in rows(db, "execution_orders"):
                                        if order["status"] == "PENDING" and order["side"] == "buy":
                                            self._cancel(db, ledger, order, now, "EXIT_TRIGGERED")
                                    self._create(
                                        db, signal, "sell", observation, now, position.quantity
                                    )
                            else:
                                result = self.strategy.evaluate(self.instrument, bars, session, now)
                                if result.signal:
                                    signal = result.signal
                                    if not db.execute(
                                        "SELECT 1 FROM execution_signals WHERE id=?",
                                        (signal.intent_id,),
                                    ).fetchone():
                                        db.execute(
                                            "INSERT INTO execution_signals VALUES(?,?)",
                                            (signal.intent_id, dump(signal)),
                                        )
                                        self._create(db, signal, "buy", observation, now)
                    else:
                        outcome = "TRADING_DATA_REJECTED"
                        self._controls(db, ledger, saved, now, session, freeze=True)
                elif outcome != "ELIGIBLE":
                    # Duplicate redelivery is harmless; actual data/control failures cancel risk.
                    if source.get("reasons") != ["OUT_OF_ORDER", "DUPLICATE_OBSERVATION"]:
                        self._controls(db, ledger, saved, now, session, freeze=True)
                saved["ack"] += 1
                db.execute(
                    "INSERT INTO execution_inbox VALUES(?,?,?)",
                    (saved["ack"], digest(source), outcome),
                )
            saved["status"] = "KILLED" if saved["kill"] else outcome
            self._finish(db, ledger, saved, now)
            self.store.owned(db, lease)  # Fence/lease rechecked immediately before commit.
            fault("before_commit")
        fault("after_commit")
        return outcome

    def kill(self):
        """Operator control commits independently; subsequent workers cannot forget it."""
        with self.store.connect(write=True) as db:
            ledger, saved = load(db)
            now = self.store.now()
            if now < saved["last_time"]:
                raise AtlasError("Paper kill clock regressed.")
            saved.update(kill=True, status="KILLED")
            self._controls(db, ledger, saved, now, None)
            self._finish(db, ledger, saved, now)
