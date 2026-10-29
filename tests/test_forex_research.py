"""Frozen research hypotheses on artificial data, including adversarial prefix and ledger tests."""

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError
from test_forex_execution import META, OPEN, frame, order, settings, spec

from atlas.config import AtlasError
from atlas.forex.execution.calendar import window
from atlas.forex.execution.contracts import (
    MINUTE,
    Costs,
    EntryRule,
    Frame,
    Observation,
    arithmetic_context,
)
from atlas.forex.execution.engine import jsonable, replay
from atlas.forex.models import FxCandle
from atlas.forex.research.design import DESIGN_SHA, cell_id, load_design, settings_for, split_for
from atlas.forex.research.execution import evaluate_intent
from atlas.forex.research.metrics import classify, summarize
from atlas.forex.research.runner import read_rows, worker, write_row
from atlas.forex.research.strategies import candidate

D = Decimal


def fixture_frames(strategy="ORB", side="long"):
    frames = []
    for index in range(90):
        changes = {}
        if strategy == "ORB" and index < 15:
            changes = {"EUR_USD": {"high": "1.1001", "low": "1.0999"}}
        if strategy == "ORB" and index >= 15:
            bid = "1.101" if side == "long" else "1.099"
            changes = {"EUR_USD": {"bid": bid, "ask": str(D(bid) + D(".0002"))}}
        if strategy == "MOMENTUM" and index >= 20:
            bid = "1.102" if side == "long" else "1.098"
            changes = {"EUR_USD": {"bid": bid, "ask": str(D(bid) + D(".0002"))}}
        if strategy == "MEAN_REVERSION" and index in (20, 21):
            bid = "1.098" if side == "long" else "1.102"
            changes = {"EUR_USD": {"bid": bid, "ask": str(D(bid) + D(".0002"))}}
        if strategy == "DIRECTIONAL" and index == 15:
            changes = {"EUR_USD": {"opening": "1.0999" if side == "long" else "1.1001"}}
        frames.append(frame(OPEN + index * MINUTE, changes))
    return frames


@pytest.mark.parametrize("strategy", ["ORB", "MOMENTUM", "MEAN_REVERSION", "DIRECTIONAL"])
@pytest.mark.parametrize("side", ["long", "short"])
def test_frozen_strategies_use_correct_completed_prefix_and_actual_entry_brackets(strategy, side):
    frames = fixture_frames(strategy, side)
    cfg = settings_for("C0")
    run = spec()
    win = window(run, cfg)
    with localcontext(arithmetic_context()):
        intent, index, _ = candidate(frames, "EUR_USD", strategy, META[0], cfg, win)
        assert intent.side == side
        assert index == (15 if strategy in ("ORB", "DIRECTIONAL") else 20)
        result = replay(cfg, run, META, frames, (intent,))
        assert len(result.trades) == 1
        position = result.trades[0].position
        assert position.opened_at == intent.signal_at + MINUTE
        if strategy == "ORB":
            assert abs(position.intent.target - position.entry) == D("1.5") * abs(
                position.entry - position.intent.stop
            )
        else:
            assert abs(position.intent.stop - position.entry) == D(".0015") * position.entry
        assert position.planned_risk_gbp <= 5 and position.reserved_gbp <= 1000


@pytest.mark.parametrize("strategy", ["ORB", "MOMENTUM", "MEAN_REVERSION", "DIRECTIONAL"])
@pytest.mark.parametrize("scenario", ["C0", "C1", "C2", "C3"])
def test_quiescent_specialization_has_full_replay_economic_parity(strategy, scenario):
    frames = fixture_frames(strategy)
    cfg = settings_for(scenario)
    run = spec()
    with localcontext(arithmetic_context()):
        intent, index, _ = candidate(frames, "EUR_USD", strategy, META[0], cfg, window(run, cfg))
        full = replay(cfg, run, META, frames, (intent,))
        fast = evaluate_intent(cfg, run, META, frames, intent, index, "synthetic-source-hash")
    assert fast["trade"] == (jsonable(full.trades[0]) if full.trades else None)
    assert fast["net_pnl_gbp"] == str(full.net_pnl_gbp)
    assert fast["cash_partial"] == str(full.cash_gbp)
    assert fast["status"] == full.status
    if fast["trade"]:
        bridge = fast["cost_bridge"]
        assert D(bridge["gross_model_pnl_gbp"]) - D(bridge["total_modeled_costs_gbp"]) == D(
            fast["trade"]["net_gbp"]
        )
        assert D(bridge["total_modeled_costs_gbp"]) >= 0


