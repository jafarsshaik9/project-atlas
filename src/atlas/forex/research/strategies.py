"""Exactly four frozen causal signal rules; one attempt per pair/session."""

from collections import Counter
from decimal import Decimal

from atlas.forex.execution.contracts import MINUTE, EntryRule, Intent, Overlay
from atlas.forex.execution.market import entry_reason, reason
from atlas.market_data.contracts import digest

EMPTY_OVERLAY = Overlay()


def candidate(frames, pair, strategy, meta, settings, win, overlay=EMPTY_OVERLAY):
    """Only inspect prefixes until the first qualifying signal. No entry or exit prices read."""
    own, bad = [], []
    reasons = Counter()
    for index, frame in enumerate(frames):
        now = frame.start + MINUTE
        if now > win.last_signal_at:
            break
        o = next((o for o in frame.observations if o.instrument == pair), None)
        own.append(o)
        bad.append(reason(o, overlay) or (entry_reason(o, meta, settings) if o else None))
        if strategy in ("ORB", "DIRECTIONAL") and index < 15:
            continue
        if strategy in ("MOMENTUM", "MEAN_REVERSION") and index < 20:
            continue
        first = 0 if strategy == "ORB" else index if strategy == "DIRECTIONAL" else index - 20
        issues = [v for v in bad[first : index + 1] if v]
        if issues:
            reasons.update(issues)
            if strategy == "ORB" and any(bad[:15]):
                reasons["ORB_INCOMPLETE_OR_INELIGIBLE_OPENING_RANGE"] += 1
                break
            continue
        bar = own[index].candle
        close, reference, side = bar.mid.c, None, None
        if strategy == "ORB":
            high = max(o.candle.mid.h for o in own[:15])
            low = min(o.candle.mid.l for o in own[:15])
            if high <= low:
                reasons["ORB_ZERO_WIDTH"] += 1
                break
            side = "long" if close > high else "short" if close < low else None
            stop = low if side == "long" else high
            rule = EntryRule(kind="ORB")
        elif strategy == "MOMENTUM":
            reference = own[index - 20].candle.mid.c
            side = (
                "long"
                if close >= reference * Decimal("1.001")
                else ("short" if close <= reference * Decimal(".999") else None)
            )
            rule = EntryRule(kind="PERCENT")
        elif strategy == "MEAN_REVERSION":
            reference = sum((o.candle.mid.c for o in own[index - 20 : index]), Decimal(0)) / 20
            side = (
                "long"
                if close <= reference * Decimal(".9985")
                else ("short" if close >= reference * Decimal("1.0015") else None)
            )
            rule = EntryRule(kind="REFERENCE", frozen_reference=reference)
        elif strategy == "DIRECTIONAL":
            side = "long" if close > bar.mid.o else "short" if close < bar.mid.o else None
            rule = EntryRule(kind="PERCENT")
            if side is None:
                reasons["DIRECTIONAL_FIRST_ELIGIBLE_DOJI"] += 1
                break
        else:
            raise ValueError("Unknown frozen strategy")
        if side is None:
            continue
        direction = Decimal(1) if side == "long" else Decimal(-1)
        if strategy != "ORB":
            stop = close * (1 - direction * Decimal(".0015"))
        target = (
            close + direction * abs(close - stop) * Decimal("1.5")
            if strategy == "ORB"
            else reference
            if strategy == "MEAN_REVERSION"
            else close * (1 + direction * Decimal(".00225"))
        )
        if stop <= 0 or target <= 0:
            reasons["SIGNAL_NONPOSITIVE_PREVIEW_BRACKET"] += 1
            break
        intent = Intent(
            identity=digest([str(win.opening), pair, strategy, side]),
            instrument=pair,
            side=side,
            units=meta.maximum_units or Decimal("1000000000"),
            stop=stop,
            target=target,
            signal_at=now,
            information_cutoff=now,
            evidence_start=own[first].candle.start,
            entry_rule=rule,
        )
        return intent, index, dict(reasons)
    reasons["NO_ELIGIBLE_FROZEN_SIGNAL"] += 1
    return None, None, dict(reasons)
