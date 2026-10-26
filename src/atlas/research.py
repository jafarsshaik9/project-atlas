"""Offline capital settings and opening ledger; no broker or order capabilities."""

import os
from dataclasses import dataclass, field, fields
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from atlas.config import AtlasError

_PENNY = Decimal("0.01")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ResearchSettings:
    """Provisional research percentages of initial capital, not guaranteed loss caps."""

    starting_capital_gbp: Decimal = field(
        default=Decimal("1000.00"), metadata={"env": "ATLAS_STARTING_CAPITAL_GBP"}
    )
    planned_risk_per_trade_pct: Decimal = field(
        default=Decimal("0.5"), metadata={"env": "ATLAS_PLANNED_RISK_PER_TRADE_PCT"}
    )
    daily_loss_threshold_pct: Decimal = field(
        default=Decimal("1"), metadata={"env": "ATLAS_DAILY_LOSS_THRESHOLD_PCT"}
    )
    max_concurrent_planned_risk_pct: Decimal = field(
        default=Decimal("1"), metadata={"env": "ATLAS_MAX_CONCURRENT_PLANNED_RISK_PCT"}
    )

    def __post_init__(self) -> None:
        for setting in fields(self):
            value = getattr(self, setting.name)
            name = setting.metadata["env"]
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise AtlasError(f"{name} must be a finite positive decimal.")
            if setting.name != "starting_capital_gbp" and value > _HUNDRED:
                raise AtlasError(f"{name} must be a percentage greater than 0 and at most 100.")
        try:
            rounded = self.starting_capital_gbp.quantize(_PENNY)
        except InvalidOperation:
            raise AtlasError("ATLAS_STARTING_CAPITAL_GBP is outside supported precision.") from None
        if rounded != self.starting_capital_gbp:
            raise AtlasError("ATLAS_STARTING_CAPITAL_GBP must be a whole number of pennies.")
        if self.planned_risk_per_trade_pct > self.max_concurrent_planned_risk_pct:
            raise AtlasError(
                "Planned risk per trade cannot exceed maximum concurrent planned risk."
            )

    @classmethod
    def from_environment(cls) -> "ResearchSettings":
        values = {}
        for setting in fields(cls):
            name = setting.metadata["env"]
            raw = os.environ.get(name)
            if raw is not None:
                try:
                    values[setting.name] = Decimal(raw)
                except InvalidOperation:
                    raise AtlasError(f"{name} must be a decimal number.") from None
        return cls(**values)

    def _budget(self, percentage: Decimal) -> Decimal:
        return (self.starting_capital_gbp * percentage / _HUNDRED).quantize(
            _PENNY, rounding=ROUND_DOWN
        )

    @property
    def planned_risk_per_trade_gbp(self) -> Decimal:
        return self._budget(self.planned_risk_per_trade_pct)

    @property
    def daily_loss_threshold_gbp(self) -> Decimal:
        return self._budget(self.daily_loss_threshold_pct)

    @property
    def max_concurrent_planned_risk_gbp(self) -> Decimal:
        return self._budget(self.max_concurrent_planned_risk_pct)


@dataclass(frozen=True)
class VirtualLedger:
    """Immutable opening cash snapshot funded solely by explicit research settings.

    Transactions, fills and risk enforcement remain separate future components.
    No broker balance is accepted or fetched to initialize this ledger.
    """

    settings: ResearchSettings
    initial_balance_gbp: Decimal = field(init=False)
    cash_balance_gbp: Decimal = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_balance_gbp", self.settings.starting_capital_gbp)
        object.__setattr__(self, "cash_balance_gbp", self.settings.starting_capital_gbp)
