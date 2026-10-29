"""Quiescent-prefix specialization of the accepted engine, not an alternative fill model.

Exactly one intent is possible. Before it exists there is no position/order to
manage; after it resolves flat there can be no later entry. Full-session source
quality is reported separately. Tests compare economic results to full replay.
"""

from collections import Counter
from decimal import ROUND_FLOOR, Decimal, localcontext

from atlas.forex.execution.contracts import MINUTE, Overlay, arithmetic_context
from atlas.forex.execution.engine import GBP_UNIT, _Engine, jsonable
from atlas.forex.execution.market import quote_stress, reason
from atlas.market_data.contracts import digest


def evaluate_intent(settings, run, instruments, frames, intent, index, source_hash):
    with localcontext(arithmetic_context()):
        engine = _Engine(settings, run, instruments, Overlay())
        engine.configuration["intents"] = [intent.model_dump(mode="json")] if intent else []
        engine.configuration["specialization"] = "ONE_INTENT_QUIESCENT_PREFIX_V1"
        engine.configuration["full_source_frame_hash"] = source_hash
        engine.binding = digest(engine.configuration)
        engine.chain = engine.binding
        curve = [[engine.window.opening.isoformat(), str(engine.cash)]]
        exposure = []
        if intent:
            for f in frames[:index]:
                if f.start >= intent.evidence_start:
                    for o in f.observations:
                        if o.instrument == intent.instrument and reason(o, engine.overlay) is None:
                            engine.history[o.instrument, f.start] = o
            for j in range(index, len(frames)):
                f = frames[j]
                engine.process(f, (intent,) if j == index else ())
                available = {
                    o.instrument: o for o in f.observations if reason(o, engine.overlay) is None
                }
                if not engine.unresolved:
                    marked_exposure, equity = engine.mark(available)
                    curve.append([engine.now.isoformat(), str(equity)])
                    exposure.append(str(marked_exposure))
                if not engine.positions and not engine.pending:
                    break
        if engine.positions or engine.pending:
            raise ValueError("Research specialization did not reach flat or unresolved state")
        trade = engine.trades[0] if engine.trades else None
        if len(engine.trades) > 1:
            raise ValueError("Frozen one-entry rule violated")
        bridge = None
        if trade:
            start_index = {f.start + MINUTE: f for f in frames}
            entry_frame, exit_frame = (
                start_index[trade.position.opened_at],
                start_index[trade.exited_at],
            )
            meta = engine.instruments[intent.instrument]
            entry_bar = next(
                o.candle for o in entry_frame.observations if o.instrument == intent.instrument
            )
            exit_bar = next(
                o.candle for o in exit_frame.observations if o.instrument == intent.instrument
            )
            entry_half = (entry_bar.ask.c - entry_bar.bid.c) / 2 + quote_stress(
                entry_bar, meta, settings.costs
            )
            exit_half = (exit_bar.ask.c - exit_bar.bid.c) / 2 + quote_stress(
                exit_bar, meta, settings.costs
            )
            slip = settings.costs.slippage_pips * meta.pip
            modeled_quote_cost = trade.position.intent.units * (entry_half + exit_half + 2 * slip)
            neutral_quote = trade.gross_quote + modeled_quote_cost
            available = {o.instrument: o for o in exit_frame.observations}
            gbp_mid = (available["GBP_USD"].candle.bid.c + available["GBP_USD"].candle.ask.c) / 2
            midpoint_rate = Decimal(1) / gbp_mid
            if meta.quote == "JPY":
                jpy_mid = (
                    available["USD_JPY"].candle.bid.c + available["USD_JPY"].candle.ask.c
                ) / 2
                midpoint_rate /= jpy_mid
            gross_reference = (neutral_quote * midpoint_rate).quantize(
                GBP_UNIT, rounding=ROUND_FLOOR
            )
            bridge = {
                "gross_model_pnl_gbp": str(gross_reference),
                "total_modeled_costs_gbp": str(gross_reference - trade.net_gbp),
                "commission_gbp": str(trade.position.entry_fee_gbp + trade.exit_fee_gbp),
                "price_pnl_before_commission_gbp": str(trade.gross_gbp),
                "convention": "ASSUMED_SYMMETRIC_ENDPOINT_MODEL_BRIDGE_NOT_OBSERVED_INTRABAR_COST",
            }
        events = engine.events
        entry_count = sum(e.kind == "ENTRY" for e in events)
        result = {
            "status": "INVALID_UNRESOLVED_PATH" if engine.invalid else "VALID_ASSUMED_EXECUTION",
            "cash_partial": str(engine.cash),
            "net_pnl_gbp": None
            if engine.invalid
            else str(engine.cash - settings.starting_capital_gbp),
            "trade": jsonable(trade) if trade else None,
            "cost_bridge": bridge,
            "events": jsonable(events),
            "curve": curve,
            "marked_exposure_gbp": exposure,
            "signals": int(intent is not None),
            "entries": entry_count,
            "rejected": sum(e.kind == "REJECT" for e in events),
            "invalidated": len(engine.unresolved),
            "unresolved_positions": jsonable(tuple(engine.unresolved.values())),
            "execution_reason_counts": dict(Counter(e.reason for e in events if e.reason)),
            "configuration": engine.configuration,
            "execution_input_hash": engine.chain,
        }
        result["checksum"] = digest(result)
        return result
