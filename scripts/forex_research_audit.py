"""Independent source/arithmetic acceptance; never rerun holdout strategy selection."""

import hashlib
import json
import sqlite3
import zlib
from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas.forex.collection import CollectionLedger
from atlas.forex.execution.contracts import MINUTE, Intent, arithmetic_context
from atlas.forex.execution.engine import jsonable, replay
from atlas.forex.execution.market import frames_from_partitions
from atlas.forex.models import FxInstrument
from atlas.forex.research.design import ROOT, load_design, run_for, settings_for, split_for
from atlas.forex.research.execution import evaluate_intent
from atlas.forex.research.runner import research_source_hash
from atlas.market_data.contracts import digest

D = Decimal
Q = D(".00000001")
NY = ZoneInfo("America/New_York")


def must(condition, reason):
    if not condition:
        raise AssertionError(reason)


def session_bounds(day, session):
    london = ZoneInfo("Europe/London")
    spans = {
        name: (
            datetime.combine(day, time(8), zone).astimezone(UTC),
            datetime.combine(day, time(17), zone).astimezone(UTC),
        )
        for name, zone in (("LONDON", london), ("NEW_YORK", NY))
    }
    if session == "OVERLAP":
        opening, closing = max(v[0] for v in spans.values()), min(v[1] for v in spans.values())
    else:
        opening, closing = spans[session]
    deadline = min(
        closing - timedelta(minutes=5), datetime.combine(day, time(16, 45), NY).astimezone(UTC)
    )
    return opening, deadline


def conversion_rate(pair, amount, bars, at):
    side = "ask" if amount >= 0 else "bid"
    divisor = getattr(bars["GBP_USD"][at - MINUTE], side).c
    if pair == "USD_JPY":
        divisor *= getattr(bars["USD_JPY"][at - MINUTE], side).c
    return D(1) / divisor


def unresolved_stress_overlay(row, bars, design):
    """Nonfinancial V1 supplement: first intraday resumption quote, never a booked exit."""
    execution = row["execution"]
    position = execution["unresolved_positions"][0]
    pair, session, _, scenario = row["cell"].split("/")
    issue = next(e for e in execution["events"] if e["kind"] == "UNRESOLVED")
    issue_at = datetime.fromisoformat(issue["at"])
    _, flat = session_bounds(issue_at.date(), session)
    result = {
        "version": "UNRESOLVED_STRESS_DIAGNOSTIC_OVERLAY_V1",
        "cell": row["cell"],
        "day": row["day"],
        "original_execution_checksum": execution["checksum"],
        "unknown_path": True,
        "booked_exit": False,
        "portfolio_pnl": None,
    }
    previous = next((e for e in execution["events"] if e["kind"] == "RESUMPTION_STRESS_ONLY"), None)
    if previous:
        result.update(
            observation_at=previous["at"],
            adverse_price=json.loads(previous["details"])["adverse_price"],
            provenance="ORIGINAL_ENGINE_DIAGNOSTIC",
        )
        return result
    for stamp, b in sorted(bars[pair].items()):
        if not issue_at <= stamp < flat or not b.complete or b.bid is None or b.ask is None:
            continue
        if b.bid.c > b.ask.c:
            continue
        multiplier = D(design["costs"][scenario]["spread_multiplier"])
        pip = D(".01") if pair == "USD_JPY" else D(".0001")
        slip = D(design["costs"][scenario]["slippage_pips"]) * pip
        extra = (b.ask.c - b.bid.c) * (multiplier - 1) / 2
        price = (
            b.bid.c - extra - slip
            if position["intent"]["side"] == "long"
            else b.ask.c + extra + slip
        )
        result.update(
            observation_at=(stamp + MINUTE).isoformat(),
            source_candle_start=stamp.isoformat(),
            adverse_price=str(price),
            provenance="READ_ONLY_SOURCE_DIAGNOSTIC_SUPPLEMENT",
        )
        return result
    result["reason"] = "NO_DEFENSIBLE_INTRADAY_RESUMPTION_OBSERVATION"
    return result


