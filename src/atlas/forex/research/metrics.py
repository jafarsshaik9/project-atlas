"""Complete-case metrics and calendar-week uncertainty; never impute unknown P/L."""

from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, localcontext

from atlas.forex.execution.contracts import arithmetic_context
from atlas.market_data.contracts import digest

D = Decimal
ZERO = D(0)


def summarize(rows, split, design):
    with localcontext(arithmetic_context()):
        selected = [r for r in rows if split == "all" or r["split"] == split]
        trades = [
            (r, r["execution"]["trade"])
            for r in selected
            if r.get("execution") and r["execution"]["trade"]
        ]
        values = [D(t["net_gbp"]) for _, t in trades]
        gains, losses = (
            sum((v for v in values if v > 0), ZERO),
            -sum((v for v in values if v < 0), ZERO),
        )
        net = sum(values, ZERO)
        invalid = sum(r.get("execution", {}).get("invalidated", 0) for r in selected)
        entries = sum(r.get("execution", {}).get("entries", 0) for r in selected)
        unknown = any(
            r["status"]
            in ("INVALID_UNRESOLVED_PATH", "BLOCKED_UNKNOWN_CAPITAL", "PROCESSING_FAILURE")
            for r in selected
        )
        reasons = Counter()
        for r in selected:
            reasons.update(r.get("reason_counts", {}))
            reasons.update(r.get("execution", {}).get("execution_reason_counts", {}))
        peak = D(selected[0]["starting_cash"]) if selected else D(1000)
        maximum_dd, losing_run, longest_loss_run = ZERO, 0, 0
        for r in selected:
            for _, text in r.get("execution", {}).get("curve", []):
                mark = D(text)
                maximum_dd, peak = max(maximum_dd, peak - mark), max(peak, mark)
        daily = defaultdict(lambda: ZERO)
        weekly = defaultdict(lambda: [ZERO, 0])
        for r in selected:
            if r["status"] == "VALID_ASSUMED_EXECUTION":
                key = date.fromisoformat(r["day"]).isocalendar()[:2]
                weekly[key]
        holding = []
        for r, t in trades:
            pnl = D(t["net_gbp"])
            losing_run = losing_run + 1 if pnl < 0 else 0
            longest_loss_run = max(longest_loss_run, losing_run)
            daily[r["day"]] += pnl
            week = date.fromisoformat(r["day"]).isocalendar()[:2]
            weekly[week][0] += pnl
            weekly[week][1] += 1
            holding.append(
                D(
                    str(
                        (
                            datetime.fromisoformat(t["exited_at"])
                            - datetime.fromisoformat(t["position"]["opened_at"])
                        ).total_seconds()
                        / 60
                    )
                )
            )
        uncertainty = None
        if (
            len(trades) >= design["uncertainty"]["minimum_trades"]
            and len(weekly) >= design["uncertainty"]["minimum_weeks"]
        ):
            mean, n, g = net / len(trades), D(len(trades)), D(len(weekly))
            residuals = sum(((p - mean * count) ** 2 for p, count in weekly.values()), ZERO)
            se = (g / (g - 1) * residuals / (n * n)).sqrt()
            uncertainty = {
                "method": "CALENDAR_WEEK_CLUSTER_RATIO_SANDWICH_NORMAL_95",
                "weeks": len(weekly),
                "standard_error": str(se),
                "lower": str(mean - D("1.96") * se),
                "upper": str(mean + D("1.96") * se),
                "multiplicity_adjusted": False,
            }
        top5 = sum(sorted((v for v in values if v > 0), reverse=True)[:5], ZERO)
        bridges = [r["execution"]["cost_bridge"] for r, _ in trades]
        gross = sum((D(b["gross_model_pnl_gbp"]) for b in bridges), ZERO)
        costs = sum((D(b["total_modeled_costs_gbp"]) for b in bridges), ZERO)
        if gross - costs != net:
            raise ValueError("Gross/cost/net research bridge failed")
        winning = [v for v in values if v > 0]
        losing = [v for v in values if v < 0]
        result = {
            "sessions": len(selected),
            "eligible_sessions": sum(r["status"] != "EXCLUDED_SESSION" for r in selected),
            "excluded_sessions": sum(r["status"] == "EXCLUDED_SESSION" for r in selected),
            "blocked_sessions": sum(r["status"].startswith("BLOCKED") for r in selected),
            "signals_generated": sum(
                r.get("execution", {}).get("signals", r.get("prospective_signals", 0))
                for r in selected
            ),
            "orders_rejected": sum(r.get("execution", {}).get("rejected", 0) for r in selected),
            "trade_count": len(trades),
            "gross_model_pnl_gbp": str(gross),
            "total_modeled_costs_gbp": str(costs),
            "commission_gbp": str(sum((D(b["commission_gbp"]) for b in bridges), ZERO)),
            "price_pnl_before_commission_gbp": str(
                sum((D(b["price_pnl_before_commission_gbp"]) for b in bridges), ZERO)
            ),
            "complete_case_net_pnl_gbp": str(net),
            "portfolio_net_pnl_gbp": None if unknown else str(net),
            "net_return_on_initial_equity": None if unknown else str(net / D(1000)),
            "win_rate": str(D(len(winning)) / len(values)) if values else None,
            "average_winner_gbp": str(gains / len(winning)) if winning else None,
            "average_loser_gbp": str(-losses / len(losing)) if losing else None,
            "expectancy_gbp": str(net / len(values)) if values else None,
            "profit_factor": str(gains / losses) if losses else "INFINITY" if gains else None,
            "maximum_marked_drawdown_gbp": str(maximum_dd),
            "drawdown_fraction_of_initial": str(maximum_dd / D(1000)),
            "maximum_consecutive_losses": longest_loss_run,
            "average_holding_minutes": str(sum(holding, ZERO) / len(holding)) if holding else None,
            "exposure_minutes": str(sum(holding, ZERO)),
            "best_day": max(daily, key=daily.get) if daily else None,
            "best_day_pnl_gbp": str(max(daily.values())) if daily else None,
            "worst_day": min(daily, key=daily.get) if daily else None,
            "worst_day_pnl_gbp": str(min(daily.values())) if daily else None,
            "top_five_winning_trades_gbp": str(top5),
            "net_without_top_five_gbp": str(net - top5),
            "top_five_fraction_of_winning_pnl": str(top5 / gains) if gains else None,
            "invalidated_positions": invalid,
            "unresolved_positions": invalid,
            "invalidated_position_rate": str(D(invalid) / entries) if entries else "0",
            "capital_path_unknown": unknown,
            "risk_limit_failure": any(r.get("risk_failure", False) for r in selected),
            "reason_counts": dict(sorted(reasons.items())),
            "missing_pair_minutes": sum(
                r.get("quality", {}).get("missing_pair_minutes", 0) for r in selected
            ),
            "conversion_missing_minutes": sum(
                r.get("quality", {}).get("conversion_missing_minutes", 0) for r in selected
            ),
            "closure_restriction_minutes": sum(
                r.get("quality", {}).get("closure_restriction_minutes", 0) for r in selected
            ),
            "extreme_exposed_trades": sum(bool(r.get("extreme_exposures")) for r, _ in trades),
            "extreme_exposed_positive_pnl_gbp": str(
                sum(
                    (max(D(t["net_gbp"]), ZERO) for r, t in trades if r.get("extreme_exposures")),
                    ZERO,
                )
            ),
            "uncertainty": uncertainty,
            "warning": "COMPLETE_CASE_ONLY_UNRESOLVED_LOSSES_NOT_IMPUTED"
            if unknown
            else "ASSUMED_EXECUTION_NOT_BROKER_VERIFIED",
        }
        return result


