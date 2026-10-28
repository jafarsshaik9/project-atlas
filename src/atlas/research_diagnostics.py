"""Read-only ORB explanations and report projections over the existing replay engine."""

import argparse
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from atlas.backtest import jsonable, run_backtest
from atlas.config import AtlasError
from atlas.historical_backtest import HistoricalReplayDataset
from atlas.market_data.contracts import digest
from atlas.market_data.store import HistoricalStore
from atlas.multi_session_research import WalkForward, prepare, run_multi_session, walk_forward
from atlas.strategy import ORBStrategy


def replay_dataset(prepared):
    d = prepared
    return HistoricalReplayDataset(
        "ARTIFICIAL_OR_HISTORICAL_REPLAY_" + d["ref"].dataset_id,
        d["instrument"],
        d["session"],
        d["bars"],
        d["quotes"],
        d["rate"],
        (
            ("dataset_id", d["ref"].dataset_id),
            ("origin", d["manifest"]["origin"]),
            ("availability", d["ref"].config.availability_mode.value),
        ),
        d["ref"].dataset_id,
    )


def diagnose(ref, *, allow_artificial=False):
    d = prepare(ref, allow_artificial=allow_artificial)
    strategy = ORBStrategy(ref.config.orb)
    visible, transitions, captures = [], [], []
    count = ref.config.orb.opening_range_minutes // ref.config.orb.candle_minutes
    for bar in sorted(d["bars"], key=lambda b: (b.visible_time, b.start)):
        visible.append(bar)
        result = strategy.evaluate(d["instrument"], tuple(visible), d["session"], bar.visible_time)
        opening = sorted(visible, key=lambda b: b.start)[:count]
        high = max((b.high for b in opening), default=None)
        low = min((b.low for b in opening), default=None)
        breakout = len(visible) > count and high is not None and bar.close > high
        reasons = result.reasons
        category = (
            "SIGNAL"
            if result.signal
            else "SESSION_INELIGIBLE"
            if "MARKET_CLOSED" in reasons
            else "OPENING_RANGE_FORMING"
            if "OPENING_RANGE_FORMING" in reasons
            else "NO_BREAKOUT"
            if "NO_BREAKOUT" in reasons
            else "BREAKOUT_REJECTED_BY_STRATEGY"
            if breakout
            else "STRATEGY_FILTER_REJECTED"
        )
        transitions.append(
            {
                "at": bar.visible_time.isoformat(),
                "category": category,
                "reasons": list(reasons),
                "range_bars": len(opening),
                "range_complete": len(opening) == count,
                "range_high": high,
                "range_low": low,
                "breakout_observed": breakout,
            }
        )

    def observer(kind, snapshot, value, kill):
        if kind in ("signal.generated", "risk.decided", "fill.confirmed", "risk.breached"):
            captures.append(
                {
                    "kind": kind,
                    "at": snapshot.timestamp,
                    "value": value,
                    "available_cash_gbp": snapshot.available_cash_gbp,
                    "position_count": len(snapshot.positions),
                    "kill_switch": kill,
                }
            )

    result = run_backtest(
        replay_dataset(d),
        ref.config.settings,
        costs=d["costs"],
        orb=ref.config.orb,
        policy=d["policy"],
        calendar=d["calendar"],
        ignore_predecision_quotes=True,
        observer=observer,
    )
    risk_rejections = [
        x for x in captures if x["kind"] == "risk.decided" and x["value"].status.value != "approved"
    ]
    execution = [
        (code, n)
        for code, n in result.quality_events
        if any(word in code for word in ("LIQUIDITY", "FILL", "EXPIRED", "EXIT_PRICE"))
    ]
    risk_ids = {x["value"].intent_id for x in risk_rejections}
    execution_rejections = [
        {"intent_id": intent, "reasons": reasons}
        for intent, reasons in result.rejected_opportunities
        if intent not in risk_ids
    ]
    execution_rejections.extend(
        {"reasons": [code], "count": n}
        for code, n in execution
        if any(word in code for word in ("INSUFFICIENT", "UNLIQUIDATED", "EXPIRED"))
    )
    quality = HistoricalStore(ref.database, read_only=True).inspect(ref.dataset_id)[1]
    categories = Counter(x["category"] for x in transitions)
    if not d["selected_rows"]:
        categories["MISSING_OR_UNAVAILABLE_DATA"] += 1
    if risk_rejections:
        categories["RISK_REJECTION"] += len(risk_rejections)
    if execution:
        categories["SIMULATED_EXECUTION_OUTCOME"] += sum(n for _, n in execution)
    if execution_rejections:
        categories["SIMULATED_EXECUTION_REJECTION"] += len(execution_rejections)
    return jsonable(
        {
            "dataset_id": ref.dataset_id,
            "scope": "INDEPENDENT_PARTITION_PROBE; shared-account outcomes reported separately",
            "symbol": d["instrument"].instrument_id,
            "session": str(d["session"].local_date),
            "origin": d["manifest"]["origin"],
            "availability_mode": ref.config.availability_mode.value,
            "point_in_time_verified": d["verified"],
            "source_rows": d["source_rows"],
            "available_rows": d["selected_rows"],
            "categories": dict(categories),
            "opening_range_and_filters": transitions,
            "risk_and_execution": captures,
            "risk_rejections": risk_rejections,
            "execution_outcomes": execution,
            "execution_rejections": execution_rejections,
            "position_count_limit": "No fixed count; value and concurrent-risk budgets apply",
            "position_value_limit_pct": d["policy"].max_position_pct,
            "concurrent_planned_risk_limit_pct": (
                ref.config.settings.max_concurrent_planned_risk_pct
            ),
            "trade_count": result.simulated_trades,
            "quality_warnings": [f.code for f in quality.findings],
            "policy": "READ_ONLY; unchanged thresholds; available observations only",
        }
    )


