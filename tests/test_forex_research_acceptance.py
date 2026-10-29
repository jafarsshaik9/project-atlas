"""Independent validator attacks using original artificial execution fixtures."""

import copy
import importlib.util
from decimal import localcontext
from pathlib import Path

import pytest
from test_forex_execution import META, OPEN, frame, spec
from test_forex_research import fixture_frames

from atlas.forex.execution.calendar import window
from atlas.forex.execution.contracts import MINUTE, arithmetic_context
from atlas.forex.execution.engine import jsonable, replay
from atlas.forex.research.design import cell_id, load_design, settings_for
from atlas.forex.research.execution import evaluate_intent
from atlas.forex.research.runner import worker
from atlas.forex.research.strategies import candidate

SPEC = importlib.util.spec_from_file_location(
    "forex_research_auditor", Path(__file__).parents[1] / "scripts/forex_research_audit.py"
)
AUDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDITOR)


@pytest.mark.parametrize("strategy", ["ORB", "MOMENTUM", "MEAN_REVERSION", "DIRECTIONAL"])
def test_independent_validator_accepts_synthetic_engine_fills(strategy):
    design = load_design()
    frames = fixture_frames(strategy)
    states = {
        cell_id("EUR_USD", "LONDON", s, c): {"cash": "1000", "known": True}
        for s in design["strategies"]
        for c in ("C0", "C1", "C2", "C3")
    }
    outputs, _ = worker(
        (OPEN.date(), "EUR_USD", "LONDON", frames, META, states, design, "artificial", [])
    )
    source = {m.name: {} for m in META}
    for f in frames:
        for o in f.observations:
            source[o.instrument][f.start] = o.candle
    checked = 0
    with localcontext(arithmetic_context()):
        for _, _, row in outputs:
            if row["execution"]["trade"]:
                AUDITOR.validate_trade(row, source, META[0], design)
                checked += 1
    assert checked > 0


def test_unresolved_specialization_preserves_financial_state_and_supplements_stress():
    frames = fixture_frames("ORB")
    frames[18] = frame(OPEN + 18 * MINUTE, absent=("EUR_USD",))
    cfg = settings_for("C0")
    with localcontext(arithmetic_context()):
        intent, index, _ = candidate(frames, "EUR_USD", "ORB", META[0], cfg, window(spec(), cfg))
        complete = replay(cfg, spec(), META, frames, (intent,))
        fast = evaluate_intent(cfg, spec(), META, frames, intent, index, "artificial-gap")
        assert fast["status"] == complete.status == "INVALID_UNRESOLVED_PATH"
        assert fast["cash_partial"] == str(complete.cash_gbp)
        assert fast["net_pnl_gbp"] is None and not fast["trade"]
        assert fast["unresolved_positions"] == jsonable(complete.unresolved_positions)
        bars = {m.name: {} for m in META}
        for f in frames:
            for o in f.observations:
                bars[o.instrument][f.start] = o.candle
        overlay = AUDITOR.unresolved_stress_overlay(
            {"cell": "EUR_USD/LONDON/ORB/C0", "day": str(OPEN.date()), "execution": fast},
            bars,
            load_design(),
        )
        assert overlay["portfolio_pnl"] is None and not overlay["booked_exit"]
        assert overlay["adverse_price"] and overlay["unknown_path"]


def test_independent_validator_rejects_an_optimistically_rewritten_fill():
    design = load_design()
    frames = fixture_frames("ORB")
    states = {
        cell_id("EUR_USD", "LONDON", s, c): {"cash": "1000", "known": True}
        for s in design["strategies"]
        for c in ("C0", "C1", "C2", "C3")
    }
    rows, _ = worker(
        (OPEN.date(), "EUR_USD", "LONDON", frames, META, states, design, "artificial", [])
    )
    row = copy.deepcopy(next(r for _, _, r in rows if r["execution"]["trade"]))
    row["execution"]["trade"]["position"]["entry"] = "1.00000"
    bars = {m.name: {} for m in META}
    for f in frames:
        for o in f.observations:
            bars[o.instrument][f.start] = o.candle
    with localcontext(arithmetic_context()), pytest.raises(AssertionError, match="Entry bid/ask"):
        AUDITOR.validate_trade(row, bars, META[0], design)


def test_deadline_exit_has_priority_over_simultaneous_marked_exposure_breach():
    design = load_design()
    frames = fixture_frames("ORB")
    frames.extend(
        frame(OPEN + i * MINUTE, {"EUR_USD": {"bid": "1.101", "ask": "1.1012"}})
        for i in range(90, 150)
    )
    frames[76] = frame(OPEN + 76 * MINUTE, {"EUR_USD": {"bid": "1.1022", "ask": "1.1024"}})
    states = {
        cell_id("EUR_USD", "LONDON", s, c): {"cash": "1000", "known": True}
        for s in design["strategies"]
        for c in ("C0", "C1", "C2", "C3")
    }
    rows, _ = worker(
        (OPEN.date(), "EUR_USD", "LONDON", frames, META, states, design, "artificial", [])
    )
    row = next(r for key, _, r in rows if key == "EUR_USD/LONDON/ORB/C0")
    assert row["execution"]["trade"]["reason"] == "FX_MAX_HOLDING"
    bars = {m.name: {} for m in META}
    for f in frames:
        for o in f.observations:
            bars[o.instrument][f.start] = o.candle
    with localcontext(arithmetic_context()):
        AUDITOR.validate_trade(row, bars, META[0], design)
