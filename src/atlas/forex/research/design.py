"""Read the preregistered design; reject changes rather than selecting new settings."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.execution.contracts import Costs, Run, Settings

DESIGN_SHA = "b3031e70acb3b1a801dcec2cef1bb394b659ff9333ac7fa4f44c8a79a8b6b499"
ROOT = Path(__file__).resolve().parents[4]
MANIFEST = ROOT / "examples/forex-research-phase6b3-v1.json"
STRATEGIES = ("ORB", "MOMENTUM", "MEAN_REVERSION", "DIRECTIONAL")
SCENARIOS = ("C0", "C1", "C2", "C3", "C4")
SESSIONS = ("LONDON", "NEW_YORK", "OVERLAP")
PAIRS = ("EUR_USD", "GBP_USD", "USD_JPY")


def load_design(path=MANIFEST):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != DESIGN_SHA:
        raise AtlasError("FOREX_FROZEN_DESIGN_CHANGED")
    return json.loads(raw)


def split_for(stamp, design):
    return next(
        (
            s["name"]
            for s in design["splits"]
            if datetime.fromisoformat(s["start"]) <= stamp < datetime.fromisoformat(s["end"])
        ),
        None,
    )


def cell_id(pair, session, strategy, scenario):
    return "/".join((pair, session, strategy, scenario))


def settings_for(scenario, cash=Decimal(1000), design=None):
    design = design or load_design()
    if scenario == "C4":
        raise AtlasError("FX_C4_CONTRACT_BLOCKED")
    return Settings(
        costs=Costs(
            version="FX_CLOSE_COSTS_V2",
            provenance="FROZEN_PHASE6B3_ASSUMED_COSTS",
            known_at=datetime.fromisoformat(design["cost_known_at"]),
            slippage_pips=Decimal(design["costs"][scenario]["slippage_pips"]),
            commission_gbp_per_unit_per_side=Decimal(design["commission_gbp_per_unit_per_side"]),
            spread_floor_pips=Decimal(0),
            additional_spread_pips=Decimal(0),
            spread_multiplier=Decimal(design["costs"][scenario]["spread_multiplier"]),
        ),
        starting_capital_gbp=cash,
        max_exposure_gbp=min(Decimal(1000), cash),
        risk_per_trade_gbp=min(Decimal(5), cash),
        max_concurrent_risk_gbp=min(Decimal(10), cash),
        daily_loss_limit_gbp=min(Decimal(10), cash),
        max_positions=1,
        max_holding_minutes=60,
    )


def run_for(day, session, design, *, origin="EXTERNAL_HISTORICAL_MARKET_DATA"):
    return Run(
        day=day,
        session=session,
        coverage_start=datetime.fromisoformat(design["source_start"]),
        coverage_end=datetime.fromisoformat(design["source_end_exclusive"]),
        code_version="PHASE6B3_V1_ENTRY_RULES_COSTS_V2",
        calendar_provenance="PHASE6B1C_V2_PLUS_PHASE6B2_RESEARCH_EXCLUSIONS",
        origin=origin,
    )
