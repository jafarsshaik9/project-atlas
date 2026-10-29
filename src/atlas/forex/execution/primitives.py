"""Opening-range measurement only. No breakout signals, strategy search or history runner."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from atlas.config import AtlasError
from atlas.domain import utc
from atlas.forex.execution.contracts import (
    MINUTE,
    Observation,
    Overlay,
    Settings,
    arithmetic_context,
)
from atlas.forex.execution.market import entry_reason, reason
from atlas.forex.models import FxInstrument

EMPTY_OVERLAY = Overlay()


@dataclass(frozen=True)
class OpeningRange:
    high: Decimal
    low: Decimal
    information_cutoff: datetime
    source_ids: tuple[str, ...]
    basis: str = "MIDPOINT_FEATURE_ONLY_NOT_EXECUTION"


def _opening_range(
    observations: tuple[Observation, ...],
    instrument: FxInstrument,
    start: datetime,
    minutes: int,
    now: datetime,
    settings: Settings,
    overlay: Overlay = EMPTY_OVERLAY,
) -> OpeningRange:
    utc(start)
    utc(now)
    if type(minutes) is not int or not 1 <= minutes <= 60 or len(observations) != minutes:
        raise AtlasError("FX_OPENING_RANGE_INCOMPLETE")
    end = start + minutes * MINUTE
    if end > now:
        raise AtlasError("FX_OPENING_RANGE_FUTURE_DATA")
    ordered = sorted(observations, key=lambda o: o.candle.start)
    for index, o in enumerate(ordered):
        if o.instrument != instrument.name or o.candle.start != start + index * MINUTE:
            raise AtlasError("FX_OPENING_RANGE_GRID_INVALID")
        if issue := reason(o, overlay) or entry_reason(o, instrument, settings):
            raise AtlasError("FX_OPENING_RANGE_INELIGIBLE:" + issue)
    return OpeningRange(
        max(o.candle.mid.h for o in ordered),
        min(o.candle.mid.l for o in ordered),
        end,
        tuple(o.dataset_id for o in ordered),
    )


def opening_range(
    observations: tuple[Observation, ...],
    instrument: FxInstrument,
    start: datetime,
    minutes: int,
    now: datetime,
    settings: Settings,
    overlay: Overlay = EMPTY_OVERLAY,
) -> OpeningRange:
    with localcontext(arithmetic_context()):
        if settings.costs.known_at > start:
            raise AtlasError("FX_OPENING_RANGE_FUTURE_COST_CONFIGURATION")
        return _opening_range(observations, instrument, start, minutes, now, settings, overlay)