def validate_trade(row, bars, meta, design):
    execution = row["execution"]
    t = execution["trade"]
    p = t["position"]
    intent = p["intent"]
    pair, session, strategy, scenario = row["cell"].split("/")
    units, entry, exit_price, stop, target = map(
        D, (intent["units"], p["entry"], t["exit_price"], intent["stop"], intent["target"])
    )
    direction = D(1) if intent["side"] == "long" else D(-1)
    signal, cutoff, eligible, opened, closed = map(
        datetime.fromisoformat,
        (
            intent["signal_at"],
            intent["information_cutoff"],
            p["eligible_at"],
            p["opened_at"],
            t["exited_at"],
        ),
    )
    opening, flat = session_bounds(opened.date(), session)
    must(
        signal == cutoff and signal + timedelta(seconds=1) == eligible <= opened == signal + MINUTE,
        "Information/entry chronology",
    )
    must(
        opening < signal <= flat - 2 * MINUTE
        and opened < closed <= min(opened + 60 * MINUTE, flat),
        "Session/holding/overnight boundary",
    )
    must(split_for(signal, design) == split_for(closed, design) == row["split"], "Split leakage")
    pip = D(".01") if pair == "USD_JPY" else D(".0001")
    multiplier = D(design["costs"][scenario]["spread_multiplier"])
    slip = D(design["costs"][scenario]["slippage_pips"]) * pip
    entry_bar = bars[pair][opened - MINUTE]
    extra = (entry_bar.ask.c - entry_bar.bid.c) * (multiplier - 1) / 2
    expected_entry = (
        entry_bar.ask.c + extra + slip if direction > 0 else entry_bar.bid.c - extra - slip
    )
    must(entry == expected_entry, "Entry bid/ask/stress/slippage mismatch")
    fee_at_entry = (units * D(design["commission_gbp_per_unit_per_side"])).quantize(
        Q, rounding=ROUND_CEILING
    )
    expense_rate = conversion_rate(pair, D(-1), bars, opened)
    source_reserve = units * (entry_bar.ask.c + (extra + slip)) * expense_rate
    source_risk = units * (abs(entry - stop) + (extra + slip)) * expense_rate + 2 * fee_at_entry
    must(source_reserve == D(p["reserved_gbp"]), "Source-derived collateral mismatch")
    must(
        source_risk == D(p["planned_risk_gbp"]) and source_risk <= 5, "Source-derived risk mismatch"
    )
    must((entry_bar.ask.c - entry_bar.bid.c) / pip * multiplier <= 5, "Wide spread entry")
    must(
        units >= meta.minimum_units
        and units % meta.unit_step == 0
        and (meta.maximum_units is None or units <= meta.maximum_units),
        "Invalid units",
    )
    must(
        D(p["planned_risk_gbp"]) <= 5
        and D(p["reserved_gbp"]) <= min(D(1000), D(row["starting_cash"])),
        "Entry risk/notional cap",
    )
    must(
        D(row["starting_cash"]) - D(p["entry_fee_gbp"]) - D(p["reserved_gbp"]) >= 0,
        "Borrowed entry capital",
    )
    source_signal = bars[pair][cutoff - MINUTE]
    evidence = datetime.fromisoformat(intent["evidence_start"])
    cursor = evidence
    while cursor < cutoff:
        must(cursor in bars[pair], "Signal has missing source evidence")
        b = bars[pair][cursor]
        must(
            b.complete and b.bid is not None and b.ask is not None and b.mid is not None,
            "Incomplete signal evidence",
        )
        must(
            (b.ask.c - b.bid.c) / pip * multiplier <= 5 and (b.mid.h - b.mid.l) / pip <= 100,
            "Ineligible signal evidence",
        )
        cursor += MINUTE
    if strategy == "ORB":
        range_bars = [bars[pair][opening + i * MINUTE] for i in range(15)]
        low, high = min(b.mid.l for b in range_bars), max(b.mid.h for b in range_bars)
        must(high > low, "Zero opening range")
        must(
            source_signal.mid.c > high if direction > 0 else source_signal.mid.c < low,
            "False ORB signal",
        )
        must(
            stop == (low if direction > 0 else high)
            and target == entry + direction * abs(entry - stop) * D("1.5"),
            "ORB bracket mismatch",
        )
    else:
        must(
            stop == entry * (1 - direction * D(".0015")),
            "Percentage stop is not actual-entry-relative",
        )
        if strategy == "MOMENTUM":
            reference = bars[pair][cutoff - 21 * MINUTE].mid.c
            must(
                source_signal.mid.c >= reference * D("1.001")
                if direction > 0
                else source_signal.mid.c <= reference * D(".999"),
                "Momentum reference/lookback mismatch",
            )
        elif strategy == "MEAN_REVERSION":
            reference = (
                sum((bars[pair][cutoff - (i + 2) * MINUTE].mid.c for i in range(20)), D(0)) / 20
            )
            must(
                source_signal.mid.c <= reference * D(".9985")
                if direction > 0
                else source_signal.mid.c >= reference * D("1.0015"),
                "Mean-reversion reference mismatch",
            )
            must(target == reference, "Mean target not frozen SMA")
        elif strategy == "DIRECTIONAL":
            must(
                source_signal.mid.c > source_signal.mid.o
                if direction > 0
                else source_signal.mid.c < source_signal.mid.o,
                "Directional baseline mismatch",
            )
        if strategy != "MEAN_REVERSION":
            must(target == entry * (1 + direction * D(".00225")), "Percentage target mismatch")
    cursor = opened
    final_expected = None
    while cursor < closed:
        bar = bars[pair].get(cursor)
        must(
            bar is not None and bar.complete and bar.bid is not None and bar.ask is not None,
            "Completed trade bridged a source gap",
        )
        now = cursor + MINUTE
        for needed in ("GBP_USD", "USD_JPY") if pair == "USD_JPY" else ("GBP_USD",):
            must(cursor in bars[needed], "Completed trade bridged a conversion gap")
        part = bar.bid if direction > 0 else bar.ask
        stress = (bar.ask.c - bar.bid.c) * (multiplier - 1) / 2
        shift = -direction * stress
        hit_stop = part.l + shift <= stop if direction > 0 else part.h + shift >= stop
        hit_target = part.h + shift >= target if direction > 0 else part.l + shift <= target
        if hit_stop:
            quote = min(stop, part.o + shift) if direction > 0 else max(stop, part.o + shift)
            final_expected = quote - direction * slip
            must(
                now == closed
                and t["reason"] == ("FX_STOP_FIRST_AMBIGUOUS" if hit_target else "FX_STOP"),
                "Late/optimistic stop exit",
            )
            must(bool(t["ambiguous"]) == bool(hit_target), "Collision flag missing")
        elif hit_target:
            final_expected = target - direction * slip
            must(
                now == closed and t["reason"] == "FX_TARGET_NO_PRICE_IMPROVEMENT",
                "Late/optimistic target exit",
            )
        elif now == closed:
            final_expected = part.c + shift - direction * slip
            must(
                t["reason"]
                in (
                    "FX_MAX_HOLDING",
                    "FX_MANDATORY_FLAT",
                    "FX_EXPOSURE_LIMIT_EXIT",
                    "FX_DAILY_EQUITY_LOSS_EXIT",
                ),
                "Unknown exit reason",
            )
            if t["reason"] == "FX_MAX_HOLDING":
                must(closed == opened + 60 * MINUTE, "Early holding exit")
            if t["reason"] == "FX_MANDATORY_FLAT":
                must(closed == flat, "Wrong flatten deadline")
        if not hit_stop and not hit_target:
            projected = (part.c + shift - direction * slip - entry) * units * direction
            projected_gbp = (projected * conversion_rate(pair, projected, bars, now)).quantize(
                Q, rounding=ROUND_FLOOR
            )
            fee_here = (units * D(design["commission_gbp_per_unit_per_side"])).quantize(
                Q, rounding=ROUND_CEILING
            )
            initial = D(row["starting_cash"])
            equity = initial - D(p["entry_fee_gbp"]) + projected_gbp - fee_here
            notional = units * (bar.ask.c + stress + slip) * conversion_rate(pair, D(-1), bars, now)
            daily_breach = initial - equity >= min(D(10), initial)
            exposure_breach = notional > min(D(1000), initial, equity)
            # The accepted engine handles existing holding/flat deadlines before
            # evaluating marked exposure for positions that remain open.
            if (daily_breach or exposure_breach) and now < min(opened + 60 * MINUTE, flat):
                must(now == closed, "Ignored an earlier marked risk breach")
                must(
                    t["reason"]
                    == ("FX_DAILY_EQUITY_LOSS_EXIT" if daily_breach else "FX_EXPOSURE_LIMIT_EXIT"),
                    "Unjustified risk-exit reason",
                )
            elif now == closed:
                must(
                    t["reason"] in ("FX_MAX_HOLDING", "FX_MANDATORY_FLAT"),
                    "Risk exit lacked a risk breach",
                )
        cursor += MINUTE
    must(exit_price == final_expected, "Exit side, gap or cost mismatch")
    gross_quote = (exit_price - entry) * units * direction
    rate = conversion_rate(pair, gross_quote, bars, closed)
    gross = (gross_quote * rate).quantize(Q, rounding=ROUND_FLOOR)
    fee = (units * D(design["commission_gbp_per_unit_per_side"])).quantize(
        Q, rounding=ROUND_CEILING
    )
    must(
        gross_quote == D(t["gross_quote"]) and rate == D(t["conversion_rate"]),
        "Quote P/L or current directional GBP conversion mismatch",
    )
    must(fee == D(p["entry_fee_gbp"]) == D(t["exit_fee_gbp"]), "Commission mismatch")
    must(gross == D(t["gross_gbp"]) and gross - 2 * fee == D(t["net_gbp"]), "Settled P/L mismatch")
    must(
        D(row["starting_cash"]) + D(t["net_gbp"]) == D(execution["cash_partial"]),
        "Cash/trade reconciliation",
    )
    for stamp_text, equity_text in execution["curve"]:
        stamp = datetime.fromisoformat(stamp_text)
        if stamp < opened:
            equity = D(row["starting_cash"])
        elif stamp >= closed:
            equity = D(execution["cash_partial"])
        else:
            b = bars[pair][stamp - MINUTE]
            stress = (b.ask.c - b.bid.c) * (multiplier - 1) / 2
            liquidating = b.bid.c - stress - slip if direction > 0 else b.ask.c + stress + slip
            marked = (liquidating - entry) * units * direction
            settled = (marked * conversion_rate(pair, marked, bars, stamp)).quantize(
                Q, rounding=ROUND_FLOOR
            )
            equity = D(row["starting_cash"]) - D(p["entry_fee_gbp"]) + settled - fee
        must(equity == D(equity_text), "Incorrect source-derived marked equity")
    bridge = execution["cost_bridge"]
    must(
        D(bridge["gross_model_pnl_gbp"]) - D(bridge["total_modeled_costs_gbp"]) == D(t["net_gbp"]),
        "Modeled cost bridge mismatch",
    )
    return D(t["net_gbp"])