@pytest.mark.parametrize(
    "strategy,index", [("ORB", 15), ("MOMENTUM", 20), ("MEAN_REVERSION", 20), ("DIRECTIONAL", 15)]
)
def test_signal_does_not_inspect_a_future_frame(strategy, index):
    frames = fixture_frames(strategy)

    class PrefixOnly:
        def __iter__(self):
            for n, f in enumerate(frames):
                if n > index:
                    raise AssertionError("Strategy attempted future access")
                yield f

    cfg = settings_for("C0")
    intent, found, _ = candidate(
        PrefixOnly(), "EUR_USD", strategy, META[0], cfg, window(spec(), cfg)
    )
    assert found == index and intent


def test_momentum_and_mean_reference_exclude_signal_from_20_lag_closes():
    frames = fixture_frames("MEAN_REVERSION")
    cfg = settings_for("C0")
    with localcontext(arithmetic_context()):
        intent, index, _ = candidate(
            frames, "EUR_USD", "MEAN_REVERSION", META[0], cfg, window(spec(), cfg)
        )
    assert index == 20 and intent.entry_rule.frozen_reference == D("1.10010")


@pytest.mark.parametrize("strategy", ["ORB", "MOMENTUM", "MEAN_REVERSION"])
def test_incomplete_indicator_windows_are_not_bridged(strategy):
    frames = fixture_frames(strategy)[:21]
    frames[5] = frame(OPEN + 5 * MINUTE, absent=("EUR_USD",))
    cfg = settings_for("C0")
    intent, _, reasons = candidate(frames, "EUR_USD", strategy, META[0], cfg, window(spec(), cfg))
    assert intent is None and reasons


def test_first_directional_doji_ends_opportunity_without_later_selection():
    frames = [frame(OPEN + i * MINUTE) for i in range(20)]
    frames[16] = frame(OPEN + 16 * MINUTE, {"EUR_USD": {"opening": "1.09"}})
    cfg = settings_for("C0")
    assert candidate(frames, "EUR_USD", "DIRECTIONAL", META[0], cfg, window(spec(), cfg))[0] is None


@pytest.mark.parametrize("multiplier", [D("1.5"), D(2)])
def test_spread_multiplier_requires_explicit_cost_version_and_shifts_prices(multiplier):
    with pytest.raises(ValidationError):
        Costs(**(settings().costs.model_dump() | {"spread_multiplier": multiplier}))
    cfg = settings(costs={"version": "FX_CLOSE_COSTS_V2", "spread_multiplier": multiplier})
    result = replay(cfg, spec(), META, [frame(OPEN + i * MINUTE) for i in range(3)], (order(),))
    assert result.trades[0].position.entry > D("1.1002")


def test_fill_relative_reference_target_cannot_be_below_actual_long_entry():
    intent = order(entry_rule=EntryRule(kind="REFERENCE", frozen_reference=D("1.1001")))
    result = replay(
        settings(), spec(), META, [frame(OPEN + i * MINUTE) for i in range(3)], (intent,)
    )
    assert not result.trades
    assert any(e.reason == "FX_ENTRY_RULE_INVALID_BRACKET_AT_FILL" for e in result.events)


