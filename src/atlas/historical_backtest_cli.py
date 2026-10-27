"""Offline CLI for immutable Phase 3A datasets; never contacts providers or brokers."""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from atlas.config import AtlasError
from atlas.historical_backtest import (
    AvailabilityMode,
    EvaluationPlan,
    EvaluationRole,
    ExecutionAssumptions,
    FxPolicy,
    HistoricalBacktestConfig,
    InstrumentRules,
    run_historical_backtest,
)
from atlas.market_data.store import HistoricalStore
from atlas.research import ResearchSettings
from atlas.strategy import ORBConfig


def _decimal(value, name):
    if not isinstance(value, str):
        raise AtlasError(f"{name} must be an explicit decimal string.")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise AtlasError(f"{name} must be an explicit decimal string.") from None


def _object(value, fields, name):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise AtlasError(f"{name} requires exactly: {', '.join(fields)}.")
    return value


def load_config(path):
    if path.stat().st_size > 100_000:
        raise AtlasError("Historical backtest configuration is too large.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AtlasError(
            "Historical backtest configuration is unreadable or invalid JSON."
        ) from None
    root_fields = (
        "schema_version",
        "dataset_id",
        "availability",
        "instrument",
        "execution",
        "fx",
        "evaluation",
        "orb",
        "research",
    )
    root = _object(raw, root_fields, "Historical backtest configuration")
    if root["schema_version"] != 1 or not isinstance(root["dataset_id"], str):
        raise AtlasError("Unsupported historical backtest schema or dataset identity.")
    availability = _object(root["availability"], ("mode", "delay_seconds"), "availability")
    instrument_fields = ("tick_size", "quantity_step", "minimum_quantity")
    instrument = _object(
        root["instrument"],
        instrument_fields,
        "instrument",
    )
    execution_fields = (
        "spread_bps",
        "slippage_bps",
        "commission_fixed_gbp",
        "commission_bps",
        "fx_bps",
        "entry_tax_bps",
        "exit_tax_bps",
        "other_fixed_gbp",
        "max_volume_participation_pct",
        "stress_multiplier",
    )
    execution = _object(root["execution"], execution_fields, "execution")
    fx = _object(
        root["fx"],
        ("gbp_per_usd", "event_time", "available_time", "source", "point_in_time_verified"),
        "fx",
    )
    evaluation = _object(root["evaluation"], ("role", "parameter_selection_cutoff"), "evaluation")
    orb = _object(
        root["orb"],
        (
            "opening_range_minutes",
            "candle_minutes",
            "target_r",
            "signal_lifetime_minutes",
            "exit_buffer_minutes",
            "minimum_bar_volume",
        ),
        "orb",
    )
    research_fields = (
        "starting_capital_gbp",
        "planned_risk_per_trade_pct",
        "daily_loss_threshold_pct",
        "max_concurrent_planned_risk_pct",
    )
    research = _object(
        root["research"],
        research_fields,
        "research",
    )
    try:
        config = HistoricalBacktestConfig(
            AvailabilityMode(availability["mode"]),
            availability["delay_seconds"],
            InstrumentRules(
                *(_decimal(instrument[key], f"instrument.{key}") for key in instrument_fields)
            ),
            ExecutionAssumptions(
                *(_decimal(execution[key], f"execution.{key}") for key in execution_fields)
            ),
            FxPolicy(
                _decimal(fx["gbp_per_usd"], "fx.gbp_per_usd"),
                datetime.fromisoformat(fx["event_time"]),
                datetime.fromisoformat(fx["available_time"]),
                fx["source"],
                fx["point_in_time_verified"],
            ),
            EvaluationPlan(
                EvaluationRole(evaluation["role"]),
                datetime.fromisoformat(evaluation["parameter_selection_cutoff"])
                if evaluation["parameter_selection_cutoff"] is not None
                else None,
            ),
            ORBConfig(
                orb["opening_range_minutes"],
                orb["candle_minutes"],
                _decimal(orb["target_r"], "orb.target_r"),
                orb["signal_lifetime_minutes"],
                orb["exit_buffer_minutes"],
                _decimal(orb["minimum_bar_volume"], "orb.minimum_bar_volume"),
            ),
            ResearchSettings(
                *(_decimal(research[key], f"research.{key}") for key in research_fields)
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise AtlasError("Historical backtest configuration values are invalid.") from None
    return root["dataset_id"], config


def run(args):
    dataset_id, config = load_config(args.historical_config)
    if dataset_id.startswith("replace_"):
        raise AtlasError("Replace the example dataset ID with an accepted local dataset ID.")
    store = HistoricalStore(args.historical_db, read_only=True)
    report = run_historical_backtest(
        store,
        dataset_id,
        config,
        allow_artificial_fixture=args.allow_artificial_historical,
    )
    if args.json:
        print(json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":")))
    else:
        result = report.result
        print(report.label)
        print(f"Run: {report.run_id}; dataset: {report.dataset_id}")
        print(
            f"Availability: {report.availability_mode}; selected bars: "
            f"{report.selected_rows}/{report.source_rows}; point-in-time verified: "
            f"{report.point_in_time_verified}"
        )
        print(
            f"Starting capital: GBP {result.starting_capital_gbp:,.2f}; "
            f"ending equity: GBP {result.ending_equity_gbp:,.2f}; "
            f"net P/L: GBP {result.net_pnl_gbp:,.2f}"
        )
        print(
            f"Trades: {result.simulated_trades}; costs: GBP {result.total_costs_gbp:,.2f}; "
            f"maximum drawdown: GBP {result.maximum_drawdown_gbp:,.2f}"
        )
        print("No broker orders, provider requests, persistence or notifications were performed.")
    return 0
