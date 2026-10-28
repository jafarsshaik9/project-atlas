"""Offline chronological portfolio replay and time-separated strategy evaluation."""

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import groupby

from atlas.backtest import jsonable
from atlas.config import AtlasError
from atlas.costs import validate_fx
from atlas.domain import ZERO, Currency, DecisionStatus, FxRate, PriceBasis, TradeIntent, floor_step
from atlas.historical_backtest import (
    AvailabilityMode,
    EvaluationPlan,
    EvaluationRole,
    HistoricalBacktestConfig,
    _costs,
    _effective_time,
    _execution_quotes,
    _instrument,
    _strategy_bars,
)
from atlas.market_data.calendars import RecordedCalendar
from atlas.market_data.contracts import Adjustment, Availability, DownloadRequest, Origin, digest
from atlas.market_data.store import HistoricalStore
from atlas.portfolio import PortfolioLedger
from atlas.risk import RiskEngine, RiskPolicy
from atlas.strategy import ORBConfig, ORBStrategy


@dataclass(frozen=True)
class SessionRef:
    database: str
    dataset_id: str
    config: HistoricalBacktestConfig


def header(ref):
    store = HistoricalStore(ref.database, read_only=True)
    with store.connect() as db:
        row = db.execute(
            "SELECT manifest,manifest_checksum FROM md_datasets WHERE dataset_id=?",
            (ref.dataset_id,),
        ).fetchone()
    if row is None:
        raise AtlasError("Research dataset not found.")
    manifest = json.loads(row[0])
    if digest(manifest) != row[1] or manifest["state"] != "ACCEPTED":
        raise AtlasError("Research requires intact accepted dataset manifests.")
    request = DownloadRequest.model_validate(manifest["request"])
    calendar = RecordedCalendar(manifest["calendar"])
    sessions = [s for s in calendar.sessions() if s.open < request.end and s.close > request.start]
    if len(sessions) != 1:
        raise AtlasError("Each research partition must contain exactly one recorded session.")
    return manifest, request, sessions[0]


def prepare(ref, *, allow_artificial=False):
    manifest, request, session = header(ref)
    checked, quality, bars = HistoricalStore(ref.database, read_only=True).inspect(ref.dataset_id)
    if manifest != checked or quality.status == "FAIL":
        raise AtlasError("Research source integrity failed.")
    if request.adjustment != Adjustment.RAW or request.instrument.quote_unit.value != "USD":
        raise AtlasError("Multi-session ORB requires unadjusted USD equities.")
    if manifest["origin"] == Origin.FIXTURE and not allow_artificial:
        raise AtlasError("Artificial data requires explicit research fixture opt-in.")
    ref.config.evaluation.validate(session.open)
    # Intraday-only accounting cannot process a share conversion or identity change.
    for action in manifest["actions"]:
        when = datetime.fromisoformat(action["event_at"]) if action["event_at"] else None
        if action["kind"] in ("split", "ticker_change", "delisting") and (
            (when is not None and session.open <= when < session.close)
            or (when is None and action["effective_date"] == str(session.local_date))
        ):
            raise AtlasError(
                "Corporate-action session requires separate review; no automatic adjustment."
            )
    inst = _instrument(request, ref.config.instrument)
    effective = {b.start: _effective_time(b, ref.config) for b in bars}
    selected = tuple(b for b in bars if effective[b.start] is not None)
    strategy_bars = _strategy_bars(selected, effective, inst, session)
    quotes = _execution_quotes(selected, effective, inst, ref.config.execution)
    rate = FxRate(
        Currency.USD,
        ref.config.fx.gbp_per_usd,
        ref.config.fx.event_time,
        ref.config.fx.available_time,
        ref.config.fx.source,
    )
    validate_fx(rate, inst, session.open)
    verified = (
        manifest["origin"] != Origin.FIXTURE
        and ref.config.availability_mode == AvailabilityMode.VERIFIED
        and ref.config.fx.point_in_time_verified
        and bool(selected)
        and len(selected) == len(bars)
        and all(b.availability == Availability.KNOWN for b in selected)
        and manifest["action_coverage"] == "PROVIDER_DECLARED_COMPLETE"
        and all(
            a["available_at"] is not None
            and datetime.fromisoformat(a["available_at"]) <= session.open
            for a in manifest["actions"]
        )
    )
    lag = max((effective[b.start] - b.start for b in selected), default=timedelta(seconds=60))
    return {
        "ref": ref,
        "manifest": manifest,
        "instrument": inst,
        "session": session,
        "bars": strategy_bars,
        "quotes": quotes,
        "rate": rate,
        "verified": verified,
        "source_rows": len(bars),
        "selected_rows": len(selected),
        "policy": RiskPolicy(
            version="historical-multi-risk-v1",
            quote_max_age=max(timedelta(seconds=60), lag + timedelta(seconds=1)),
        ),
        "costs": _costs(ref.config, inst.venue, session.open, session.close),
        "calendar": RecordedCalendar(manifest["calendar"]),
    }


