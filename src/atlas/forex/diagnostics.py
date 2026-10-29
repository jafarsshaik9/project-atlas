"""Read-only accounting diagnostics and hypothetical capital arithmetic, never execution."""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext

D = Decimal
GBP = D(".00000001")
EQUITY = D(1000)
RISK = D(5)
ADVERSE_GAP = D(50)
COMMISSION = D(".00005")
UNIT_STEP = D(1)


@dataclass(frozen=True)
class CloseQuote:
    bid: Decimal
    ask: Decimal

    def __post_init__(self):
        if not (self.bid.is_finite() and self.ask.is_finite() and 0 < self.bid <= self.ask):
            raise ValueError("Positive, finite, uncrossed indicative quotes required")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


def attribution(
    trade: dict,
    costs: dict,
    entry: CloseQuote,
    exit_: CloseQuote,
    gbp: CloseQuote,
    jpy: CloseQuote | None = None,
) -> dict:
    """Decompose the frozen endpoint-cost bridge at unchanged trades, size and times.

    Spread attribution at contingent exits is a reference convention, not a
    measured intrabar spread. This cannot estimate a strategy without costs.
    """
    with localcontext() as ctx:
        ctx.prec = 34
        p = trade["position"]
        pair = p["intent"]["instrument"]
        units = D(p["intent"]["units"])
        if not units.is_finite() or units <= 0 or pair not in ("EUR_USD", "GBP_USD", "USD_JPY"):
            raise ValueError("Unsupported pair or size")
        multiplier = D(costs["spread_multiplier"])
        slip = D(costs["slippage_pips"])
        if not multiplier.is_finite() or not slip.is_finite() or multiplier < 1 or slip < 0:
            raise ValueError("Invalid diagnostic cost assumptions")
        pip = D(".01") if pair == "USD_JPY" else D(".0001")
        if pair == "USD_JPY" and jpy is None:
            raise ValueError("Current JPY conversion quote required")
        midpoint_rate = D(1) / gbp.mid
        if jpy is not None and pair == "USD_JPY":
            midpoint_rate /= jpy.mid
        quote = D(trade["gross_quote"])
        direction = D(1) if p["intent"]["side"] == "long" else D(-1)
        if (
            p["intent"]["side"] not in ("long", "short")
            or quote != (D(trade["exit_price"]) - D(p["entry"])) * units * direction
        ):
            raise ValueError("Stored price/size/direction mismatch")
        side_rate = D(1) / (gbp.ask if quote >= 0 else gbp.bid)
        if pair == "USD_JPY":
            side_rate = D(1) / (
                (gbp.ask if quote >= 0 else gbp.bid) * (jpy.ask if quote >= 0 else jpy.bid)
            )
        if side_rate != D(trade["conversion_rate"]):
            raise ValueError("Stored current directional GBP conversion mismatch")
        spread_quote = units * (entry.ask - entry.bid + exit_.ask - exit_.bid) / 2
        stress_quote = spread_quote * (multiplier - 1)
        slippage_quote = 2 * units * slip * pip
        neutral_quote = quote + spread_quote + stress_quote + slippage_quote
        gross = (neutral_quote * midpoint_rate).quantize(GBP, rounding=ROUND_FLOOR)
        settled = (quote * side_rate).quantize(GBP, rounding=ROUND_FLOOR)
        commission = D(p["entry_fee_gbp"]) + D(trade["exit_fee_gbp"])
        if settled != D(trade["gross_gbp"]) or settled - commission != D(trade["net_gbp"]):
            raise ValueError("Stored settlement/commission mismatch")
        base = spread_quote * midpoint_rate
        stress = stress_quote * midpoint_rate
        slippage = slippage_quote * midpoint_rate
        conversion = quote * (midpoint_rate - side_rate)
        # Includes finite-precision and eight-decimal settlement reconciliation.
        rounding = gross - settled - base - stress - slippage - conversion
        return {
            "gross_reference_gbp": gross,
            "indicative_endpoint_spread_gbp": base,
            "additional_spread_stress_gbp": stress,
            "assumed_slippage_gbp": slippage,
            "directional_conversion_gbp": conversion,
            "settlement_rounding_gbp": rounding,
            "recorded_commission_gbp": commission,
            "recorded_price_pnl_gbp": settled,
            "recorded_net_gbp": D(trade["net_gbp"]),
        }


