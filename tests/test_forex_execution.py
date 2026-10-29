"""Artificial intraday execution only. No accepted history or broker access."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError
from test_forex_oanda import instrument

from atlas.config import AtlasError
from atlas.forex.execution.calendar import market_reason, window
from atlas.forex.execution.contracts import (
    MINUTE,
    Annotation,
    Costs,
    Frame,
    Intent,
    Observation,
    Overlay,
    Run,
    Settings,
)
from atlas.forex.execution.engine import replay
from atlas.forex.execution.market import conversion, frames_from_partitions
from atlas.forex.execution.primitives import opening_range
from atlas.forex.models import FxCandle
from atlas.forex.sessions import local_instant
from atlas.market_data.contracts import digest

D = Decimal
META = tuple(instrument(s) for s in ("EUR_USD", "GBP_USD", "USD_JPY"))
DAY = date(2025, 2, 10)
OPEN = datetime(2025, 2, 10, 8, tzinfo=UTC)


def settings(**changes):
    costs = changes.pop("costs", {})
    return Settings(
        **(
            {
                "costs": Costs(
                    **(
                        {
                            "provenance": "ARTIFICIAL_DECLARED_COSTS",
                            "known_at": OPEN - timedelta(days=10),
                            "slippage_pips": D(0),
                            "commission_gbp_per_unit_per_side": D(0),
                            "spread_floor_pips": D(0),
                            "additional_spread_pips": D(0),
                        }
                        | costs
                    )
                ),
                "max_holding_minutes": 1,
            }
            | changes
        )
    )


def spec(**changes):
    return Run(
        **(
            {
                "day": DAY,
                "session": "LONDON",
                "coverage_start": OPEN - timedelta(hours=1),
                "coverage_end": OPEN + timedelta(hours=14),
                "code_version": "synthetic-engine-v1",
                "calendar_provenance": "ASSUMED_CONSERVATIVE_RESEARCH_WINDOWS_V1",
                "origin": "ARTIFICIAL_OFFLINE_FIXTURE",
            }
            | changes
        )
    )


def observation(
    stamp,
    name="EUR_USD",
    *,
    bid="1.10000",
    ask="1.10020",
    low=None,
    high=None,
    opening=None,
    complete=True,
    missing=None,
):
    if name == "GBP_USD" and bid == "1.10000":
        bid, ask = "1.25000", "1.25020"
    if name == "USD_JPY" and bid == "1.10000":
        bid, ask = "150.000", "150.020"
    bid, ask = D(bid), D(ask)
    width = ask - bid
    opening = D(opening) if opening is not None else bid
    low = D(low) if low is not None else min(opening, bid)
    high = D(high) if high is not None else max(opening, bid)
    parts = {}
    for key, shift in (("bid", D(0)), ("ask", width), ("mid", width / 2)):
        parts[key] = (
            {
                "o": str(opening + shift),
                "h": str(high + shift),
                "l": str(low + shift),
                "c": str(bid + shift),
            }
            if key != missing
            else None
        )
    bar = FxCandle(
        source_time=stamp.strftime("%Y-%m-%dT%H:%M:00Z"),
        start=stamp,
        complete=complete,
        activity_count=42,
        **parts,
    )
    return Observation(
        instrument=name, candle=bar, dataset_id="ARTIFICIAL_SOURCE", quality_id="ARTIFICIAL_QUALITY"
    )


def frame(stamp, changes=None, absent=()):
    changes = changes or {}
    return Frame(
        start=stamp,
        observations=tuple(
            observation(stamp, m.name, **changes.get(m.name, {}))
            for m in META
            if m.name not in absent
        ),
    )


def order(**changes):
    return Intent(
        **(
            {
                "identity": "order1",
                "instrument": "EUR_USD",
                "side": "long",
                "units": D(10),
                "stop": D("1.09"),
                "target": D("1.11"),
                "signal_at": OPEN + MINUTE,
                "information_cutoff": OPEN + MINUTE,
                "evidence_start": OPEN,
            }
            | changes
        )
    )


def execute(frames=None, orders=None, config=None, run=None, overlay=None):
    return replay(
        config or settings(),
        run or spec(),
        META,
        frames if frames is not None else tuple(frame(OPEN + i * MINUTE) for i in range(3)),
        orders if orders is not None else (order(),),
        **({"overlay": overlay} if overlay else {}),
    )


@pytest.mark.parametrize(
    "side,expected_entry,expected_exit",
    [("long", "1.10020", "1.10000"), ("short", "1.10000", "1.10020")],
)
def test_direction_spread_and_timing(side, expected_entry, expected_exit):
    intent = order(
        side=side,
        stop=D("1.11") if side == "short" else D("1.09"),
        target=D("1.09") if side == "short" else D("1.11"),
    )
    result = execute(orders=(intent,))
    (trade,) = result.trades
    assert trade.position.entry == D(expected_entry)
    assert trade.exit_price == D(expected_exit)
    assert trade.net_gbp == D("-.0016")
    assert (
        intent.signal_at < trade.position.eligible_at <= trade.position.opened_at < trade.exited_at
    )
    assert trade.position.opened_at == OPEN + 2 * MINUTE
    assert result.cash_gbp == D(1000) + trade.net_gbp
    assert result.status == "VALID_ASSUMED_EXECUTION"


def test_slippage_and_commissions_are_explicit_and_reconcile():
    result = execute(
        config=settings(
            costs={"slippage_pips": D(1), "commission_gbp_per_unit_per_side": D(".001")}
        )
    )
    (trade,) = result.trades
    assert trade.position.entry == D("1.10030")
    assert trade.exit_price == D("1.09990")
    assert trade.position.entry_fee_gbp == trade.exit_fee_gbp == D(".01")
    assert trade.net_gbp == D("-.0232")


@pytest.mark.parametrize(
    "side,changes,expected,code",
    [
        ("long", {"low": "1.089"}, "1.09", "FX_STOP"),
        ("long", {"high": "1.111"}, "1.11", "FX_TARGET_NO_PRICE_IMPROVEMENT"),
        ("long", {"opening": "1.08", "low": "1.08"}, "1.08", "FX_STOP"),
        ("short", {"high": "1.12"}, "1.11", "FX_STOP"),
        ("short", {"low": "1.08"}, "1.09", "FX_TARGET_NO_PRICE_IMPROVEMENT"),
        ("short", {"opening": "1.12", "high": "1.12"}, "1.12020", "FX_STOP"),
    ],
)
def test_contingent_exits_and_gap_through(side, changes, expected, code):
    intent = order(
        side=side,
        stop=D("1.11") if side == "short" else D("1.09"),
        target=D("1.09") if side == "short" else D("1.11"),
    )
    frames = [frame(OPEN), frame(OPEN + MINUTE), frame(OPEN + 2 * MINUTE, {"EUR_USD": changes})]
    result = execute(frames, (intent,))
    assert result.trades[0].exit_price == D(expected)
    assert result.trades[0].reason == code


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("policy", ["STOP_FIRST_V1", "INVALIDATE_V1"])
def test_stop_target_collision_is_never_target_first(side, policy):
    intent = order(
        side=side,
        stop=D("1.11") if side == "short" else D("1.09"),
        target=D("1.09") if side == "short" else D("1.11"),
    )
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, {"EUR_USD": {"low": "1.08", "high": "1.12"}}),
    ]
    result = execute(frames, (intent,), settings(collision=policy))
    if policy == "STOP_FIRST_V1":
        assert result.trades[0].ambiguous and result.trades[0].net_gbp < 0
    else:
        assert not result.trades and result.net_pnl_gbp is None and result.unresolved_positions


@pytest.mark.parametrize("missing", ["EUR_USD", "GBP_USD"])
def test_open_position_gap_invalidates_and_keeps_outstanding_risk(missing):
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, absent=(missing,)),
        frame(OPEN + 3 * MINUTE),
    ]
    result = execute(frames)
    assert result.net_pnl_gbp is None and result.unresolved_positions
    assert not result.trades
    assert any(e.kind == "RESUMPTION_STRESS_ONLY" for e in result.events)


def test_missing_scheduled_entry_is_cancelled_without_deferral():
    result = execute(
        [frame(OPEN), frame(OPEN + MINUTE, absent=("EUR_USD",)), frame(OPEN + 2 * MINUTE)]
    )
    assert not result.trades and not result.unresolved_positions
    assert any(e.reason == "FX_SCHEDULED_EXECUTION_UNAVAILABLE" for e in result.events)


def test_omitted_frames_and_truncated_input_are_fail_closed():
    result = execute([frame(OPEN), frame(OPEN + MINUTE)])
    assert result.net_pnl_gbp is None
    assert result.unresolved_positions[0].opened_at == OPEN + 2 * MINUTE
    skipped = execute([frame(OPEN), frame(OPEN + 2 * MINUTE)])
    assert not skipped.trades


@pytest.mark.parametrize(
    "changes",
    [
        {"missing": "bid"},
        {"missing": "ask"},
        {"complete": False},
        {"bid": "1.1004", "ask": "1.1002"},
    ],
)
def test_ineligible_candles_prevent_new_risk(changes):
    result = execute(
        [frame(OPEN, {"EUR_USD": changes}), frame(OPEN + MINUTE), frame(OPEN + 2 * MINUTE)]
    )
    assert not result.trades and any(e.kind == "REJECT" for e in result.events)


@pytest.mark.parametrize("phase", [0, 1])
def test_high_spread_rejects_signals_or_entries(phase):
    frames = [
        frame(OPEN + i * MINUTE, {"EUR_USD": {"ask": "1.101"}} if i == phase else {})
        for i in range(3)
    ]
    assert not execute(frames).trades


def test_high_spread_never_defers_existing_exit():
    frames = [
        frame(OPEN),
        frame(OPEN + MINUTE),
        frame(OPEN + 2 * MINUTE, {"EUR_USD": {"ask": "1.101"}}),
    ]
    assert execute(frames).trades[0].exited_at == OPEN + 3 * MINUTE


def test_extreme_signal_range_and_overlay_are_separate_source_preserving_gates():
    frames = (frame(OPEN), frame(OPEN + MINUTE), frame(OPEN + 2 * MINUTE))
    before = tuple(f.model_dump_json() for f in frames)
    overlay = Overlay(
        annotations=(
            Annotation(
                instrument="EUR_USD",
                start=OPEN,
                end=OPEN + MINUTE,
                reason="UNRESOLVED_MISSING_MINUTE",
                evidence="audit-v2",
            ),
        )
    )
    assert not execute(frames, overlay=overlay).trades
    assert tuple(f.model_dump_json() for f in frames) == before
    extreme = [frame(OPEN, {"EUR_USD": {"high": "1.2"}}), *frames[1:]]
    assert not execute(extreme).trades


@pytest.mark.parametrize(
    "month,day,expected_lon,expected_ny",
    [(2, 10, 8, 13), (3, 17, 8, 12), (4, 7, 7, 12), (10, 27, 8, 12), (11, 3, 8, 13)],
)
def test_london_new_york_and_overlap_dst(month, day, expected_lon, expected_ny):
    run = spec(
        day=date(2025, month, day),
        coverage_start=datetime(2025, month, day, tzinfo=UTC),
        coverage_end=datetime(2025, month, day, 23, tzinfo=UTC),
    )
    cfg = settings()
    assert window(run, cfg).opening.hour == expected_lon
    assert window(run.model_copy(update={"session": "NEW_YORK"}), cfg).opening.hour == expected_ny
    assert window(run.model_copy(update={"session": "OVERLAP"}), cfg).opening.hour == expected_ny


@pytest.mark.parametrize(
    "day,code", [(date(2025, 2, 8), "WEEKEND"), (date(2025, 12, 25), "HOLIDAY")]
)
def test_nontrading_days_are_rejected(day, code):
    with pytest.raises(AtlasError, match=code):
        window(spec(day=day), settings())


@pytest.mark.parametrize(
    "day,clock,zone",
    [
        (date(2025, 3, 9), time(2, 30), "America/New_York"),
        (date(2025, 11, 2), time(1, 30), "America/New_York"),
        (date(2025, 3, 30), time(1, 30), "Europe/London"),
        (date(2025, 10, 26), time(1, 30), "Europe/London"),
    ],
)
def test_ambiguous_and_nonexistent_local_times(day, clock, zone):
    with pytest.raises(AtlasError):
        local_instant(day, clock, zone)


@pytest.mark.parametrize("hour,minute", [(21, 59), (22, 4), (21, 45)])
def test_closure_warning_and_rollover_boundaries_are_ineligible(hour, minute):
    assert market_reason(datetime(2025, 2, 10, hour, minute, tzinfo=UTC))


def test_incomplete_declared_execution_interval_is_rejected():
    with pytest.raises(AtlasError, match="INCOMPLETE_DECLARED_COVERAGE"):
        execute(run=spec(coverage_end=OPEN + 3 * MINUTE))


@pytest.mark.parametrize("side", ["long", "short"])
def test_jpy_conversions_and_positions(side):
    intent = order(
        instrument="USD_JPY",
        side=side,
        stop=D(149) if side == "long" else D(151),
        target=D(151) if side == "long" else D(149),
    )
    result = execute(orders=(intent,))
    assert result.trades[0].net_gbp < 0
    available = {o.instrument: o for o in frame(OPEN).observations}
    with localcontext() as ctx:
        ctx.prec = 34
        assert conversion("JPY", D(-1), available, at=OPEN + MINUTE) == 1 / (D(150) * D("1.25"))
        assert conversion("JPY", D(1), available, at=OPEN + MINUTE) == 1 / (
            D("150.020") * D("1.25020")
        )


@pytest.mark.parametrize(
    "changes",
    [{"units": D(".5")}, {"units": D("1000001")}, {"units": D(10000)}, {"stop": D("1.2")}],
)
def test_invalid_size_stop_and_exposure_rejections(changes):
    assert not execute(orders=(order(**changes),)).trades


def test_maximum_concurrency_and_deterministic_priority():
    second = order(identity="order2", instrument="GBP_USD", stop=D("1.24"), target=D("1.26"))
    one = execute(orders=(second, order()))
    assert len(one.trades) == 1
    two = execute(orders=(second, order()), config=settings(max_positions=2))
    assert len(two.trades) == 2
    assert execute(orders=(order(), second), config=settings(max_positions=2)) == two


def test_no_signal_can_use_missing_earlier_evidence():
    intent = order(signal_at=OPEN + 2 * MINUTE, information_cutoff=OPEN + 2 * MINUTE)
    frames = [frame(OPEN, absent=("EUR_USD",)), frame(OPEN + MINUTE), frame(OPEN + 2 * MINUTE)]
    assert not execute(frames, (intent,)).trades


def test_entry_never_uses_execution_candle_open_or_earlier_extrema():
    frames = [
        frame(OPEN),
        frame(
            OPEN + MINUTE,
            {"EUR_USD": {"opening": "1.095", "low": "1.095", "bid": "1.10000", "ask": "1.10020"}},
        ),
        frame(OPEN + 2 * MINUTE),
    ]
    trade = execute(frames).trades[0]
    assert trade.position.entry == D("1.10020")
    assert trade.exited_at == OPEN + 3 * MINUTE


def test_future_suffix_does_not_change_earlier_decisions():
    baseline = execute()
    altered = execute(
        [
            frame(OPEN),
            frame(OPEN + MINUTE),
            frame(OPEN + 2 * MINUTE, {"EUR_USD": {"bid": "1.08", "ask": "1.0802"}}),
        ]
    )
    cutoff = OPEN + 2 * MINUTE
    assert [e for e in baseline.events if e.at <= cutoff] == [
        e for e in altered.events if e.at <= cutoff
    ]


def test_identical_hashes_source_immutability_and_ambient_decimal_independence():
    frames = tuple(frame(OPEN + i * MINUTE) for i in range(3))
    before = tuple(f.model_dump_json() for f in frames)
    baseline = execute(frames)
    assert execute(iter(frames)) == baseline
    with localcontext() as ctx:
        ctx.prec = 6
        assert execute(frames) == baseline
    assert tuple(f.model_dump_json() for f in frames) == before
    data = baseline.to_dict()
    checksum = data.pop("checksum")
    assert digest(data) == checksum


@pytest.mark.parametrize(
    "changes",
    [
        {"max_positions": 0},
        {"max_exposure_gbp": D(1001)},
        {"collision": "TARGET_FIRST"},
        {"publication_delay_seconds": 1},
        {"order_latency_seconds": 0},
    ],
)
def test_invalid_and_optimistic_configurations_are_rejected(changes):
    with pytest.raises(ValidationError):
        settings(**changes)


def test_future_costs_duplicate_orders_and_unsorted_frames_are_rejected():
    with pytest.raises(AtlasError, match="FUTURE_COST"):
        execute(config=settings(costs={"known_at": OPEN + MINUTE}))
    with pytest.raises(AtlasError, match="IDENTITIES"):
        execute(orders=(order(), order()))
    with pytest.raises(AtlasError, match="OUT_OF_ORDER"):
        execute([frame(OPEN + MINUTE), frame(OPEN)])


def test_opening_range_primitive_requires_complete_visible_grid():
    cfg = settings()
    obs = tuple(observation(OPEN + i * MINUTE) for i in range(3))
    result = opening_range(obs, META[0], OPEN, 3, OPEN + 3 * MINUTE, cfg)
    assert result.information_cutoff == OPEN + 3 * MINUTE
    with pytest.raises(AtlasError, match="FUTURE_DATA"):
        opening_range(obs, META[0], OPEN, 3, OPEN + 2 * MINUTE, cfg)
    with pytest.raises(AtlasError, match="INCOMPLETE"):
        opening_range(obs[:2], META[0], OPEN, 3, OPEN + 3 * MINUTE, cfg)


def test_partition_adapter_checks_proofs_and_preserves_read_only_sources(tmp_path):
    from test_forex_collection import build, run

    from atlas.forex.dataset import partitions

    a = build(tmp_path)
    run(a)
    bundles = list(partitions(a))[:3]
    start = datetime(2024, 10, 28, tzinfo=UTC)
    merged = list(frames_from_partitions(bundles, start, start + timedelta(days=1)))
    assert len(merged) == 2 and len(merged[0].observations) == 3
    broken = list(bundles)
    identity, manifest, quality, bars = broken[0]
    broken[0] = (identity, manifest, quality, bars[1:])
    with pytest.raises(AtlasError, match="PROOF_MISMATCH"):
        list(frames_from_partitions(broken, start, start + timedelta(days=1)))