def ordered_catalog(refs):
    if not refs:
        raise AtlasError("At least one immutable dataset is required.")
    catalog = []
    seen = set()
    for ref in refs:
        manifest, request, session = header(ref)
        key = (request.instrument.instrument_id, session.local_date)
        if key in seen:
            raise AtlasError("Duplicate instrument/session research partition.")
        seen.add(key)
        catalog.append(
            (session.local_date, request.instrument.instrument_id, ref, digest(manifest))
        )
    return sorted(catalog, key=lambda row: row[:2])


def run_multi_session(refs, *, orb=None, allow_artificial=False):
    """One GBP ledger across all symbols/days, with deterministic identity-sorted allocation."""
    catalog = ordered_catalog(refs)
    settings = catalog[0][2].config.settings
    if any(row[2].config.settings != settings for row in catalog):
        raise AtlasError("Every partition must share one capital and risk-budget policy.")
    ledger = PortfolioLedger(settings)
    curve, decisions, rejected, sessions_report = [], [], [], []
    peak, maximum_drawdown = settings.starting_capital_gbp, ZERO
    verified_all = True
    origins = set()
    source_rows = selected_rows = 0
    source_proofs = []
    # Only one day's observations reside in the event queue, rather than two years of ticks.
    for day, group in groupby(catalog, key=lambda row: row[0]):
        day_rows = list(group)
        data = [prepare(row[2], allow_artificial=allow_artificial) for row in day_rows]
        for row, item in zip(day_rows, data, strict=True):
            if digest(item["manifest"]) != row[3]:
                raise AtlasError("Dataset changed between planning and replay.")
            source_proofs.append((row[2].dataset_id, row[3]))
        verified_all = verified_all and all(d["verified"] for d in data)
        origins.update(d["manifest"]["origin"] for d in data)
        source_rows += sum(d["source_rows"] for d in data)
        selected_rows += sum(d["selected_rows"] for d in data)
        contexts = {d["instrument"].instrument_id: d for d in data}
        visible, pending, identified, stopped = defaultdict(list), {}, set(), set()
        strategies = {key: ORBStrategy(orb or d["ref"].config.orb) for key, d in contexts.items()}
        events = []
        for key, d in contexts.items():
            events.extend((b.visible_time, 0, key, i, b) for i, b in enumerate(d["bars"]))
            events.extend((q.visible_time, 1, key, i, q) for i, q in enumerate(d["quotes"]))
        events.sort(key=lambda row: row[:4])
        before = ledger.snapshot(min(d["session"].open for d in data))
        for now, simultaneous in groupby(events, key=lambda row: row[0]):
            batch = list(simultaneous)
            quotes = [row for row in batch if row[1] == 1]
            # Mark every currently held symbol before another symbol asks to use shared capital.
            for _, _, key, _, quote in quotes:
                d = contexts[key]
                if d["session"].contains(now):
                    ledger.mark(key, quote.bid, d["rate"], now)
            for _, _, key, ordinal, quote in quotes:
                d = contexts[key]
                position = next(
                    (
                        p
                        for p in ledger.snapshot(now).positions
                        if p.instrument.instrument_id == key
                    ),
                    None,
                )
                if position is None or quote.event_time <= position.opened_at:
                    continue
                if quote.bid <= position.signal.stop:
                    stopped.add(key)
                if not (
                    key in stopped
                    or quote.bid >= position.signal.target
                    or now >= position.signal.time_exit
                ):
                    continue
                qty = floor_step(
                    min(position.quantity, quote.bid_size), d["instrument"].quantity_step
                )
                if qty < d["instrument"].minimum_quantity:
                    continue
                risk = RiskEngine(ledger, d["costs"], d["calendar"], d["policy"])
                exit_id = digest([position.signal.intent_id, "dispose", ordinal])
                decision = risk.evaluate_exit(
                    exit_id, d["instrument"], qty, quote, d["session"], d["rate"], now
                )
                decisions.append(decision)
                if decision.status != DecisionStatus.APPROVED:
                    rejected.append((exit_id, decision.reasons))
                    continue
                cost = d["costs"].estimate(
                    d["instrument"],
                    qty,
                    quote.bid,
                    quote.bid,
                    d["rate"],
                    now,
                    exit_basis=PriceBasis.EXECUTABLE,
                )
                ledger.exit_fill(
                    key,
                    exit_id,
                    qty,
                    quote.bid,
                    d["rate"],
                    cost.exit_cost_gbp,
                    now,
                    cost_components=tuple(
                        (k, v) for k, v in cost.components if k.startswith("exit_")
                    ),
                )
            for _, kind, key, _, bar in batch:
                if kind != 0:
                    continue
                visible[key].append(bar)
                d = contexts[key]
                result = strategies[key].evaluate(
                    d["instrument"], tuple(visible[key]), d["session"], now
                )
                if result.signal and result.signal.intent_id not in identified:
                    identified.add(result.signal.intent_id)
                    pending[key] = result.signal
            for _, _, key, ordinal, quote in quotes:
                signal = pending.get(key)
                if signal is None:
                    continue
                if now >= signal.expires_at:
                    rejected.append((signal.intent_id, ("EXPIRED_NO_ACTIONABLE_PRICE",)))
                    del pending[key]
                    continue
                if quote.event_time <= signal.decision_time:
                    continue
                d = contexts[key]
                risk = RiskEngine(ledger, d["costs"], d["calendar"], d["policy"])
                qty = risk.size(signal, d["instrument"], quote, d["rate"], now)
                if qty == ZERO:
                    decision = risk.evaluate(
                        TradeIntent(signal, d["instrument"].minimum_quantity),
                        d["instrument"],
                        quote,
                        tuple(visible[key]),
                        d["session"],
                        d["rate"],
                        now,
                    )
                    decisions.append(decision)
                    rejected.append(
                        (signal.intent_id, decision.reasons or ("NO_FEASIBLE_QUANTITY",))
                    )
                    del pending[key]
                    continue
                decision = risk.authorize(
                    TradeIntent(signal, qty),
                    d["instrument"],
                    quote,
                    tuple(visible[key]),
                    d["session"],
                    d["rate"],
                    now,
                )
                decisions.append(decision)
                del pending[key]
                if decision.status != DecisionStatus.APPROVED:
                    rejected.append((signal.intent_id, decision.reasons))
                    continue
                filled = floor_step(min(qty, quote.ask_size), d["instrument"].quantity_step)
                if filled >= d["instrument"].minimum_quantity:
                    cost = d["costs"].estimate(
                        d["instrument"],
                        filled,
                        quote.ask,
                        signal.stop,
                        d["rate"],
                        now,
                        entry_basis=PriceBasis.EXECUTABLE,
                    )
                    ledger.entry_fill(
                        signal.intent_id,
                        digest([signal.intent_id, "entry", ordinal]),
                        filled,
                        quote.ask,
                        d["rate"],
                        cost.entry_cost_gbp,
                        now,
                        cost_components=tuple(
                            (k, v) for k, v in cost.components if k.startswith("entry_")
                        ),
                    )
                    ledger.mark(key, quote.bid, d["rate"], now)
                ledger.cancel(signal.intent_id, now)
            snap = ledger.snapshot(now)
            if not snap.consistent:
                raise AtlasError("Multi-session account reconciliation failed.")
            peak = max(peak, snap.equity_gbp)
            maximum_drawdown = max(maximum_drawdown, peak - snap.equity_gbp)
            curve.append((now.isoformat(), snap.equity_gbp, snap.available_cash_gbp))
        end = max([d["session"].close for d in data] + [e[0] for e in events])
        final = ledger.snapshot(end)
        if final.positions or final.pending_intents:
            raise AtlasError(
                "Unresolved intraday exposure: subsequent sessions refused; no fabricated exit."
            )
        sessions_report.append(
            {
                "date": str(day),
                "starting_equity_gbp": before.equity_gbp,
                "ending_equity_gbp": final.equity_gbp,
                "net_pnl_gbp": final.equity_gbp - before.equity_gbp,
                "hypotheses": len(identified),
            }
        )
    exits = defaultdict(lambda: ZERO)
    for fill in ledger.history:
        if fill.side == "sell":
            exits[fill.intent_id] += fill.realized_pnl_gbp
    count = len(exits)
    wins = sum(pnl > ZERO for pnl in exits.values())
    per_instrument = {}
    for fill in ledger.history:
        item = per_instrument.setdefault(
            fill.instrument_id, {"net_pnl_gbp": ZERO, "fees_gbp": ZERO}
        )
        item["fees_gbp"] += fill.fees_gbp
        if fill.side == "sell":
            item["net_pnl_gbp"] += fill.realized_pnl_gbp
    configuration = {
        "engine": "atlas-multi-session-v1",
        "sources": source_proofs,
        "configurations": [r[2].config.content() for r in catalog],
        "orb_override": jsonable(orb),
        "allocation": "CANONICAL_ID_SORTED_SHARED_GBP_CASH",
        "fills": (
            "LATER_MINUTE_OPEN_AT_HISTORICAL_AVAILABILITY; one entry attempt; "
            "partial exits; no OHLC extrema fills"
        ),
        "overnight": "REFUSE_UNRESOLVED_EXPOSURE",
    }
    result = jsonable(
        {
            "configuration": configuration,
            "run_id": digest(configuration),
            "label": "ARTIFICIAL FIXTURES; EXPLORATORY; NOT MARKET PERFORMANCE"
            if Origin.FIXTURE.value in origins
            else "VERIFIED HISTORICAL RESEARCH"
            if verified_all
            else "EXPLORATORY HISTORICAL RESEARCH; NOT POINT-IN-TIME VERIFIED",
            "data_origins": sorted(origins),
            "research_status": "COMPLETED" if selected_rows else "NO_ELIGIBLE_OBSERVATIONS",
            "point_in_time_verified": verified_all,
            "source_rows": source_rows,
            "selected_rows": selected_rows,
            "availability_modes": sorted({r[2].config.availability_mode.value for r in catalog}),
            "initial_capital_gbp": settings.starting_capital_gbp,
            "ending_equity_gbp": final.equity_gbp,
            "net_pnl_gbp": final.equity_gbp - settings.starting_capital_gbp,
            "realised_pnl_gbp": final.realized_pnl_gbp,
            "costs_gbp": ledger.total_fees_gbp,
            "gross_pnl_gbp": final.equity_gbp
            - settings.starting_capital_gbp
            + ledger.total_fees_gbp,
            "fx_conversion_costs_gbp": ledger.fx_conversion_costs_gbp,
            "per_instrument": per_instrument,
            "maximum_drawdown_gbp": maximum_drawdown,
            "trade_count": count,
            "wins": wins,
            "losses": sum(pnl < ZERO for pnl in exits.values()),
            "breakeven_trades": sum(pnl == ZERO for pnl in exits.values()),
            "win_rate": Decimal(wins) / count if count else None,
            "expectancy_gbp": sum(exits.values(), ZERO) / count if count else None,
            "sessions": sessions_report,
            "equity_curve": curve,
            "fills": ledger.history,
            "risk_decisions": decisions,
            "rejections": rejected,
            "reconciled": final.consistent,
            "assumptions": [
                "NO_SURVIVORSHIP_FREE_UNIVERSE_CLAIM",
                "NO_TOTAL_RETURN_CLAIM",
                "REPORTED_BAR_VOLUME_IS_NOT_EXECUTABLE_ORDER_BOOK_LIQUIDITY",
                "ASSUMED_AVAILABILITY_AND_FX_ARE_NOT_POINT_IN_TIME_EVIDENCE",
            ],
        }
    )
    result["result_checksum"] = digest(result)
    return result


