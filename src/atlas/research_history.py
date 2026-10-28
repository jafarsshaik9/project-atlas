"""Adapter from authoritative replay observations to isolated, persisted research runs."""

import hashlib
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from atlas.activity import ActivityEvent, EventType, Valuation, canonical, now_utc
from atlas.backtest import jsonable, run_backtest
from atlas.config import AtlasError
from atlas.costs import validate_fx
from atlas.history import HistoryStore
from atlas.strategy import ORBStrategy
from atlas.synthetic import fixture_calendar


def source_fingerprint() -> str:
    folder = Path(__file__).parent
    names = (
        "backtest",
        "portfolio",
        "risk",
        "domain",
        "costs",
        "sessions",
        "synthetic",
        "strategy",
        "research",
        "activity",
        "history",
        "research_history",
    )
    return hashlib.sha256(
        b"".join((folder / (name + ".py")).read_bytes() for name in names)
    ).hexdigest()


def valuation(snapshot, data, kill_switch) -> dict:
    positions = []
    for position in snapshot.positions:
        rate, reason = data.fx, None
        try:
            if rate is None:
                raise AtlasError("MISSING_FX")
            validate_fx(rate, position.instrument, position.mark_time)
        except AtlasError:
            rate, reason = None, "Validated mark FX is unavailable."
        unit = position.instrument.quote_unit
        factor = unit.major(position.quantity)  # GBX /100; other quote units unchanged.
        native_entry = position.entry_notional_gbp / (factor * rate.gbp_per_unit) if rate else None
        native_mark = position.mark_value_gbp / (factor * rate.gbp_per_unit) if rate else None
        positions.append(
            {
                "instrument_id": position.instrument.instrument_id,
                "venue": position.instrument.venue.value,
                "quantity": position.quantity,
                "quote_unit": unit.value,
                "currency": unit.currency.value,
                "average_entry_native": native_entry,
                "average_carrying_cost_gbp": position.average_cost_gbp,
                "mark_native": native_mark,
                "market_value_gbp": position.mark_value_gbp,
                "unrealized_pnl_gbp": position.mark_value_gbp - position.cost_basis_gbp,
                "planned_risk_gbp": position.planned_risk_gbp,
                "stressed_risk_gbp": position.stressed_risk_gbp,
                "strategy": position.signal.strategy_version,
                "opened_at": position.opened_at,
                "mark_at": position.mark_time,
                "unavailable_reason": reason,
            }
        )
    return Valuation(
        initial_capital_gbp=snapshot.initial_capital_gbp,
        cash_gbp=dict(snapshot.cash_by_currency)["GBP"],
        available_cash_gbp=snapshot.available_cash_gbp,
        reserved_cash_gbp=snapshot.reserved_cash_gbp,
        invested_value_gbp=sum(p.mark_value_gbp for p in snapshot.positions),
        equity_gbp=snapshot.equity_gbp,
        realized_pnl_gbp=snapshot.realized_pnl_gbp,
        unrealized_pnl_gbp=snapshot.unrealized_pnl_gbp,
        daily_pnl_gbp=snapshot.daily_pnl_gbp,
        daily_realized_pnl_gbp=snapshot.daily_realized_pnl_gbp,
        concurrent_planned_risk_gbp=snapshot.concurrent_planned_risk_gbp,
        planned_open_risk_gbp=sum(p.planned_risk_gbp for p in snapshot.positions),
        positions=positions,
        snapshot_version=snapshot.version,
        consistent=snapshot.consistent,
        kill_switch=kill_switch,
    ).model_dump(mode="json")


