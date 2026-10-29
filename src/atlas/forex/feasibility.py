"""Artificial-only contract funding arithmetic. No prices, credentials, orders or replay I/O."""

from decimal import (
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from atlas.market_data.contracts import Contract, Money, Positive, digest

D = Decimal
ZERO = D(0)
ONE = D(1)
NONNEG = Field(default=None, ge=0)


def _context():
    return Context(
        prec=40,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


class Evidence(Contract):
    field: str
    status: Literal[
        "VERIFIED_PUBLIC", "VERIFIED_ARTIFICIAL", "ILLUSTRATIVE", "INFERRED", "UNRESOLVED"
    ]
    source: str
    retrieved: Literal["2026-10-29"] = "2026-10-29"
    scope: str


class InstrumentContract(Contract):
    version: Literal["OFFLINE_FX_CONTRACT_V1"] = "OFFLINE_FX_CONTRACT_V1"
    identity: str
    origin: Literal["ARTIFICIAL", "PUBLIC_DOCUMENTATION"]
    provider: str
    account_type: str
    product: Literal["DELIVERABLE_SPOT", "ROLLING_FX_CFD", "FX_SPREAD_BET"]
    pair: Literal["EUR_USD", "GBP_USD", "USD_JPY"]
    quantity_kind: Literal["BASE_UNITS", "LOTS", "GBP_PER_POINT"]
    minimum_quantity: Positive | None = None
    quantity_step: Positive | None = None
    units_per_quantity: Positive | None = None
    point_increment: Positive | None = None
    margin_rate: Positive | None = Field(default=None, le=1)
    maximum_retail_leverage: Positive | None = Field(default=None, le=30)
    minimum_stop_points: Money | None = NONNEG
    commission_per_quantity_side: Money | None = NONNEG
    minimum_commission_side: Money | None = NONNEG
    commission_currency: Literal["GBP", "QUOTE"] | None = None
    conversion_markup: Money | None = Field(default=None, ge=0, le=1)
    long_allowed: StrictBool | None = None
    short_allowed: StrictBool | None = None
    guarantee_available: StrictBool | None = None
    guarantee_premium_points: Money | None = NONNEG
    guarantee_minimum_points: Money | None = NONNEG
    adverse_annual_funding_rate: Money | None = Field(default=None, ge=0, le=10)
    funding_day_basis: Literal[360, 365] | None = None
    evidence: tuple[Evidence, ...]

    @model_validator(mode="after")
    def conventions(self):
        if self.minimum_quantity is not None and self.quantity_step is not None:
            if self.minimum_quantity % self.quantity_step:
                raise ValueError("Explicit zero-origin quantity grid required")
        if self.quantity_kind == "BASE_UNITS" and self.units_per_quantity not in (None, ONE):
            raise ValueError("Base units cannot silently become lots")
        if self.quantity_kind == "GBP_PER_POINT" and self.product != "FX_SPREAD_BET":
            raise ValueError("GBP stake requires a spread-bet contract")
        if self.product == "FX_SPREAD_BET" and self.quantity_kind != "GBP_PER_POINT":
            raise ValueError("This model requires explicit GBP stake normalization")
        if self.quantity_kind == "GBP_PER_POINT" and self.units_per_quantity is not None:
            raise ValueError("A stake is not a base-unit quantity")
        fields = [e.field for e in self.evidence]
        if len(fields) != len(set(fields)):
            raise ValueError("Duplicate field evidence")
        if self.origin == "PUBLIC_DOCUMENTATION" and any(
            e.status == "VERIFIED_ARTIFICIAL" for e in self.evidence
        ):
            raise ValueError("Artificial evidence cannot verify a public contract")
        if self.origin == "ARTIFICIAL" and any(
            e.status == "VERIFIED_PUBLIC" for e in self.evidence
        ):
            raise ValueError("Artificial fixture cannot impersonate public verification")
        return self

    def verified(self, field: str) -> bool:
        required = "VERIFIED_ARTIFICIAL" if self.origin == "ARTIFICIAL" else "VERIFIED_PUBLIC"
        return any(e.field == field and e.status == required for e in self.evidence)


class Scenario(Contract):
    identity: str
    quantity: Positive
    side: Literal["long", "short"] = "long"
    reference_price: Positive
    # Conversion quote is units of this instrument's quote currency per GBP.
    quote_per_gbp_bid: Positive
    quote_per_gbp_ask: Positive
    stop_points: Positive
    spread_points: Money = Field(ge=0)
    entry_slippage_points: Money = Field(ge=0)
    exit_slippage_points: Money = Field(ge=0)
    additional_gap_points: Money = Field(ge=0)
    stressed_spread_points: Money = Field(ge=0)
    stress_conversion_multiplier: Positive = Field(default=ONE, ge=1)
    stressed_margin_rate: Positive | None = Field(default=None, le=1)
    guaranteed: StrictBool = False
    overnight_days: int = Field(default=0, strict=True, ge=0, le=30)

    @model_validator(mode="after")
    def stress(self):
        if self.quote_per_gbp_bid > self.quote_per_gbp_ask:
            raise ValueError("Crossed conversion quote")
        if self.stressed_spread_points < self.spread_points:
            raise ValueError("Adverse spread stress cannot narrow the spread")
        return self


class Policy(Contract):
    name: Literal["FULL_NOTIONAL", "MARGIN_ONLY", "MARGIN_STRESS", "RISK_FUNDED"]
    initial_capital_gbp: Positive = Field(default=D(1000), le=1000)
    trade_risk_gbp: Positive = Field(default=D(5), le=5)
    aggregate_risk_gbp: Positive = Field(default=D(10), le=10)
    max_stress_loss_gbp: Positive = Field(default=D(100), le=100)
    max_positions: int = Field(default=3, strict=True, ge=1, le=3)

    @model_validator(mode="after")
    def budgets(self):
        if not self.trade_risk_gbp <= self.aggregate_risk_gbp <= self.initial_capital_gbp:
            raise ValueError("Initial capital and fixed risk bounds required")
        if self.max_stress_loss_gbp > self.initial_capital_gbp:
            raise ValueError("Stress budget cannot exceed initial capital")
        return self


ESSENTIAL = (
    "minimum_quantity",
    "quantity_step",
    "point_increment",
    "minimum_stop_points",
    "commission_per_quantity_side",
    "minimum_commission_side",
    "commission_currency",
    "conversion_markup",
    "long_allowed",
    "short_allowed",
    "guarantee_available",
)
RIGHTS = ("uk_retail_eligibility", "specific_product_api", "automation_terms", "data_licence")


def estimate(contract: InstrumentContract, scenario: Scenario) -> dict:
    """Static sizing budget; stop distance is a mid-reference move plus explicit costs.

    A quote-currency GBP obligation converts at reciprocal bid, including markup.
    GBP stakes are already denominated in GBP; quote FX never converts their P/L.
    Overnight arithmetic is a diagnostic only and is rejected by allocation.
    """
    with localcontext(_context()):
        essential = list(ESSENTIAL)
        if contract.quantity_kind != "GBP_PER_POINT":
            essential.append("units_per_quantity")
        if contract.product != "DELIVERABLE_SPOT":
            essential.extend(("margin_rate", "maximum_retail_leverage"))
        if scenario.guaranteed:
            essential.extend(("guarantee_premium_points", "guarantee_minimum_points"))
        if scenario.overnight_days:
            essential.extend(("adverse_annual_funding_rate", "funding_day_basis"))
        missing = tuple(f for f in essential if getattr(contract, f) is None)
        unverified = tuple(f for f in essential + list(RIGHTS) if not contract.verified(f))
        result = {
            "scenario": scenario.identity,
            "contract": contract.identity,
            "origin": contract.origin,
            "executable": False,
            "calculation_status": "BLOCKED_MISSING_SPEC"
            if missing
            else (
                "VERIFIED_ARTIFICIAL_ARITHMETIC"
                if contract.origin == "ARTIFICIAL" and not unverified
                else "PUBLIC_SPEC_ARITHMETIC"
                if not unverified
                else "ILLUSTRATIVE_ONLY"
            ),
            "missing_fields": missing,
            "unverified_fields": unverified,
        }
        if missing:
            return result | {"reasons": ("MISSING_ESSENTIAL_CONTRACT_FIELD",)}
        q, price, point = scenario.quantity, scenario.reference_price, contract.point_increment
        reasons = []
        if q < contract.minimum_quantity:
            reasons.append("BELOW_MINIMUM_QUANTITY")
        if q % contract.quantity_step:
            reasons.append("QUANTITY_STEP_VIOLATION")
        if not getattr(contract, scenario.side + "_allowed"):
            reasons.append("DIRECTION_NOT_PERMITTED")
        if scenario.stop_points < contract.minimum_stop_points:
            reasons.append("BELOW_MINIMUM_STOP_DISTANCE")
        if scenario.guaranteed and (
            not contract.guarantee_available
            or scenario.stop_points < contract.guarantee_minimum_points
        ):
            reasons.append("GUARANTEE_UNAVAILABLE_OR_DISTANCE_INVALID")
        if scenario.overnight_days:
            reasons.append("OVERNIGHT_POSITION_FORBIDDEN")
        if contract.product == "DELIVERABLE_SPOT":
            reasons.append("DELIVERABLE_CURRENCY_INVENTORY_NOT_MODELED")
        if contract.pair == "GBP_USD" and not (
            scenario.quote_per_gbp_bid <= price <= scenario.quote_per_gbp_ask
        ):
            reasons.append("GBPUSD_REFERENCE_AND_CONVERSION_DISAGREE")
        fx = ONE / scenario.quote_per_gbp_bid
        quote_fx = fx * (ONE + contract.conversion_markup)
        if contract.quantity_kind == "GBP_PER_POINT":
            base_units = None
            point_value = q
            notional = q * price / point
            raw_point = point_value
        else:
            base_units = q * contract.units_per_quantity
            raw_point = base_units * point * fx
            point_value = base_units * point * quote_fx
            # GBP is already the home currency: do not revalue GBP principal via itself.
            notional = base_units if contract.pair == "GBP_USD" else base_units * price * fx
        pip = D(".01") if contract.pair == "USD_JPY" else D(".0001")
        fee_unit = max(q * contract.commission_per_quantity_side, contract.minimum_commission_side)
        fee = fee_unit * (quote_fx if contract.commission_currency == "QUOTE" else ONE)
        commission = 2 * fee
        premium = point_value * contract.guarantee_premium_points if scenario.guaranteed else ZERO
        spread = point_value * scenario.spread_points
        slip = point_value * (
            scenario.entry_slippage_points
            + (ZERO if scenario.guaranteed else scenario.exit_slippage_points)
        )
        funding = (
            (
                notional
                * contract.adverse_annual_funding_rate
                * scenario.overnight_days
                / contract.funding_day_basis
            )
            if scenario.overnight_days
            else ZERO
        )
        planned = (
            point_value * scenario.stop_points + spread + slip + commission + premium + funding
        )
        adverse_move = scenario.stop_points + scenario.additional_gap_points
        adverse_price = price + (ONE if scenario.side == "short" else -ONE) * adverse_move * point
        if adverse_price <= 0:
            return result | {"reasons": tuple(reasons + ["NONPOSITIVE_ARTIFICIAL_STRESS_PRICE"])}
        stress_fx = scenario.stress_conversion_multiplier if base_units is not None else ONE
        stress_notional = notional * adverse_price / price * stress_fx
        if base_units is not None and contract.pair == "GBP_USD":
            stress_notional = notional
        stress_fee = commission * (
            scenario.stress_conversion_multiplier
            if contract.commission_currency == "QUOTE"
            else ONE
        )
        stress_points = (
            (
                scenario.stop_points
                + scenario.spread_points
                + scenario.entry_slippage_points
                + contract.guarantee_premium_points
            )
            if scenario.guaranteed
            else (
                adverse_move
                + scenario.stressed_spread_points
                + scenario.entry_slippage_points
                + scenario.exit_slippage_points
            )
        )
        stressed_loss = stress_points * point_value * stress_fx + stress_fee + funding * stress_fx
        effective_margin = (
            max(contract.margin_rate, ONE / contract.maximum_retail_leverage)
            if (contract.product != "DELIVERABLE_SPOT")
            else ONE
        )
        margin = notional * effective_margin
        if (
            scenario.stressed_margin_rate is not None
            and scenario.stressed_margin_rate < effective_margin
        ):
            reasons.append("STRESS_MARGIN_CANNOT_RELAX_VERIFIED_REQUIREMENT")
        stress_margin = stress_notional * max(
            effective_margin, scenario.stressed_margin_rate or effective_margin
        )
        values = {
            "quantity": q,
            "minimum_quantity": contract.minimum_quantity,
            "quantity_step": contract.quantity_step,
            "base_units": base_units,
            "point_value_gbp": point_value,
            "pip_value_gbp": point_value * pip / point,
            "notional_gbp": notional,
            "initial_margin_gbp": margin,
            "spread_cost_gbp": spread,
            "slippage_cost_gbp": slip,
            "roundtrip_commission_gbp": commission,
            "guarantee_premium_reserved_gbp": premium,
            "financing_gbp": funding,
            "planned_loss_gbp": planned,
            "quote_currency_per_gbp_bid": scenario.quote_per_gbp_bid,
            "adverse_gbp_conversion_rate": quote_fx,
            "commission_conversion_markup_gbp": (
                2 * fee_unit * fx * contract.conversion_markup
                if contract.commission_currency == "QUOTE"
                else ZERO
            ),
            "conversion_markup_on_price_costs_gbp": (point_value - raw_point)
            * (
                scenario.stop_points
                + scenario.spread_points
                + scenario.entry_slippage_points
                + (ZERO if scenario.guaranteed else scenario.exit_slippage_points)
                + (contract.guarantee_premium_points if scenario.guaranteed else ZERO)
            ),
            "roundtrip_cost_and_premium_gbp": spread + slip + commission + premium + funding,
            "stress_loss_gbp": stressed_loss,
            "stress_notional_gbp": stress_notional,
            "stress_margin_gbp": stress_margin,
            "ordinary_stop_is_loss_guarantee": False,
            "maximum_realized_loss_gbp": None,
            "guarantee_conditional_on_contract": scenario.guaranteed,
        }
        return result | values | {"reasons": tuple(reasons)}


def reservation(item: dict, policy: Policy, contract: InstrumentContract) -> Decimal:
    if policy.name == "FULL_NOTIONAL":
        return item["notional_gbp"] + item["roundtrip_cost_and_premium_gbp"]
    if not all(contract.verified(f) for f in ("margin_rate", "maximum_retail_leverage")):
        raise ValueError("MARGIN_SPECIFICATION_NOT_VERIFIED")
    if policy.name == "MARGIN_ONLY":
        return item["initial_margin_gbp"] + item["roundtrip_cost_and_premium_gbp"]
    # Stress loss already includes commissions and guarantee premium: reserve once.
    return max(item["initial_margin_gbp"], item["stress_margin_gbp"]) + item["stress_loss_gbp"]


def allocate(requests: tuple[tuple[InstrumentContract, Scenario], ...], policy: Policy) -> dict:
    """Static simultaneous artificial positions, priority by unique scenario identity.

    RISK_FUNDED sizes down on the broker grid with independent margin+stress funding.
    No equity curve, strategy evaluation, subsequent deposit or risk ratchet exists.
    """
    identities = [s.identity for _, s in requests]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate artificial scenario identity")
    conversion_quotes = {}
    for c, s in requests:
        currency = c.pair.split("_")[1]
        rate = s.quote_per_gbp_bid, s.quote_per_gbp_ask
        if currency in conversion_quotes and conversion_quotes[currency] != rate:
            raise ValueError("Conflicting simultaneous quote-currency conversion quotes")
        conversion_quotes[currency] = rate
    with localcontext(_context()):
        accepted, decisions = [], []
        reserved, used_risk, used_stress = ZERO, ZERO, ZERO
        admitted_pairs = set()
        for contract, initial in sorted(requests, key=lambda r: r[1].identity):
            s = initial
            item = estimate(contract, s)
            reasons = list(item["reasons"])
            if policy.name == "RISK_FUNDED" and not reasons:
                step = contract.quantity_step
                low = int(contract.minimum_quantity / step)
                high = int((s.quantity / step).to_integral_value(rounding=ROUND_FLOOR))
                found = None
                while low <= high:
                    mid = (low + high) // 2
                    probe = s.model_copy(update={"quantity": mid * step})
                    value = estimate(contract, probe)
                    try:
                        capital = reservation(value, policy, contract)
                    except ValueError as exc:
                        reasons.append(str(exc))
                        break
                    if (
                        value["planned_loss_gbp"]
                        <= min(policy.trade_risk_gbp, policy.aggregate_risk_gbp - used_risk)
                        and capital <= policy.initial_capital_gbp - reserved
                        and value["stress_loss_gbp"] <= policy.max_stress_loss_gbp - used_stress
                    ):
                        found = (probe, value)
                        low = mid + 1
                    else:
                        high = mid - 1
                if found:
                    s, item = found
                elif not reasons:
                    reasons.append("NO_FUNDED_MINIMUM_WITHIN_RISK_BUDGET")
            required = None
            if not reasons:
                try:
                    required = reservation(item, policy, contract)
                except ValueError as exc:
                    reasons.append(str(exc))
            if not reasons:
                if len(accepted) >= policy.max_positions:
                    reasons.append("MAXIMUM_POSITION_COUNT")
                if contract.pair in admitted_pairs:
                    reasons.append("PAIR_POSITION_LIMIT")
                if item["planned_loss_gbp"] > policy.trade_risk_gbp:
                    reasons.append("PLANNED_TRADE_RISK_EXCEEDED")
                if used_risk + item["planned_loss_gbp"] > policy.aggregate_risk_gbp:
                    reasons.append("AGGREGATE_PLANNED_RISK_EXCEEDED")
                if reserved + required > policy.initial_capital_gbp:
                    reasons.append("INSUFFICIENT_UNENCUMBERED_CAPITAL")
                if policy.name in ("MARGIN_STRESS", "RISK_FUNDED") and (
                    used_stress + item["stress_loss_gbp"] > policy.max_stress_loss_gbp
                ):
                    reasons.append("AGGREGATE_ARTIFICIAL_STRESS_BUDGET_EXCEEDED")
            admitted = not reasons
            if admitted:
                reserved += required
                used_risk += item["planned_loss_gbp"]
                used_stress += item["stress_loss_gbp"]
                admitted_pairs.add(contract.pair)
                accepted.append(item | {"capital_required_gbp": required})
            decisions.append(
                item
                | {
                    "requested_quantity": initial.quantity,
                    "admitted_to_artificial_book": admitted,
                    "capital_required_gbp": required,
                    "remaining_capital_after_gbp": policy.initial_capital_gbp - reserved,
                    "allocation_reasons": tuple(reasons),
                }
            )
        stress_loss = sum((a["stress_loss_gbp"] for a in accepted), ZERO)
        margin = sum((a["stress_margin_gbp"] for a in accepted), ZERO)
        stress_equity = policy.initial_capital_gbp - stress_loss
        result = {
            "version": "STATIC_ARTIFICIAL_FUNDING_COMPARISON_V1",
            "executable": False,
            "policy": policy.model_dump(mode="json"),
            "decisions": decisions,
            "feasible_positions": len(accepted),
            "reserved_capital_gbp": reserved,
            "available_capital_gbp": policy.initial_capital_gbp - reserved,
            "capital_utilization": reserved / policy.initial_capital_gbp,
            "notional_exposure_gbp": sum((a["notional_gbp"] for a in accepted), ZERO),
            "initial_margin_gbp": sum((a["initial_margin_gbp"] for a in accepted), ZERO),
            "planned_risk_gbp": used_risk,
            "planned_risk_utilization": used_risk / policy.aggregate_risk_gbp,
            "stress_loss_gbp": stress_loss,
            "post_stress_equity_gbp": stress_equity,
            "post_stress_margin_gbp": margin,
            "stress_free_margin_gbp": stress_equity - margin,
            "stress_margin_sufficient": stress_equity >= margin,
            "stress_budget_exceeded": stress_loss > policy.max_stress_loss_gbp,
            "legacy_notional_cap_breached_in_stress": sum(
                (a["stress_notional_gbp"] for a in accepted), ZERO
            )
            > stress_equity,
            "account_closeout_threshold_breached": bool(accepted) and stress_equity < margin / 2,
        }
        serialized = _json(result)
        return serialized | {"checksum": digest(serialized)}


def _json(value):
    if isinstance(value, D):
        return str(value)
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(v) for v in value]
    return value
