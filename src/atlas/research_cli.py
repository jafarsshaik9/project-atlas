"""Offline command handlers. Never load broker keys or import the broker adapter."""

import json
import os
from dataclasses import fields
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from atlas.backtest import run_backtest
from atlas.config import AtlasError, Settings
from atlas.costs import CostEngine, FeeSchedule
from atlas.domain import Venue
from atlas.portfolio import PortfolioLedger
from atlas.research import ResearchSettings
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import LABEL, generate, synthetic_costs


def read_config(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise AtlasError("Cannot read a valid offline JSON configuration.") from None
    if not isinstance(value, dict):
        raise AtlasError("Offline configuration must be a JSON object.")
    return value


def load_research(path: str | None) -> ResearchSettings:
    if path is None:
        return ResearchSettings.from_environment()
    data = read_config(path)
    allowed = {field.name for field in fields(ResearchSettings)}
    if set(data) != allowed:
        raise AtlasError("Research JSON must specify exactly the four capital/risk settings.")
    if any(not isinstance(value, str) for value in data.values()):
        raise AtlasError("Research settings must be decimal strings.")
    try:
        return ResearchSettings(**{key: Decimal(value) for key, value in data.items()})
    except (InvalidOperation, TypeError, ValueError):
        raise AtlasError("Research settings must be decimal strings.") from None


def load_costs(path: str | None) -> CostEngine:
    if path is None:
        return synthetic_costs()
    data = read_config(path)
    try:
        for key in list(data):
            if key.endswith(("_bps", "_gbp")) or key == "stress_multiplier":
                if data[key] is not None and not isinstance(data[key], str):
                    raise AtlasError("Cost rates must be decimal strings or explicit null.")
                data[key] = Decimal(data[key]) if data[key] is not None else None
        data["effective_from"] = datetime.fromisoformat(data["effective_from"])
        data["effective_until"] = datetime.fromisoformat(data["effective_until"])
        data["venues"] = tuple(Venue(venue) for venue in data["venues"])
        return CostEngine((FeeSchedule(**data),))
    except (InvalidOperation, TypeError, ValueError, KeyError):
        raise AtlasError("Invalid synthetic cost configuration.") from None


def load_policy(path: str | None) -> RiskPolicy:
    if path is None:
        return RiskPolicy()
    data = read_config(path)
    try:
        for key in ("max_position_pct", "gap_stress_pct", "max_stressed_risk_pct"):
            if not isinstance(data[key], str):
                raise AtlasError("Risk percentages must be decimal strings.")
            data[key] = Decimal(data[key])
        seconds = data.pop("quote_max_age_seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            raise ValueError
        data["quote_max_age"] = timedelta(seconds=seconds)
        return RiskPolicy(**data)
    except (InvalidOperation, TypeError, ValueError, KeyError):
        raise AtlasError("Invalid provisional risk configuration.") from None


def run(args) -> int:
    # Only the environment selector is read; broker credentials are not loaded.
    Settings(environment=os.environ.get("ATLAS_ENV", "demo"))
    settings = load_research(args.research_config)
    costs = load_costs(args.cost_config)
    policy = load_policy(args.risk_config)
    try:
        session_date = date.fromisoformat(args.session_date)
    except ValueError:
        raise AtlasError("Session date must use YYYY-MM-DD.") from None
    data = generate(args.scenario, Venue(args.venue), session_date)
    orb = ORBConfig(opening_range_minutes=args.opening_range_minutes)
    if args.command == "research-check":
        state = PortfolioLedger(settings).snapshot(data.session.open)
        costs.schedule(data.instrument, data.session.open)
        print(LABEL)
        print(f"Research configuration OK; virtual cash GBP {state.available_cash_gbp:,.2f}")
        print(
            f"Planned risk GBP {settings.planned_risk_per_trade_gbp:.2f}; "
            f"daily loss threshold GBP {settings.daily_loss_threshold_gbp:.2f}; "
            f"concurrent planned risk GBP {settings.max_concurrent_planned_risk_gbp:.2f}"
        )
        print(f"Fixture calendar; ORB range {orb.opening_range_minutes}m on 5m bars.")
        print(f"Risk policy {policy.version}; no leverage, shorts or broker execution.")
        print("Offline checks passed. No credentials, network or AI service used.")
        return 0
    if args.no_persist:
        if args.notify_synthetic:
            raise AtlasError("Notifications require persisted, confirmed simulation events.")
        report = run_backtest(data, settings, costs=costs, orb=orb, policy=policy)
    else:
        from atlas.history import HistoryStore
        from atlas.research_history import recorded_backtest

        report, _ = recorded_backtest(
            data,
            settings,
            costs=costs,
            orb=orb,
            policy=policy,
            store=HistoryStore(args.history_db),
            new_run=args.new_run,
            notify=args.notify_synthetic,
            notify_summary=args.notify_summary,
        )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=True))
    else:
        print(report.label)
        print(f"Dataset: {data.name}; venue: {data.instrument.venue.value}")
        print(f"Starting virtual capital: GBP {report.starting_capital_gbp:,.2f}")
        print(f"Ending virtual equity: GBP {report.ending_equity_gbp:,.2f}")
        print(f"Opportunities: {report.opportunities}; simulated trades: {report.simulated_trades}")
        print(
            f"Gross P&L: GBP {report.gross_pnl_gbp:.2f}; costs: GBP {report.total_costs_gbp:.2f}; "
            f"net P&L: GBP {report.net_pnl_gbp:.2f}"
        )
        print(f"FX conversion costs within total costs: GBP {report.fx_conversion_costs_gbp:.2f}")
        print(
            f"Wins/losses: {report.wins}/{report.losses}; "
            f"max drawdown: GBP {report.maximum_drawdown_gbp:.2f}; "
            f"cash utilisation: {report.maximum_cash_utilisation_pct:.2f}%"
        )
        print(f"Rejected opportunities: {len(report.rejected_opportunities)}")
        for _, reasons in report.rejected_opportunities:
            print("  " + ", ".join(reasons))
        print(f"Risk-limit events: {len(report.risk_limit_events)}")
        for _, reasons in report.risk_limit_events:
            print("  " + ", ".join(reasons))
        if report.risk_decisions:
            maximum_planned = max(item.planned_risk_gbp for item in report.risk_decisions)
            maximum_stressed = max(item.stressed_risk_gbp for item in report.risk_decisions)
            print(
                f"Largest planned/stressed risk: GBP {maximum_planned:.2f} / "
                f"GBP {maximum_stressed:.2f} (stress is not a guaranteed cap)"
            )
        print(
            "Strategy/data outcomes: "
            + ", ".join(f"{reason}={count}" for reason, count in report.quality_events)
        )
        print(f"Open positions: {report.open_positions}; reconciled: {report.reconciled}")
        print(f"Dataset SHA-256: {report.dataset_checksum}")
        print(f"Configuration SHA-256: {report.configuration_checksum}")
        print("Artificial data and fees; these results do not establish a profitable strategy.")
    return 0
