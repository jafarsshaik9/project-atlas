"""Metrics reconcile to fills; display and progress cannot change trading behaviour."""

import copy
from dataclasses import replace
from decimal import Decimal

import pytest

from atlas.config import AtlasError
from atlas.historical_backtest import AvailabilityMode
from atlas.market_data.contracts import digest
from atlas.multi_session_research import run_multi_session
from atlas.research_performance import performance, render_research_html
from tests.test_multi_session_research import build


def reseal(result):
    result.pop("result_checksum", None)
    result["result_checksum"] = digest(result)


def test_metrics_html_and_progress_match_actual_ledger(tmp_path):
    refs = build(tmp_path, symbols=("A",))
    events = []
    result = run_multi_session(refs, allow_artificial=True, progress=events.append)
    assert result == run_multi_session(refs, allow_artificial=True)
    assert len(events) == 3
    metrics = performance(result)
    assert metrics["completed_trades"] == 3
    assert Decimal(metrics["final_cash_gbp"]) == Decimal("1000") + Decimal(
        metrics["net_realised_pnl_gbp"]
    )
    assert metrics["fees_and_costs_charged_gbp"] == result["costs_gbp"]
    assert metrics["maximum_drawdown_gbp"] == result["maximum_drawdown_gbp"]
    page = render_research_html(result, metrics, excluded_dates=("2025-03-10",))
    assert metrics["final_equity_gbp"] in page and "ARTIFICIAL FIXTURES" in page
    assert "2025-03-10" in page and "<script" not in page and "<form" not in page
    changed = dict(metrics, completed_trades=999)
    with pytest.raises(AtlasError, match="display metrics"):
        render_research_html(result, changed)


def test_partial_exits_are_one_completed_trade(tmp_path):
    result = run_multi_session(build(tmp_path, symbols=("A",)), allow_artificial=True)
    expected = performance(result)
    exit_index = next(i for i, f in enumerate(result["fills"]) if f["side"] == "sell")
    original = result["fills"][exit_index]
    parts = []
    for suffix in ("a", "b"):
        part = copy.deepcopy(original)
        part["fill_id"] += suffix
        part["intent_id"] += suffix
        for name in ("quantity", "fees_gbp", "gross_pnl_gbp", "realized_pnl_gbp"):
            part[name] = str(Decimal(part[name]) / 2)
        part["cost_components"] = [(k, str(Decimal(v) / 2)) for k, v in part["cost_components"]]
        parts.append(part)
    result["fills"][exit_index : exit_index + 1] = parts
    result["trade_count"] += 1
    reseal(result)
    actual = performance(result)
    assert actual["completed_trades"] == expected["completed_trades"]
    assert actual["net_realised_pnl_gbp"] == expected["net_realised_pnl_gbp"]
    assert Decimal(actual["fees_and_costs_charged_gbp"]) == Decimal(
        expected["fees_and_costs_charged_gbp"]
    )


@pytest.mark.parametrize("fault", ["checksum", "duplicate", "cash", "drawdown"])
def test_inconsistent_persisted_results_rejected(tmp_path, fault):
    result = run_multi_session(build(tmp_path, symbols=("A",)), allow_artificial=True)
    if fault == "checksum":
        result["ending_equity_gbp"] = "999999"
    elif fault == "duplicate":
        result["fills"].append(copy.deepcopy(result["fills"][-1]))
        reseal(result)
    elif fault == "cash":
        result["ending_equity_gbp"] = "999999"
        reseal(result)
    else:
        result["maximum_drawdown_gbp"] = "999999"
        reseal(result)
    with pytest.raises(AtlasError):
        performance(result)


def test_no_eligible_observations_are_not_invented_trades(tmp_path):
    refs = [
        replace(
            r,
            config=replace(
                r.config, availability_mode=AvailabilityMode.VERIFIED, assumed_delay_seconds=None
            ),
        )
        for r in build(tmp_path, symbols=("A",))
    ]
    result = run_multi_session(refs, allow_artificial=True)
    metrics = performance(result)
    assert metrics["completed_trades"] == 0 and metrics["profit_factor_after_costs"] is None
    assert metrics["win_rate_pct"] is None and metrics["net_unrealised_pnl_gbp"] == "0"
    assert "1000" in render_research_html(result, metrics)