def spread_bet(
    *,
    stake: Decimal,
    price: Decimal,
    point: Decimal,
    margin_rate: Decimal,
    stop_points: Decimal,
    spread_points: Decimal,
    slippage_per_side: Decimal,
    equity: Decimal = EQUITY,
    risk: Decimal = RISK,
    gap_points: Decimal = ADVERSE_GAP,
    guaranteed_premium_points: Decimal | None = None,
) -> dict:
    """Hypothetical GBP-per-point contract; stop is a mid-reference move.

    Reserve one full spread and both adverse slippages in addition to the move.
    A stop measured from an actual executable entry must not add spread again.
    No broker order, margin permission or guaranteed-stop availability is implied.
    """
    values = (stake, price, point, margin_rate, stop_points, equity, risk)
    if any(not v.is_finite() or v <= 0 for v in values) or margin_rate > 1:
        raise ValueError("Positive finite contract inputs and valid margin required")
    if any(not v.is_finite() or v < 0 for v in (spread_points, slippage_per_side, gap_points)):
        raise ValueError("Invalid costs or adverse gap")
    if guaranteed_premium_points is not None and (
        not guaranteed_premium_points.is_finite() or guaranteed_premium_points < 0
    ):
        raise ValueError("Invalid guaranteed premium")
    with localcontext() as ctx:
        ctx.prec = 34
        notional = stake * price / point
        margin = notional * margin_rate
        friction = stake * (spread_points + 2 * slippage_per_side)
        planned = stake * stop_points + friction
        max_stop = risk / stake - spread_points - 2 * slippage_per_side
        guarantee_reserve = stake * (guaranteed_premium_points or D(0))
        result = {
            "notional_equivalent_gbp": notional,
            "required_margin_gbp": margin,
            "margin_utilization": margin / equity,
            "loss_per_point_gbp": stake,
            "spread_cost_gbp": stake * spread_points,
            "spread_fraction_of_risk": stake * spread_points / risk,
            "assumed_roundtrip_slippage_gbp": 2 * stake * slippage_per_side,
            "planned_loss_gbp": planned,
            "max_mid_reference_stop_points": max_stop,
            "loss_with_additional_gap_gbp": planned + stake * gap_points,
            "account_level_closeout_equity_gbp": margin / 2,
            "risk_feasible": planned <= risk,
            "conditional_guaranteed_premium_reserve_gbp": guarantee_reserve,
            "margin_feasible_with_cost_buffer": margin + friction + guarantee_reserve < equity,
        }
        if guaranteed_premium_points is not None:
            # Conditional illustration: no adverse gap/exit slippage under an honored guarantee.
            result["conditional_guaranteed_loss_gbp"] = stake * (
                stop_points + spread_points + slippage_per_side + guaranteed_premium_points
            )
            result["conditional_guaranteed_risk_feasible"] = (
                result["conditional_guaranteed_loss_gbp"] <= risk
            )
        return result


def unleveraged(
    *,
    price: Decimal,
    quote_per_gbp: Decimal,
    pip: Decimal,
    minimum_units: Decimal,
    unit_step: Decimal = UNIT_STEP,
    stop_pips: Decimal,
    spread_pips: Decimal,
    slip_per_side: Decimal,
    commission_per_unit_side: Decimal = COMMISSION,
    equity: Decimal = EQUITY,
    risk: Decimal = RISK,
) -> dict:
    """Whole-unit collateral illustration, not a deliverable FX funding implementation."""
    positive = (price, quote_per_gbp, pip, minimum_units, unit_step, stop_pips, equity, risk)
    if (
        any(not v.is_finite() or v <= 0 for v in positive)
        or minimum_units % unit_step
        or unit_step % 1
    ):
        raise ValueError("Positive finite inputs and integer unit minimum required")
    if any(
        not v.is_finite() or v < 0 for v in (spread_pips, slip_per_side, commission_per_unit_side)
    ):
        raise ValueError("Invalid unleveraged costs")
    with localcontext() as ctx:
        ctx.prec = 34
        notional_per_unit = price / quote_per_gbp
        pip_per_unit = pip / quote_per_gbp
        friction_per_unit = (spread_pips + 2 * slip_per_side) * pip_per_unit + (
            2 * commission_per_unit_side
        )
        risk_per_unit = stop_pips * pip_per_unit + friction_per_unit
        units = min(equity / (notional_per_unit + friction_per_unit), risk / risk_per_unit)
        units = (units / unit_step).to_integral_value(rounding=ROUND_FLOOR) * unit_step
        return {
            "whole_units": units,
            "pip_value_gbp": units * pip_per_unit,
            "collateral_gbp": units * notional_per_unit,
            "planned_loss_gbp": units * risk_per_unit,
            "roundtrip_cost_gbp": units * friction_per_unit,
            "feasible": units >= minimum_units,
            "minimum_units": minimum_units,
            "unit_step": unit_step,
            "minimum_collateral_gbp": minimum_units * notional_per_unit,
        }