def classify(cells, pair, session, strategy, design):
    by_cost = {
        c["scenario"]: c
        for c in cells
        if c["pair"] == pair and c["session"] == session and c["strategy"] == strategy
    }
    base = by_cost["C0"]
    if base["status"] == "BLOCKED":
        return "BLOCKED"
    validation, holdout = (base["splits"][s] for s in ("validation", "holdout"))
    if base.get("overall", {}).get("risk_limit_failure"):
        return "REJECTED"
    n = holdout["trade_count"]
    if validation["capital_path_unknown"] or holdout["capital_path_unknown"]:
        return "INCONCLUSIVE"
    sufficient = (
        min(validation["trade_count"], n)
        >= design["classification"]["sufficient_rejection_trades_per_split"]
    )
    if sufficient and D(validation["expectancy_gbp"]) < 0 and D(holdout["expectancy_gbp"]) < 0:
        return "REJECTED"
    apparent_profit = D(holdout["complete_case_net_pnl_gbp"])
    concentrated = (
        apparent_profit <= 0
        or D(holdout["top_five_winning_trades_gbp"]) >= D(".5") * apparent_profit
        or D(holdout["extreme_exposed_positive_pnl_gbp"]) >= D(".5") * apparent_profit
    )
    stressed = all(
        by_cost[c]["status"] != "BLOCKED"
        and not by_cost[c]["splits"]["holdout"]["capital_path_unknown"]
        and by_cost[c]["splits"]["holdout"]["expectancy_gbp"] is not None
        and D(by_cost[c]["splits"]["holdout"]["expectancy_gbp"]) > 0
        for c in ("C1", "C2")
    )
    if (
        n >= 100
        and validation["expectancy_gbp"] is not None
        and D(validation["expectancy_gbp"]) > 0
        and holdout["expectancy_gbp"] is not None
        and D(holdout["expectancy_gbp"]) > 0
        and stressed
        and holdout["profit_factor"] is not None
        and D(holdout["profit_factor"]) > D("1.10")
        and D(holdout["drawdown_fraction_of_initial"]) <= D(".10")
        and D(holdout["invalidated_position_rate"]) < D(".01")
        and not concentrated
    ):
        return "PROMISING"
    return "INCONCLUSIVE"


def sealed_summary(cells, design):
    cohorts = [
        {"pair": p, "session": s, "strategy": t, "classification": classify(cells, p, s, t, design)}
        for p in design["pairs"]
        for s in design["sessions"]
        for t in design["strategies"]
    ]
    result = {
        "schema": "phase6b3-summary-v1",
        "experiment": design["experiment"],
        "planned_active_cells": 180,
        "cells": cells,
        "cohorts": cohorts,
        "classification_counts": dict(Counter(c["classification"] for c in cohorts)),
        "no_trade_baseline": [
            {
                "pair": p,
                "session": s,
                "starting_gbp": "1000",
                "trades": 0,
                "net_pnl_gbp": "0",
                "drawdown_gbp": "0",
                "splits": {
                    split["name"]: {"trades": 0, "net_pnl_gbp": "0", "drawdown_gbp": "0"}
                    for split in design["splits"]
                },
            }
            for p in design["pairs"]
            for s in design["sessions"]
        ],
        "multiple_comparisons": (
            "FULL_180_CELL_FAMILY; NO_HOLDOUT_WINNER_SELECTION; EXPLORATORY_ONLY"
        ),
    }
    return result | {"checksum": digest(result)}