@pytest.mark.parametrize(
    "stamp,split",
    [
        ("2025-12-31T23:59:00+00:00", "development"),
        ("2026-01-01T00:00:00+00:00", "validation"),
        ("2026-06-01T00:00:00+00:00", "holdout"),
        ("2026-10-28T22:49:00+00:00", "holdout"),
        ("2026-10-28T22:50:00+00:00", None),
    ],
)
def test_exact_split_boundaries(stamp, split):
    assert split_for(datetime.fromisoformat(stamp), load_design()) == split


def test_manifest_mutation_and_C4_are_rejected(tmp_path):
    p = tmp_path / "design.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(AtlasError, match="FROZEN_DESIGN_CHANGED"):
        load_design(p)
    with pytest.raises(AtlasError, match="C4_CONTRACT_BLOCKED"):
        settings_for("C4")
    assert len(DESIGN_SHA) == 64


def test_synthetic_matrix_smoke_is_deterministic_without_historical_source():
    design = load_design()
    frames = fixture_frames("ORB")
    states = {
        cell_id("EUR_USD", "LONDON", s, c): {"cash": "1000", "known": True}
        for s in design["strategies"]
        for c in ("C0", "C1", "C2", "C3")
    }
    task = (OPEN.date(), "EUR_USD", "LONDON", frames, META, states, design, "synthetic-only", [])
    first, _ = worker(task)
    second, _ = worker(task)
    assert first == second and len(first) == 16


def test_unknown_capital_cannot_be_restarted_as_1000_or_zero_pnl():
    design = load_design()
    frames = fixture_frames()
    states = {
        cell_id("EUR_USD", "LONDON", s, c): {"cash": "999.99", "known": False}
        for s in design["strategies"]
        for c in ("C0", "C1", "C2", "C3")
    }
    outputs, _ = worker(
        (OPEN.date(), "EUR_USD", "LONDON", frames, META, states, design, "synthetic", [])
    )
    assert all(
        row["status"] == "BLOCKED_UNKNOWN_CAPITAL" and "execution" not in row
        for _, _, row in outputs
    )


def metric_row(day, pnl):
    return {
        "day": str(day),
        "split": "holdout",
        "status": "VALID_ASSUMED_EXECUTION",
        "starting_cash": "1000",
        "execution": {
            "trade": {
                "net_gbp": str(pnl),
                "position": {"opened_at": str(OPEN)},
                "exited_at": str(OPEN + MINUTE),
            },
            "entries": 1,
            "invalidated": 0,
            "curve": [[str(OPEN), "1000"], [str(OPEN + MINUTE), str(D(1000) + pnl)]],
            "cost_bridge": {
                "gross_model_pnl_gbp": str(pnl + 1),
                "total_modeled_costs_gbp": "1",
                "commission_gbp": "0.1",
                "price_pnl_before_commission_gbp": str(pnl + D(".1")),
            },
        },
    }


def test_metrics_reconcile_losses_costs_and_unknown_positions():
    rows = [metric_row(date(2026, 6, 1), D(2)), metric_row(date(2026, 6, 2), D(-3))]
    rows.append(
        {
            "day": "2026-06-03",
            "split": "holdout",
            "status": "INVALID_UNRESOLVED_PATH",
            "starting_cash": "999",
            "execution": {"trade": None, "entries": 1, "invalidated": 1},
        }
    )
    result = summarize(rows, "holdout", load_design())
    assert result["complete_case_net_pnl_gbp"] == "-1"
    assert result["portfolio_net_pnl_gbp"] is None
    assert result["invalidated_positions"] == 1 and result["trade_count"] == 2
    assert D(result["gross_model_pnl_gbp"]) - D(result["total_modeled_costs_gbp"]) == D(-1)


def test_uncertainty_uses_calendar_week_clusters_and_sparse_results_have_none():
    rows = [
        metric_row(date(2026, 6, 1) + timedelta(weeks=n), D(2 if n % 2 else -1)) for n in range(30)
    ]
    result = summarize(rows, "holdout", load_design())
    ci = result["uncertainty"]
    assert ci["weeks"] == 30
    assert D(ci["lower"]) <= D(result["expectancy_gbp"]) <= D(ci["upper"])
    assert summarize(rows[:4], "holdout", load_design())["uncertainty"] is None