def recorded_backtest(
    data,
    settings,
    *,
    costs,
    orb,
    policy,
    store=None,
    new_run=False,
    notify=False,
    notify_summary=False,
    calendar=None,
    ignore_predecision_quotes=False,
):
    store = store if store is not None else HistoryStore()
    calendar = calendar if calendar is not None else fixture_calendar()
    identity = jsonable(
        {
            "adapter": "activity-v1",
            "source": source_fingerprint(),
            "dataset": data.checksum,
            "settings": settings,
            "costs": costs.schedules,
            "orb": orb,
            "policy": policy,
            "calendar": calendar.provenance(),
            "nonce": uuid4().hex if new_run else None,
            "ignore_predecision_quotes": ignore_predecision_quotes,
        }
    )
    run_id = hashlib.sha256(canonical(identity).encode()).hexdigest()
    metadata = {
        "dataset": data.name,
        "venue": data.instrument.venue.value,
        "instrument_id": data.instrument.instrument_id,
        "strategy": ORBStrategy.version,
        "environment": "SYNTHETIC",
        "settings": jsonable(settings),
        "source_checksum": identity["source"],
        "policy_version": policy.version,
    }
    owner = uuid4().hex
    claimed = store.claim_run(run_id, metadata, owner)
    observations = []

    def observe(kind, snapshot, value, kill):
        if len(observations) >= 10000:
            raise AtlasError("Synthetic observation bound exceeded.")
        observations.append((kind, snapshot, value, kill, now_utc()))
        store.heartbeat(run_id, owner)

    if notify and os.environ.get("ATLAS_TELEGRAM_ENABLED", "false").lower() != "true":
        if claimed:
            store.fail_run(run_id, owner, data.session.open)
        raise AtlasError("Synthetic notifications require explicit enablement and opt-in.")
    try:
        report = run_backtest(
            data,
            settings,
            costs=costs,
            orb=orb,
            policy=policy,
            calendar=calendar,
            observer=observe if claimed else None,
            ignore_predecision_quotes=ignore_predecision_quotes,
        )
        if not claimed:
            return report, run_id
        events = []
        previous = None

        def add(kind, at, payload, processed=None):
            nonlocal previous
            event = ActivityEvent.create(
                run_id=run_id,
                event_type=kind,
                ordinal=len(events),
                at=at,
                payload=payload,
                cause=previous,
                strategy=ORBStrategy.version,
                instrument_id=data.instrument.instrument_id,
                processed_at=processed,
            )
            events.append(event)
            previous = event.event_id

        run_payload = {
            "dataset": data.name,
            "venue": data.instrument.venue.value,
            "dataset_checksum": data.checksum,
            "configuration_checksum": report.configuration_checksum,
            "initial_capital_gbp": settings.starting_capital_gbp,
        }
        add(EventType.RUN_STARTED, data.session.open, run_payload, observations[0][-1])
        quantities = {}
        planned_by_intent = {}
        prior_positions = {}
        peak = settings.starting_capital_gbp
        for kind, snapshot, value, kill, processed in observations:
            at = snapshot.timestamp
            if kind == "signal.generated":
                add(
                    EventType.SIGNAL,
                    at,
                    {
                        "intent_id": value.intent_id,
                        "side": value.side,
                        "entry": value.entry,
                        "stop": value.stop,
                        "target": value.target,
                        "expires_at": value.expires_at,
                    },
                    processed,
                )
            elif kind == "risk.decided":
                payload = asdict(value)
                payload.pop("timestamp")
                quantities[value.intent_id] = value.proposed_quantity
                planned_by_intent[value.intent_id] = value.planned_risk_gbp
                add(
                    EventType.RISK_ACCEPTED
                    if value.status.value == "approved"
                    else EventType.RISK_REJECTED,
                    at,
                    payload,
                    processed,
                )
            elif kind == "intent.created":
                add(
                    EventType.INTENT,
                    at,
                    {"intent_id": value.signal.intent_id, "quantity": value.quantity},
                    processed,
                )
            elif kind == "fill.confirmed":
                position = next(
                    (
                        p
                        for p in snapshot.positions
                        if p.instrument.instrument_id == value.instrument_id
                    ),
                    None,
                )
                partial = (
                    value.side == "buy" and value.quantity < quantities[value.intent_id]
                ) or (value.side == "sell" and position is not None)
                unit = data.instrument.quote_unit
                add(
                    EventType.PARTIAL if partial else EventType.FILL,
                    at,
                    {
                        "fill_id": value.fill_id,
                        "intent_id": value.intent_id,
                        "side": value.side,
                        "quantity": value.quantity,
                        "price": value.price,
                        "quote_unit": unit.value,
                        "currency": unit.currency.value,
                        "venue": data.instrument.venue.value,
                        "fx_gbp_per_unit": value.fx_rate,
                        "gross_gbp": unit.major(value.price) * value.quantity * value.fx_rate,
                        "fees_gbp": value.fees_gbp,
                        "realized_pnl_gbp": value.realized_pnl_gbp
                        if value.side == "sell"
                        else None,
                        "components": value.cost_components,
                        "partial": partial,
                    },
                    processed,
                )
                if (
                    value.side == "sell"
                    and -value.realized_pnl_gbp > planned_by_intent[value.intent_id]
                ):
                    add(
                        EventType.BREACH,
                        at,
                        {
                            "code": "LOSS_EXCEEDED_PLANNED_RISK",
                            "reasons": ("GAP_OR_COST_STRESS",),
                            "planned_risk_gbp": planned_by_intent[value.intent_id],
                            "actual_loss_gbp": -value.realized_pnl_gbp,
                        },
                        processed,
                    )
                before = prior_positions.get(value.instrument_id, 0)
                after = position.quantity if position else 0
                change = (
                    EventType.OPENED
                    if before == 0
                    else EventType.CLOSED
                    if after == 0
                    else EventType.INCREASED
                    if after > before
                    else EventType.REDUCED
                )
                add(change, at, {"quantity_before": before, "quantity_after": after}, processed)
            elif kind == "risk.breached":
                add(EventType.BREACH, at, {"code": "RISK_THRESHOLD", "reasons": value}, processed)
            peak = max(peak, snapshot.equity_gbp)
            add(
                EventType.VALUATION,
                at,
                {
                    **valuation(snapshot, data, kill),
                    "drawdown_gbp": str(peak - snapshot.equity_gbp),
                },
                processed,
            )
            prior_positions = {p.instrument.instrument_id: p.quantity for p in snapshot.positions}
        add(
            EventType.RUN_COMPLETED,
            observations[-1][1].timestamp,
            {
                **run_payload,
                "ending_equity_gbp": report.ending_equity_gbp,
                "net_pnl_gbp": report.net_pnl_gbp,
                "completed_trades": sum(e.event_type == EventType.CLOSED for e in events),
            },
        )
        store.complete_run(
            run_id, owner, events, report.to_dict(), notify=notify, notify_summary=notify_summary
        )
        return report, run_id
    except AtlasError:
        if claimed:
            store.fail_run(run_id, owner, data.session.open, notify=notify)
        raise
    except (ValueError, OSError):
        if claimed:
            store.fail_run(run_id, owner, data.session.open, notify=notify)
        raise AtlasError(
            "Activity validation/persistence failed; completed history was not committed."
        ) from None
