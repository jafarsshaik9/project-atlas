"""Closed, versioned JSON vocabulary for replaying existing accounting operations."""

from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from atlas import domain
from atlas.config import AtlasError
from atlas.market_data.contracts import canonical

TYPES = {
    cls.__name__: cls
    for cls in (
        domain.Instrument,
        domain.StrategySignal,
        domain.MarketBar,
        domain.MarketQuote,
        domain.FxRate,
        domain.Position,
        domain.PortfolioSnapshot,
        domain.RiskDecision,
        domain.Currency,
        domain.QuoteUnit,
        domain.Venue,
        domain.Adjustment,
        domain.DecisionStatus,
        domain.PriceBasis,
    )
}


def encode(value):
    if isinstance(value, Enum):
        return {"type": type(value).__name__, "value": value.value}
    if isinstance(value, (Decimal, datetime, date)):
        return {"type": type(value).__name__, "value": str(value)}
    if isinstance(value, timedelta):
        return {"type": "timedelta", "value": value.total_seconds()}
    if is_dataclass(value):
        return {
            "type": type(value).__name__,
            "value": {f.name: encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, tuple):
        return {"type": "tuple", "value": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"type", "value"}:
        tag, data = value["type"], value["value"]
        converters = {
            "Decimal": Decimal,
            "datetime": datetime.fromisoformat,
            "date": date.fromisoformat,
            "timedelta": lambda v: timedelta(seconds=v),
            "tuple": lambda v: tuple(decode(x) for x in v),
        }
        if tag in converters:
            return converters[tag](data)
        if tag not in TYPES:
            raise AtlasError("Unknown paper journal type.")
        cls = TYPES[tag]
        return cls(**{k: decode(v) for k, v in data.items()}) if is_dataclass(cls) else cls(data)
    return {k: decode(v) for k, v in value.items()}


def dump(value):
    return canonical(encode(value))


def public(value):
    """Readable diagnostics, with exact decimal strings and no reconstruction tags."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Decimal, datetime, date)):
        return str(value)
    if is_dataclass(value):
        return {f.name: public(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [public(v) for v in value]
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    return value