def test_local_evidence_detects_compressed_row_tampering():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE sessions(cell,day,split,status,payload,checksum)")
    row = {"cell": "fixture", "day": "2026-06-01", "split": "holdout", "status": "EXCLUDED_SESSION"}
    write_row(db, row)
    assert list(read_rows(db, "fixture")) == [row]
    db.execute("UPDATE sessions SET checksum='changed'")
    with pytest.raises(AtlasError, match="CHECKSUM_MISMATCH"):
        list(read_rows(db, "fixture"))


@pytest.mark.parametrize("pair,scale", [("EUR_USD", "1"), ("GBP_USD", "1.13"), ("USD_JPY", "135")])
@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("strategy", ["ORB", "MOMENTUM", "MEAN_REVERSION", "DIRECTIONAL"])
@pytest.mark.parametrize("scenario", ["C0", "C1", "C2", "C3"])
def test_all_pairs_directions_and_costs_preserve_full_engine_economics(
    pair, scale, side, strategy, scenario
):
    with localcontext(arithmetic_context()):
        transformed = []
        for f in fixture_frames(strategy, side):
            original = next(o for o in f.observations if o.instrument == "EUR_USD")
            value = original.candle.model_dump(mode="json")
            for part in ("bid", "ask", "mid"):
                value[part] = {k: str(D(v) * D(scale)) for k, v in value[part].items()}
            replaced = Observation(
                instrument=pair,
                candle=FxCandle.model_validate(value),
                dataset_id="ARTIFICIAL_SCALED",
                quality_id="ARTIFICIAL_QUALITY",
            )
            transformed.append(
                Frame(
                    start=f.start,
                    observations=tuple(
                        replaced if o.instrument == pair else o for o in f.observations
                    ),
                )
            )
        cfg = settings_for(scenario)
        meta = next(m for m in META if m.name == pair)
        intent, index, _ = candidate(transformed, pair, strategy, meta, cfg, window(spec(), cfg))
        complete = replay(cfg, spec(), META, transformed, (intent,) if intent else ())
        fast = evaluate_intent(cfg, spec(), META, transformed, intent, index, "artificial-scaled")
    assert fast["status"] == complete.status
    assert fast["cash_partial"] == str(complete.cash_gbp)
    assert fast["trade"] == (jsonable(complete.trades[0]) if complete.trades else None)


def test_drawdown_uses_marked_sequence_not_only_winning_closed_trades():
    row = metric_row(date(2026, 6, 1), D(2))
    row["execution"]["curve"] = [
        [str(OPEN), "1000"],
        [str(OPEN + MINUTE), "995"],
        [str(OPEN + 2 * MINUTE), "1002"],
    ]
    assert D(summarize([row], "holdout", load_design())["maximum_marked_drawdown_gbp"]) == 5


def test_classification_requires_every_preregistered_positive_condition():
    design = load_design()
    rows = [metric_row(date(2026, 6, 1) + timedelta(days=n), D(2)) for n in range(101)]
    metric = summarize(rows, "holdout", design)
    cells = [
        {
            "pair": "EUR_USD",
            "session": "LONDON",
            "strategy": "ORB",
            "scenario": c,
            "status": "EVALUATED",
            "splits": {"validation": dict(metric), "holdout": dict(metric)},
        }
        for c in ("C0", "C1", "C2", "C3")
    ]
    assert classify(cells, "EUR_USD", "LONDON", "ORB", design) == "PROMISING"
    cells[1]["splits"]["holdout"]["expectancy_gbp"] = "-1"
    assert classify(cells, "EUR_USD", "LONDON", "ORB", design) == "INCONCLUSIVE"
    cells[0]["splits"]["holdout"]["capital_path_unknown"] = True
    assert classify(cells, "EUR_USD", "LONDON", "ORB", design) == "INCONCLUSIVE"