@dataclass(frozen=True)
class WalkForward:
    training_sessions: int
    validation_sessions: int
    test_sessions: int
    step_sessions: int

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in self.__dict__.values()):
            raise AtlasError("Walk-forward windows require positive integer session counts.")
        if self.step_sessions < self.test_sessions:
            raise AtlasError("Walk-forward tests must not overlap.")


def walk_forward(refs, candidates, windows, *, allow_artificial=False):
    """Fit/compare on training and validation only; evaluate selected strategy once on test."""
    catalog = ordered_catalog(refs)
    days = sorted({row[0] for row in catalog})
    if not candidates or any(not isinstance(c, ORBConfig) for c in candidates):
        raise AtlasError("Explicit frozen ORB candidates are required.")
    width = windows.training_sessions + windows.validation_sessions + windows.test_sessions
    folds = []
    for start in range(0, len(days) - width + 1, windows.step_sessions):
        train = days[start : start + windows.training_sessions]
        validation = days[start + len(train) : start + len(train) + windows.validation_sessions]
        test = days[start + len(train) + len(validation) : start + width]

        def subset(selected, cutoff=None):
            chosen = [row[2] for row in catalog if row[0] in selected]
            return [
                replace(
                    r,
                    config=replace(
                        r.config,
                        evaluation=EvaluationPlan(
                            EvaluationRole.OUT_OF_SAMPLE if cutoff else EvaluationRole.IN_SAMPLE,
                            cutoff,
                        ),
                    ),
                )
                for r in chosen
            ]

        train_end = max(header(row[2])[2].close for row in catalog if row[0] in train)
        validation_end = max(header(row[2])[2].close for row in catalog if row[0] in validation)
        scores = []
        for index, candidate in enumerate(candidates):
            training = run_multi_session(
                subset(train), orb=candidate, allow_artificial=allow_artificial
            )
            valid = run_multi_session(
                subset(validation, train_end), orb=candidate, allow_artificial=allow_artificial
            )
            scores.append(
                {
                    "candidate": index,
                    "parameters": jsonable(candidate),
                    "training": training,
                    "validation": valid,
                }
            )
        winner = max(
            range(len(scores)), key=lambda i: (Decimal(scores[i]["validation"]["net_pnl_gbp"]), -i)
        )
        tested = run_multi_session(
            subset(test, validation_end), orb=candidates[winner], allow_artificial=allow_artificial
        )
        folds.append(
            {
                "training_dates": list(map(str, train)),
                "validation_dates": list(map(str, validation)),
                "test_dates": list(map(str, test)),
                "selection_cutoff": str(validation_end),
                "selected_candidate": winner,
                "comparisons": scores,
                "test": tested,
            }
        )
    if not folds:
        raise AtlasError("Insufficient distinct sessions for the requested walk-forward windows.")
    result = {
        "schema": "atlas-walk-forward-v1",
        "windows": jsonable(windows),
        "folds": folds,
        "capital_policy": (
            "Independent GBP portfolio per split/fold; no fictitious continuous fold equity"
        ),
        "selection_metric": "Validation net GBP P/L; input-order tie break; test excluded",
        "test_trade_count": sum(f["test"]["trade_count"] for f in folds),
    }
    return result | {"evaluation_id": digest(result)}
