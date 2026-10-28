"""Reconciled performance and isolated read-only HTML over persisted research results."""

import html
from collections import Counter, defaultdict
from decimal import Decimal

from atlas.backtest import jsonable
from atlas.config import AtlasError
from atlas.market_data.contracts import digest
from atlas.research_diagnostics import reporting


def performance(result):
    content = dict(result)
    checksum = content.pop("result_checksum")
    if digest(content) != checksum or not result["reconciled"]:
        raise AtlasError("Research result checksum or reconciliation failed.")
    base = reporting(result)
    zero = Decimal(0)
    initial = Decimal(result["initial_capital_gbp"])
    cash = initial
    active, trades, seen = {}, [], set()
    costs = defaultdict(lambda: zero)
    previous = None
    for fill in result["fills"]:
        if fill["fill_id"] in seen or (previous and fill["timestamp"] < previous):
            raise AtlasError("Research fills duplicate identities or move backwards.")
        seen.add(fill["fill_id"])
        previous = fill["timestamp"]
        symbol = fill["instrument_id"]
        qty, price, rate, fee = (
            Decimal(fill[k]) for k in ("quantity", "price", "fx_rate", "fees_gbp")
        )
        if min(qty, price, rate) <= 0 or fee < 0:
            raise AtlasError("Invalid persisted fill values.")
        for key, value in fill["cost_components"]:
            costs[key] += Decimal(value)
        if fill["side"] == "buy":
            item = active.setdefault(
                symbol,
                {
                    "entry_intent": fill["intent_id"],
                    "symbol": symbol,
                    "entry_at": fill["timestamp"],
                    "quantity": zero,
                    "gross_pnl_gbp": zero,
                    "net_pnl_gbp": zero,
                    "fees_gbp": zero,
                },
            )
            if item["entry_intent"] != fill["intent_id"]:
                raise AtlasError("Overlapping research entries cannot be merged.")
            item["quantity"] += qty
            item["fees_gbp"] += fee
            cash -= qty * price * rate + fee
        elif fill["side"] == "sell" and symbol in active:
            item = active[symbol]
            item["quantity"] -= qty
            if item["quantity"] < 0:
                raise AtlasError("Research exit exceeds held quantity.")
            item["fees_gbp"] += fee
            item["gross_pnl_gbp"] += Decimal(fill["gross_pnl_gbp"])
            item["net_pnl_gbp"] += Decimal(fill["realized_pnl_gbp"])
            cash += qty * price * rate - fee
            if item["quantity"] == 0:
                item["exit_at"] = fill["timestamp"]
                trades.append(item)
                del active[symbol]
        else:
            raise AtlasError("Research exit has no matching entry.")
        if cash < Decimal("-1e-18"):
            raise AtlasError("Research cash reconciliation found borrowing.")
    final = Decimal(result["ending_equity_gbp"])
    net = sum((t["net_pnl_gbp"] for t in trades), zero)
    fees = sum((Decimal(f["fees_gbp"]) for f in result["fills"]), zero)
    if (
        active
        or abs(cash - final) > Decimal("1e-18")
        or abs(final - initial - net) > Decimal("1e-18")
        or abs(net - Decimal(result["realised_pnl_gbp"])) > Decimal("1e-18")
        or fees != Decimal(result["costs_gbp"])
        or any(
            abs(t["gross_pnl_gbp"] - t["fees_gbp"] - t["net_pnl_gbp"]) > Decimal("1e-18")
            for t in trades
        )
    ):
        raise AtlasError("Research fill ledger does not reconcile to reported equity and P/L.")
    peak, drawdown, dd_pct = initial, zero, zero
    exposure, utilisation = [], []
    for _, equity, available in result["equity_curve"]:
        equity, available = Decimal(equity), Decimal(available)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        dd_pct = max(dd_pct, (peak - equity) / peak * 100 if peak else zero)
        exposure.append(equity - available)
        utilisation.append((equity - available) / equity * 100 if equity else zero)
    if drawdown != Decimal(result["maximum_drawdown_gbp"]):
        raise AtlasError("Research drawdown differs from persisted valuations.")
    gains = sum((t["net_pnl_gbp"] for t in trades if t["net_pnl_gbp"] > 0), zero)
    losses = -sum((t["net_pnl_gbp"] for t in trades if t["net_pnl_gbp"] < 0), zero)
    symbols = sorted(
        {r["instrument_id"] for r in result.get("strategy_sessions", [])} | set(base["by_ticker"])
    )
    by_symbol = {}
    for symbol in symbols:
        selected = [t for t in trades if t["symbol"] == symbol]
        by_symbol[symbol] = {
            "trades": len(selected),
            "wins": sum(t["net_pnl_gbp"] > 0 for t in selected),
            "losses": sum(t["net_pnl_gbp"] < 0 for t in selected),
            "net_pnl_gbp": sum((t["net_pnl_gbp"] for t in selected), zero),
            "fees_gbp": sum((t["fees_gbp"] for t in selected), zero),
        }
    traded_days = {t["exit_at"][:10] for t in trades}
    risk = Counter(
        reason
        for d in result["risk_decisions"]
        if d["status"] != "approved"
        for reason in d["reasons"]
    )
    value = jsonable(
        {
            "run_id": result["run_id"],
            "source_result_checksum": checksum,
            "label": result["label"],
            "availability_modes": result["availability_modes"],
            "completed_trades": len(trades),
            "wins": sum(t["net_pnl_gbp"] > 0 for t in trades),
            "losses": sum(t["net_pnl_gbp"] < 0 for t in trades),
            "breakeven": sum(t["net_pnl_gbp"] == 0 for t in trades),
            "win_rate_pct": Decimal(sum(t["net_pnl_gbp"] > 0 for t in trades)) / len(trades) * 100
            if trades
            else None,
            "gross_profit_before_fees_gbp": sum(
                (t["gross_pnl_gbp"] for t in trades if t["gross_pnl_gbp"] > 0), zero
            ),
            "gross_loss_before_fees_gbp": -sum(
                (t["gross_pnl_gbp"] for t in trades if t["gross_pnl_gbp"] < 0), zero
            ),
            "net_winning_pnl_gbp": gains,
            "net_losing_pnl_abs_gbp": losses,
            "profit_factor_after_costs": gains / losses if losses else None,
            "net_realised_pnl_gbp": net,
            "net_unrealised_pnl_gbp": zero,
            "final_equity_gbp": final,
            "final_cash_gbp": cash,
            "initial_capital_gbp": initial,
            "maximum_drawdown_gbp": drawdown,
            "maximum_drawdown_pct": dd_pct,
            "average_pnl_per_completed_trade_gbp": net / len(trades) if trades else None,
            "days_with_trades": len(traded_days),
            "days_without_trades": len(result["sessions"]) - len(traded_days),
            "fees_and_costs_charged_gbp": fees,
            "cost_components": dict(costs),
            "spread_cash_charge_gbp": costs.get("entry_spread", zero)
            + costs.get("exit_spread", zero),
            "spread_note": (
                "Spread is embedded in simulated bid/ask prices; "
                "zero separate charge does not mean zero spread impact."
            ),
            "exact_spread_opportunity_cost_gbp": None,
            "by_month": base["by_month"],
            "by_symbol": by_symbol,
            "completed_trade_ledger": trades,
            "risk_rejected_decisions": sum(
                d["status"] != "approved" for d in result["risk_decisions"]
            ),
            "risk_rejection_reason_counts": dict(risk),
            "strategy_rejection_reason_counts": result.get("strategy_reason_counts", {}),
            "max_marked_exposure_gbp": max(exposure, default=zero),
            "max_cash_utilisation_pct": max(utilisation, default=zero),
            "mean_observation_cash_utilisation_pct": sum(utilisation, zero) / len(utilisation)
            if utilisation
            else None,
            "utilisation_basis": (
                "Post-observation equity minus available cash; "
                "observation-weighted, not wall-clock weighted"
            ),
            "reconciled": True,
        }
    )
    return value | {"metrics_checksum": digest(value)}