def reporting(result, *, partitions=()):
    months = defaultdict(lambda: {"trades": 0, "realised_pnl_gbp": Decimal(0)})
    ticker_counts = defaultdict(set)
    sessions = defaultdict(set)
    closed = {}
    active = {}
    for partition in partitions:
        sessions[(partition["symbol"], partition["session"])]
        ticker_counts[partition["symbol"]]
    for session in result["sessions"]:
        months[session["date"][:7]]
    for fill in result["fills"]:
        symbol = fill["instrument_id"]
        quantity = Decimal(fill["quantity"])
        if fill["side"] == "buy":
            item = active.setdefault(
                symbol, {"key": fill["intent_id"], "quantity": Decimal(0), "pnl": Decimal(0)}
            )
            item["quantity"] += quantity
            continue
        if symbol not in active or active[symbol]["quantity"] < quantity:
            raise AtlasError("Reporting cannot reconcile an exit to a prior entry.")
        item = active[symbol]
        key = item["key"]
        item["quantity"] -= quantity
        item["pnl"] += Decimal(fill["realized_pnl_gbp"])
        month = fill["timestamp"][:7]
        months[month]["realised_pnl_gbp"] += Decimal(fill["realized_pnl_gbp"])
        if item["quantity"] == 0:
            months[month]["trades"] += 1
            closed[key] = item["pnl"]
            ticker_counts[symbol].add(key)
            sessions[(symbol, fill["timestamp"][:10])].add(key)
            del active[symbol]
    if active:
        raise AtlasError("Multi-session reporting requires resolved intraday exposure.")
    return jsonable(
        {
            "label": result["label"],
            "total_trades": len(closed),
            "base_exit_intent_count": result["trade_count"],
            "trade_count_basis": "Completed position round trips; partial exits grouped by entry",
            "wins": sum(p > 0 for p in closed.values()),
            "losses": sum(p < 0 for p in closed.values()),
            "breakeven": sum(p == 0 for p in closed.values()),
            "net_realised_pnl_gbp": result["realised_pnl_gbp"],
            "net_unrealised_pnl_gbp": "0",
            "unrealised_basis": "Engine refuses unresolved intraday exposure",
            "equity_curve": result["equity_curve"],
            "maximum_drawdown_gbp": result["maximum_drawdown_gbp"],
            "by_ticker": {
                key: {
                    **result["per_instrument"].get(key, {"net_pnl_gbp": "0", "fees_gbp": "0"}),
                    "trades": len(ticker_counts[key]),
                }
                for key in sorted(set(result["per_instrument"]) | set(ticker_counts))
            },
            "by_month": dict(months),
            "by_symbol_session": [
                {"symbol": k[0], "session": k[1], "trades": len(v)}
                for k, v in sorted(sessions.items())
            ],
            "availability_modes": result["availability_modes"],
            "point_in_time_verified": result["point_in_time_verified"],
            "assumptions": result["assumptions"],
        }
    )


def main(argv=None):
    from atlas.research_history_cli import load_bundle

    parser = argparse.ArgumentParser(
        description="Read-only ORB diagnostics; no broker or notifications"
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-artificial", action="store_true")
    args = parser.parse_args(argv)
    try:
        refs, candidates, windows = load_bundle(args.bundle)
        result = run_multi_session(refs, allow_artificial=args.allow_artificial)
        probes = [diagnose(r, allow_artificial=args.allow_artificial) for r in refs]
        intent_context = {}
        for probe in probes:
            for event in probe["risk_and_execution"]:
                if event["kind"] == "signal.generated":
                    intent_context[event["value"]["intent_id"]] = {
                        "symbol": probe["symbol"],
                        "session": probe["session"],
                    }
        for fill in result["fills"]:
            intent_context[fill["intent_id"]] = {
                "symbol": fill["instrument_id"],
                "session": fill["timestamp"][:10],
            }
        value = {
            "base_result": result,
            "reporting": reporting(result, partitions=probes),
            "diagnostics": probes,
            "shared_portfolio": {
                "scope": "ACTUAL_SHARED_ACCOUNT_REPLAY",
                "risk_decisions": [
                    {**decision, **intent_context.get(decision["intent_id"], {})}
                    for decision in result["risk_decisions"]
                ],
                "rejections": result["rejections"],
                "equity_and_available_cash_series": result["equity_curve"],
                "cash_timing": "Post-observation-batch valuations; not pre-decision cash snapshots",
                "starting_capital_gbp": result["initial_capital_gbp"],
                "ending_equity_gbp": result["ending_equity_gbp"],
            },
            "walk_forward": walk_forward(
                refs,
                candidates or [refs[0].config.orb],
                WalkForward(**windows),
                allow_artificial=args.allow_artificial,
            )
            if windows
            else None,
        }
        value["report_checksum"] = digest(value)
        if args.output.suffix != ".json":
            raise AtlasError("Diagnostic reports require a new public JSON file.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
        print(
            json.dumps(
                {
                    "status": "COMPLETE",
                    "output": str(args.output),
                    "trade_count": result["trade_count"],
                    "network_enabled": False,
                }
            )
        )
        return 0
    except (AtlasError, ValueError, TypeError, KeyError, OSError):
        print(json.dumps({"error": "READ_ONLY_DIAGNOSTICS_FAILED"}))
        return 1
