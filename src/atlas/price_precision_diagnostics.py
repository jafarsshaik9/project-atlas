"""Offline paired observation-gate probes; not portfolio trade simulations."""

from collections import Counter

from atlas.sessions import bar_errors
from atlas.strategy import ORBStrategy


def precision_probe(instrument, bars, session, config=None):
    """Compare the removed OHLC grid gate with the current observation contract.

    Each evaluation uses only its visible prefix. Legacy reasons reconstruct the
    old gate at its original precedence, without rounding prices or simulating
    portfolio/risk decisions. Counts of evaluations are not counts of trades.
    """
    strategy = ORBStrategy(config)
    count = strategy.config.opening_range_minutes // strategy.config.candle_minutes
    ordered = sorted(bars, key=lambda b: (b.visible_time, b.start))
    off_grid = {
        b.start
        for b in bars
        if any(not instrument.valid_price(p) for p in (b.open, b.high, b.low, b.close))
    }
    opening = sorted(bars, key=lambda b: b.start)[:count]
    complete = len(opening) == count and not bar_errors(
        tuple(opening), session, max(b.visible_time for b in opening), instrument.instrument_id
    )
    old, new = Counter(), Counter()
    tick_rejections = sole_tick = additional_signals = 0
    transitions = []
    for i, bar in enumerate(ordered):
        visible = tuple(ordered[: i + 1])
        result = strategy.evaluate(instrument, visible, session, bar.visible_time)
        reasons = result.reasons or ("SIGNAL",)
        tick = (
            session.contains(bar.visible_time)
            and not bar_errors(visible, session, bar.visible_time, instrument.instrument_id)
            and any(b.start in off_grid for b in visible)
        )
        legacy = ("STRATEGY_DATA_QUALITY",) if tick else reasons
        old.update(legacy)
        new.update(reasons)
        tick_rejections += bool(tick)
        sole_tick += bool(tick and "STRATEGY_DATA_QUALITY" not in reasons)
        additional_signals += bool(tick and result.signal)
        transitions.append(
            {"at": bar.visible_time.isoformat(), "legacy": list(legacy), "corrected": list(reasons)}
        )
    return {
        "scope": "PAIRED_STRATEGY_PROBE_NOT_PORTFOLIO_DECISIONS",
        "evaluated_candles": len(bars),
        "off_grid_candles": len(off_grid),
        "opening_complete": bool(complete),
        "opening_off_grid": bool(complete and any(b.start in off_grid for b in opening)),
        "tick_gate_rejections": tick_rejections,
        "sole_tick_rejections": sole_tick,
        "additional_signals": additional_signals,
        "legacy_reasons": dict(old),
        "corrected_reasons": dict(new),
        "transitions": transitions,
    }