def render_research_html(result, metrics, *, excluded_dates=()):
    """No account database, network assets, controls or notifications."""
    if performance(result) != metrics:
        raise AtlasError("Research display metrics do not match persisted result.")

    def esc(value):
        return html.escape(str(value))

    daily = {}
    for stamp, equity, _ in result["equity_curve"]:
        daily[stamp[:10]] = Decimal(equity)
    values = list(daily.values()) or [Decimal(result["initial_capital_gbp"])]
    low, high = min(values), max(values)
    points = " ".join(
        f"{10 + i * 780 / max(1, len(values) - 1):.2f},"
        f"{190 - float((v - low) / (high - low or 1)) * 170:.2f}"
        for i, v in enumerate(values)
    )
    cards = "".join(
        f"<tr><th>{esc(k)}</th><td>{esc(metrics[k])}</td></tr>"
        for k in (
            "completed_trades",
            "wins",
            "losses",
            "net_realised_pnl_gbp",
            "net_unrealised_pnl_gbp",
            "final_equity_gbp",
            "maximum_drawdown_gbp",
            "maximum_drawdown_pct",
            "fees_and_costs_charged_gbp",
        )
    )
    fills = "".join(
        "<tr>"
        + "".join(
            f"<td>{esc(f[k])}</td>"
            for k in (
                "timestamp",
                "instrument_id",
                "side",
                "quantity",
                "price",
                "fees_gbp",
                "realized_pnl_gbp",
            )
        )
        + "</tr>"
        for f in result["fills"]
    )
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Atlas historical research</title>
<style>
body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px;
background:#101827;color:#e6edf6}}
table{{border-collapse:collapse;width:100%;margin:24px 0}}
td,th{{padding:8px;border-bottom:1px solid #35445c;text-align:left}}
svg{{width:100%;background:#182337}}code{{overflow-wrap:anywhere}}
</style>
<h1>Atlas historical research</h1><p>{esc(result["label"])}.</p>
<p>Availability: {esc(result["availability_modes"])}.
Excluded dates: {esc(", ".join(excluded_dates) or "None declared")}.
Independent research portfolio; no paper-account writes.</p>
<p>Run <code>{esc(result["run_id"])}</code></p><table>{cards}</table>
<h2>Daily closing equity (GBP)</h2><p>Range {esc(low)} to {esc(high)}.
Drawdown statistics use every persisted observation, not only daily closes.</p>
<svg viewBox="0 0 800 210" role="img" aria-label="Daily closing research equity">
<polyline fill="none" stroke="#65d8bc" stroke-width="2" points="{points}"/></svg>
<h2>Persisted simulated fill activity</h2><table><tr><th>UTC</th><th>Instrument</th>
<th>Side</th><th>Quantity</th><th>USD price</th><th>GBP costs</th>
<th>Realised GBP P/L</th></tr>{fills}</table>
<p>Metrics checksum <code>{esc(metrics["metrics_checksum"])}</code>.
All displayed figures reconcile to the persisted research fill ledger.
This file cannot place orders or send messages.</p></html>'''
