"""One deterministic hypothesis, with no costing, risk approval or broker access."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from atlas.config import AtlasError
from atlas.domain import (
    Adjustment,
    Instrument,
    MarketBar,
    StrategySignal,
    TradingSession,
    floor_step,
    number,
)
from atlas.sessions import bar_errors


@dataclass(frozen=True)
class ORBConfig:
    opening_range_minutes: int = 15
    candle_minutes: int = 5
    target_r: Decimal = Decimal("2")
    signal_lifetime_minutes: int = 5
    exit_buffer_minutes: int = 5
    minimum_bar_volume: Decimal = Decimal("100")

    def __post_init__(self) -> None:
        if self.candle_minutes != 5 or self.opening_range_minutes < 5:
            raise AtlasError("ORB v1 uses five-minute candles and an explicit opening range.")
        if self.opening_range_minutes % self.candle_minutes:
            raise AtlasError("Opening range must contain a whole number of candles.")
        if min(self.signal_lifetime_minutes, self.exit_buffer_minutes) <= 0:
            raise AtlasError("Invalid ORB lifetime.")
        number(self.target_r, positive=True)
        number(self.minimum_bar_volume)


@dataclass(frozen=True)
class StrategyResult:
    signal: StrategySignal | None
    reasons: tuple[str, ...]


class ORBStrategy:
    version = "orb-5m-v1"

    def __init__(self, config: ORBConfig | None = None) -> None:
        self.config = config if config is not None else ORBConfig()

    def evaluate(
        self,
        instrument: Instrument,
        bars: tuple[MarketBar, ...],
        session: TradingSession,
        now: datetime,
    ) -> StrategyResult:
        if not session.contains(now):
            return StrategyResult(None, ("MARKET_CLOSED",))
        errors = bar_errors(bars, session, now, instrument.instrument_id)
        if errors:
            return StrategyResult(None, errors)
        ordered = sorted(bars, key=lambda bar: bar.start)
        if any(
            bar.unit != instrument.quote_unit
            or bar.adjustment != Adjustment.RAW
            or bar.volume < self.config.minimum_bar_volume
            or any(not instrument.valid_price(p) for p in (bar.open, bar.high, bar.low, bar.close))
            for bar in ordered
        ):
            return StrategyResult(None, ("STRATEGY_DATA_QUALITY",))
        count = self.config.opening_range_minutes // self.config.candle_minutes
        if len(ordered) <= count:
            return StrategyResult(None, ("OPENING_RANGE_FORMING",))
        opening = ordered[:count]
        high = max(bar.high for bar in opening)
        low = min(bar.low for bar in opening)
        trigger = next((bar for bar in ordered[count:] if bar.close > high), None)
        if trigger is None:
            return StrategyResult(None, ("NO_BREAKOUT",))
        if trigger != ordered[-1] or now >= trigger.visible_time + timedelta(
            minutes=self.config.signal_lifetime_minutes
        ):
            return StrategyResult(None, ("TRIGGER_ALREADY_PAST",))
        stop = floor_step((high + low) / 2, instrument.tick_size)
        target = floor_step(
            trigger.close + self.config.target_r * (trigger.close - stop), instrument.tick_size
        )
        time_exit = session.close - timedelta(minutes=self.config.exit_buffer_minutes)
        expiry = min(now + timedelta(minutes=self.config.signal_lifetime_minutes), time_exit)
        if not stop < trigger.close < target or expiry <= now:
            return StrategyResult(None, ("INVALID_OR_EXPIRED_PLAN",))
        key = f"{self.version}|{self.config}|{instrument.instrument_id}|{session.local_date}"
        signal = StrategySignal(
            hashlib.sha256(key.encode()).hexdigest(),
            instrument.instrument_id,
            self.version,
            now,
            expiry,
            time_exit,
            trigger.close,
            stop,
            target,
        )
        return StrategyResult(signal, ())