def main():
    design = load_design()
    root = ROOT / "local/verification/phase6b3-v1"
    output = root / "results"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    must(
        digest({k: v for k, v in summary.items() if k != "checksum"}) == summary["checksum"],
        "Summary seal failed",
    )
    must(summary["source_hash"] == research_source_hash(), "Source changed after evaluation")
    must(len(summary["cells"]) == 180, "Missing matrix cells")
    must(sum(c["status"] == "BLOCKED" for c in summary["cells"]) == 36, "C4 status count")
    plan = json.loads((ROOT / "local/oanda/phase6b1/stage-b-plan.json").read_text(encoding="utf-8"))
    ledger = CollectionLedger(plan["output"], read_only=True)
    instruments = tuple(FxInstrument.model_validate(i) for i in plan["instruments"])
    metas = {i.name: i for i in instruments}
    planned_days = {
        k: list(v)
        for k, v in groupby(plan["partitions"], key=lambda r: r["candle_plan"]["start"][:10])
    }
    db = sqlite3.connect((output / "results.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA cache_size=-8192")
    states = {
        c["cell"]: {"cash": D(1000), "known": True, "rows": 0}
        for c in summary["cells"]
        if c["scenario"] != "C4"
    }
    independently = defaultdict(
        lambda: {
            "count": 0,
            "net": D(0),
            "wins": D(0),
            "losses": D(0),
            "peak": None,
            "dd": D(0),
            "invalid": 0,
        }
    )
    reason_counts = Counter()
    probes = []
    trades = 0
    known_unresolved_fees = D(0)
    stress_overlays = []
    with localcontext(arithmetic_context()):
        cursor = db.execute("SELECT day,cell,payload,checksum FROM sessions ORDER BY day,cell")
        for day_index, (day_text, group) in enumerate(groupby(cursor, key=lambda r: r[0]), 1):
            day = datetime.fromisoformat(day_text).date()
            source_rows = planned_days[day_text]
            start = datetime.fromisoformat(source_rows[0]["candle_plan"]["start"])
            end = datetime.fromisoformat(source_rows[0]["candle_plan"]["end"])
            bundles = []
            for planned in source_rows:
                record = ledger.get(planned["key"])
                manifest, bars = ledger.store.inspect(record["dataset"], check_database=False)
                bundles.append((record["dataset"], manifest, record["quality"], bars))
            frames = list(frames_from_partitions(bundles, start, end))
            bars = {name: {} for name in metas}
            for f in frames:
                for o in f.observations:
                    bars[o.instrument][f.start] = o.candle
            frame_map = {f.start: f for f in frames}
            seen = set()
            for _, cell, payload, checksum in group:
                row = json.loads(zlib.decompress(payload))
                must(digest(row) == checksum, "Detailed row seal failed")
                must(cell not in seen, "Duplicate cohort/day")
                seen.add(cell)
                state = states[cell]
                must(
                    D(row["starting_cash"]) == state["cash"],
                    "Capital reset or inconsistent continuation",
                )
                state["rows"] += 1
                if row["status"] == "EXCLUDED_SESSION":
                    must(
                        day.weekday() >= 5
                        or (day.month, day.day) in ((12, 24), (12, 25), (12, 31), (1, 1)),
                        "Unjustified whole-session exclusion",
                    )
                    continue
                if not state["known"]:
                    must(
                        row["status"] == "BLOCKED_UNKNOWN_CAPITAL" and "execution" not in row,
                        "Traded an unknown capital path",
                    )
                    continue
                if row["status"] == "BLOCKED_CAPITAL_EXHAUSTED":
                    must(state["cash"] <= 0, "Unjustified capital exhaustion block")
                    continue
                execution = row["execution"]
                must(
                    digest({k: v for k, v in execution.items() if k != "checksum"})
                    == execution["checksum"],
                    "Execution seal failed",
                )
                pair, session, strategy, scenario = cell.split("/")
                stat = independently[cell, row["split"]]
                if stat["peak"] is None:
                    stat["peak"] = state["cash"]
                for _, equity in execution["curve"]:
                    amount = D(equity)
                    stat["dd"] = max(stat["dd"], stat["peak"] - amount)
                    stat["peak"] = max(stat["peak"], amount)
                if execution["trade"]:
                    value = validate_trade(row, bars, metas[pair], design)
                    stat["count"] += 1
                    stat["net"] += value
                    stat["wins"] += max(value, D(0))
                    stat["losses"] += max(-value, D(0))
                    trades += 1
                elif execution["invalidated"]:
                    must(
                        execution["net_pnl_gbp"] is None and execution["unresolved_positions"],
                        "Unknown position assigned convenient P/L",
                    )
                    fees = sum(
                        (D(p["entry_fee_gbp"]) for p in execution["unresolved_positions"]), D(0)
                    )
                    must(
                        D(execution["cash_partial"]) == state["cash"] - fees,
                        "Unresolved position lost known fee provenance",
                    )
                    known_unresolved_fees += fees
                    stress_overlays.append(unresolved_stress_overlay(row, bars, design))
                    state["known"] = False
                    stat["invalid"] += 1
                else:
                    must(
                        D(execution["cash_partial"]) == state["cash"],
                        "No-trade session changed cash",
                    )
                state["cash"] = D(execution["cash_partial"])
                reason_counts.update(execution["execution_reason_counts"])
                # Fixed development-only reproduction subset. Never regenerate holdout strategies.
                if (
                    day_text in ("2024-10-28", "2024-10-29")
                    and session == "LONDON"
                    and strategy == "ORB"
                    and scenario in ("C0", "C3")
                ):
                    opening, flat = session_bounds(day, session)
                    selected = []
                    tick = opening
                    while tick < flat:
                        from atlas.forex.execution.contracts import Frame

                        selected.append(frame_map.get(tick) or Frame(start=tick, observations=()))
                        tick += MINUTE
                    templates = execution["configuration"]["intents"]
                    intent = Intent.model_validate(templates[0]) if templates else None
                    index = (
                        next(
                            (
                                i
                                for i, f in enumerate(selected)
                                if f.start + MINUTE == intent.signal_at
                            ),
                            None,
                        )
                        if intent
                        else None
                    )
                    cfg = settings_for(scenario, D(row["starting_cash"]), design)
                    run = run_for(day, session, design)
                    repeated = evaluate_intent(
                        cfg, run, instruments, selected, intent, index, row["source_frame_hash"]
                    )
                    must(
                        repeated == execution,
                        "Development reproduction changed complete result/hash",
                    )
                    full = replay(cfg, run, instruments, selected, (intent,) if intent else ())
                    must(
                        jsonable(full.trades[0]) == execution["trade"]
                        if full.trades
                        else execution["trade"] is None,
                        "Full engine economic parity failed",
                    )
                    must(
                        str(full.cash_gbp) == execution["cash_partial"]
                        and full.status == execution["status"],
                        "Full engine cash/status mismatch",
                    )
                    probes.append(
                        {
                            "day": day_text,
                            "cell": cell,
                            "exact_specialization_hash": True,
                            "full_engine_economic_parity": True,
                        }
                    )
            must(len(seen) == 144, "Missing supported cell/day")
            if day_index % 50 == 0:
                print(
                    json.dumps(
                        {"audited_utc_dates": day_index, "independent_trade_checks": trades}
                    ),
                    flush=True,
                )
    for cell in summary["cells"]:
        if cell["scenario"] == "C4":
            continue
        must(states[cell["cell"]]["rows"] == 731, "Session denominator lost dates")
        for split, metric in cell["splits"].items():
            stat = independently[cell["cell"], split]
            must(
                stat["count"] == metric["trade_count"]
                and stat["net"] == D(metric["complete_case_net_pnl_gbp"]),
                "Published trade count/P&L mismatch",
            )
            must(
                stat["invalid"] == metric["invalidated_positions"],
                "Published invalidation mismatch",
            )
            must(stat["dd"] == D(metric["maximum_marked_drawdown_gbp"]), "Marked drawdown mismatch")
            if stat["losses"]:
                with localcontext(arithmetic_context()):
                    must(
                        str(stat["wins"] / stat["losses"]) == metric["profit_factor"],
                        "Profit factor mismatch",
                    )
    result = {
        "decision": "PASS_INDEPENDENT_ARITHMETIC_AND_SOURCE_CHECKS",
        "summary_checksum": summary["checksum"],
        "rows_verified": sum(s["rows"] for s in states.values()),
        "cells": 180,
        "supported_cells": 144,
        "blocked_C4_cells": 36,
        "trade_checks": trades,
        "development_reproduction_probes": probes,
        "holdout_strategy_replays_in_audit": 0,
        "known_fees_on_unresolved_positions_gbp": str(known_unresolved_fees),
        "reason_counts": dict(reason_counts),
        "findings": [
            {
                "severity": "DIAGNOSTIC_COMPLETENESS_ONLY",
                "description": (
                    "Quiescent replay stops on invalidation. Stress diagnostics are supplemented "
                    "without changing trades, P/L, parameters or holdout evaluation."
                ),
                "correction": "VERSIONED_READ_ONLY_NONFINANCIAL_STRESS_OVERLAY",
            }
        ]
        if stress_overlays
        else [],
        "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    result["checksum"] = digest(result)
    with (root / "unresolved-stress-overlays.json").open("x", encoding="utf-8") as handle:
        json.dump(
            {"version": "UNRESOLVED_STRESS_DIAGNOSTIC_OVERLAY_V1", "overlays": stress_overlays},
            handle,
            indent=2,
        )
    with (root / "independent-audit.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "decision",
                    "rows_verified",
                    "trade_checks",
                    "holdout_strategy_replays_in_audit",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
