"""Deterministic historical replay over accepted immutable datasets; no execution APIs."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

from atlas.backtest import BacktestReport, jsonable, run_backtest
from atlas.config import AtlasError
from atlas.costs import CostEngine, FeeSchedule
from atlas.domain import (
    Adjustment,
    Currency,
    FxRate,
    Instrument,
    MarketBar,
    MarketQuote,
    QuoteUnit,
    TradingSession,
    Venue,
    floor_step,
    number,
    utc,
)
from atlas.market_data.calendars import RecordedCalendar
from atlas.market_data.contracts import (
    Adjustment as HistoricalAdjustment,
)
from atlas.market_data.contracts import (
    Availability,
    DownloadRequest,
    HistoricalBar,
    Origin,
    digest,
)
from atlas.market_data.store import HistoricalStore
from atlas.research import ResearchSettings
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig


class AvailabilityMode(StrEnum):
    VERIFIED = "VERIFIED"
    ASSUMED = "ASSUMED"


class EvaluationRole(StrEnum):
    IN_SAMPLE = "IN_SAMPLE"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"


@dataclass(frozen=True)
class InstrumentRules:
    tick_size: Decimal
    quantity_step: Decimal = Decimal("1")
    minimum_quantity: Decimal = Decimal("1")

    def __post_init__(self):
        for value in (self.tick_size, self.quantity_step, self.minimum_quantity):
            number(value, positive=True)
        if self.minimum_quantity % self.quantity_step:
            raise AtlasError("Historical minimum quantity must align to its quantity step.")


@dataclass(frozen=True)
class ExecutionAssumptions:
    spread_bps: Decimal
    slippage_bps: Decimal
    commission_fixed_gbp: Decimal
    commission_bps: Decimal
    fx_bps: Decimal
    entry_tax_bps: Decimal
    exit_tax_bps: Decimal
    other_fixed_gbp: Decimal
    max_volume_participation_pct: Decimal
    stress_multiplier: Decimal = Decimal("2")

    def __post_init__(self):
        for value in asdict(self).values():
            number(value)
        if not Decimal("0") < self.max_volume_participation_pct <= Decimal("100"):
            raise AtlasError(
                "Historical volume participation must be greater than 0 and at most 100."
            )
        if self.stress_multiplier < 1:
            raise AtlasError("Historical cost stress multiplier must be at least one.")


@dataclass(frozen=True)
class FxPolicy:
    gbp_per_usd: Decimal
    event_time: datetime
    available_time: datetime
    source: str
    point_in_time_verified: bool = False

    def __post_init__(self):
        number(self.gbp_per_usd, positive=True)
        utc(self.event_time)
        utc(self.available_time)
        if self.available_time < self.event_time or not self.source.strip():
            raise AtlasError("Historical FX policy requires explicit valid timestamps and source.")
        if not isinstance(self.point_in_time_verified, bool):
            raise AtlasError("Historical FX verification flag must be boolean.")


@dataclass(frozen=True)
class EvaluationPlan:
    role: EvaluationRole = EvaluationRole.IN_SAMPLE
    parameter_selection_cutoff: datetime | None = None

    def __post_init__(self):
        if self.parameter_selection_cutoff is not None:
            utc(self.parameter_selection_cutoff)

    def validate(self, start: datetime):
        if self.role == EvaluationRole.OUT_OF_SAMPLE and (
            self.parameter_selection_cutoff is None or self.parameter_selection_cutoff >= start
        ):
            raise AtlasError("OUT_OF_SAMPLE requires a parameter cutoff before the replay window.")


@dataclass(frozen=True)
class HistoricalBacktestConfig:
    availability_mode: AvailabilityMode
    assumed_delay_seconds: int | None
    instrument: InstrumentRules
    execution: ExecutionAssumptions
    fx: FxPolicy
    evaluation: EvaluationPlan = EvaluationPlan()
    orb: ORBConfig = ORBConfig()
    settings: ResearchSettings = ResearchSettings()

    def __post_init__(self):
        if self.availability_mode == AvailabilityMode.ASSUMED:
            if self.assumed_delay_seconds is None or not 60 <= self.assumed_delay_seconds <= 3600:
                raise AtlasError("ASSUMED availability requires an explicit 60-3600 second delay.")
        elif self.assumed_delay_seconds is not None:
            raise AtlasError("VERIFIED availability cannot carry an assumed delay.")

    def content(self):
        return jsonable(self)


@dataclass(frozen=True)
class HistoricalReplayDataset:
    name: str
    instrument: Instrument
    session: TradingSession
    bars: tuple[MarketBar, ...]
    quotes: tuple[MarketQuote, ...]
    fx: FxRate
    provenance: tuple[tuple[str, str], ...]
    checksum: str


@dataclass(frozen=True)
class HistoricalBacktestReport:
    label: str
    run_id: str
    dataset_id: str
    dataset_manifest_checksum: str
    configuration_checksum: str
    availability_mode: str
    point_in_time_verified: bool
    source_rows: int
    selected_rows: int
    derived_strategy_bars: int
    execution_observations: int
    evaluation_role: str
    parameter_selection_cutoff: str | None
    assumptions: tuple[str, ...]
    equity_curve: tuple[tuple[str, Decimal, Decimal], ...]
    drawdown_curve: tuple[tuple[str, Decimal], ...]
    result: BacktestReport

    def to_dict(self):
        value = jsonable(self)
        value["result"] = self.result.to_dict()
        return value


def _ceil_step(value: Decimal, step: Decimal) -> Decimal:
    number(value, positive=True)
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _effective_time(bar: HistoricalBar, config: HistoricalBacktestConfig):
    if bar.availability == Availability.KNOWN:
        return bar.permitted_at
    if config.availability_mode != AvailabilityMode.ASSUMED:
        return None
    if bar.availability == Availability.ASSUMED:
        return bar.permitted_at
    return bar.end + timedelta(seconds=config.assumed_delay_seconds)


def _instrument(request: DownloadRequest, rules: InstrumentRules) -> Instrument:
    mapping = request.instrument.mapping(request.provider, request.start, request.end)
    return Instrument(
        request.instrument.instrument_id,
        request.instrument.venue,
        request.instrument.quote_unit,
        rules.tick_size,
        rules.quantity_step,
        rules.minimum_quantity,
        ((request.provider, mapping.symbol),),
    )


def _strategy_bars(
    bars: tuple[HistoricalBar, ...],
    effective: dict[datetime, datetime],
    instrument: Instrument,
    session: TradingSession,
):
    by_start = {bar.start: bar for bar in bars}
    result = []
    start = session.open
    while start + timedelta(minutes=5) <= session.close:
        starts = tuple(start + timedelta(minutes=index) for index in range(5))
        group = tuple(by_start[value] for value in starts if value in by_start)
        if len(group) == 5:
            available = max(effective[bar.start] for bar in group)
            result.append(
                MarketBar(
                    instrument.instrument_id,
                    "historical-replay",
                    instrument.quote_unit,
                    start,
                    start + timedelta(minutes=5),
                    start + timedelta(minutes=5),
                    available,
                    available,
                    group[0].open,
                    max(bar.high for bar in group),
                    min(bar.low for bar in group),
                    group[-1].close,
                    sum((bar.volume for bar in group), Decimal("0")),
                    adjustment=Adjustment.RAW,
                )
            )
        start += timedelta(minutes=5)
    return tuple(result)


def _execution_quotes(
    bars: tuple[HistoricalBar, ...],
    effective: dict[datetime, datetime],
    instrument: Instrument,
    execution: ExecutionAssumptions,
):
    quotes = []
    for bar in bars:
        # Historical aggregates can contain valid sub-tick trade prices. The tick constrains
        # Atlas's modeled executable quote/order, not every source trade in the OHLC aggregate.
        half_spread = bar.open * execution.spread_bps / Decimal("20000")
        bid = floor_step(bar.open - half_spread, instrument.tick_size)
        ask = _ceil_step(bar.open + half_spread, instrument.tick_size)
        capacity = floor_step(
            bar.volume * execution.max_volume_participation_pct / Decimal("100"),
            instrument.quantity_step,
        )
        quotes.append(
            MarketQuote(
                instrument.instrument_id,
                "historical-next-minute-open-model",
                instrument.quote_unit,
                bar.start,
                effective[bar.start],
                effective[bar.start],
                bid,
                ask,
                capacity,
                capacity,
                adjustment=Adjustment.RAW,
            )
        )
    return tuple(quotes)


def _costs(config: HistoricalBacktestConfig, venue: Venue, start: datetime, end: datetime):
    values = config.execution
    return CostEngine(
        (
            FeeSchedule(
                "HISTORICAL-RESEARCH-COSTS-v1-NOT-BROKER-RATES",
                start - timedelta(days=1),
                end + timedelta(days=1),
                (venue,),
                values.spread_bps,
                values.slippage_bps,
                values.commission_fixed_gbp,
                values.commission_bps,
                values.fx_bps,
                values.entry_tax_bps,
                values.exit_tax_bps,
                values.other_fixed_gbp,
                values.stress_multiplier,
            ),
        )
    )


def run_historical_backtest(
    store: HistoricalStore,
    dataset_id: str,
    config: HistoricalBacktestConfig,
    *,
    allow_artificial_fixture: bool = False,
) -> HistoricalBacktestReport:
    """Inspect immutable data, project visibility, then call common strategy/risk/accounting."""
    before = store.inspect(dataset_id)
    manifest, quality, bars = before
    if manifest["state"] != "ACCEPTED" or quality.status == "FAIL":
        raise AtlasError("Only accepted historical datasets can be replayed.")
    request = DownloadRequest.model_validate(manifest["request"])
    if request.instrument.venue not in (Venue.NYSE, Venue.NASDAQ):
        raise AtlasError("Phase 3B initially supports US-listed equities only.")
    if request.instrument.quote_unit != QuoteUnit.USD:
        raise AtlasError("Phase 3B US datasets require explicit USD quotation units.")
    if request.adjustment != HistoricalAdjustment.RAW:
        raise AtlasError("Phase 3B ORB currently requires explicitly unadjusted bars.")
    if manifest["origin"] == Origin.FIXTURE.value and not allow_artificial_fixture:
        raise AtlasError("Artificial historical fixtures require an explicit test-only opt-in.")
    calendar = RecordedCalendar(manifest["calendar"])
    sessions = tuple(
        session
        for session in calendar.sessions()
        if session is not None and session.open < request.end and session.close > request.start
    )
    if len(sessions) != 1:
        raise AtlasError("Phase 3B v1 requires exactly one recorded trading session per run.")
    session = sessions[0]
    config.evaluation.validate(session.open)
    instrument = _instrument(request, config.instrument)
    selected = []
    effective = {}
    for bar in bars:
        permitted = _effective_time(bar, config)
        if permitted is not None:
            selected.append(bar)
            effective[bar.start] = permitted
    selected = tuple(sorted(selected, key=lambda item: item.start))
    strategy_bars = _strategy_bars(selected, effective, instrument, session)
    quotes = _execution_quotes(selected, effective, instrument, config.execution)
    fx = FxRate(
        Currency.USD,
        config.fx.gbp_per_usd,
        config.fx.event_time,
        config.fx.available_time,
        config.fx.source,
    )
    assumptions = [
        "NEXT_MINUTE_OPEN_EXECUTION_OBSERVED_AT_CONFIGURED_AVAILABILITY",
        "REPORTED_VOLUME_PARTICIPATION_CAP_NOT_ORDER_BOOK_LIQUIDITY",
        "EXPLICIT_COST_AND_SLIPPAGE_ASSUMPTIONS_NOT_BROKER_FACTS",
    ]
    if config.availability_mode == AvailabilityMode.ASSUMED:
        assumptions.append("BAR_END_PLUS_DELAY_NOT_PROVIDER_PUBLICATION_TIME")
    if not config.fx.point_in_time_verified:
        assumptions.append("FX_RATE_ASSUMED_NOT_POINT_IN_TIME_VERIFIED")
    point_in_time = (
        config.availability_mode == AvailabilityMode.VERIFIED
        and config.fx.point_in_time_verified
        and bool(selected)
        and len(selected) == len(bars)
        and all(bar.availability == Availability.KNOWN for bar in selected)
    )
    label = (
        "ARTIFICIAL HISTORICAL FIXTURE — NOT MARKET PERFORMANCE"
        if manifest["origin"] == Origin.FIXTURE.value
        else "HISTORICAL RESEARCH — BAR AND FX AVAILABILITY VERIFIED — NOT LIVE PERFORMANCE"
        if point_in_time
        else "EXPLORATORY HISTORICAL RESEARCH — ASSUMED/INCOMPLETE POINT-IN-TIME EVIDENCE"
    )
    replay = HistoricalReplayDataset(
        dataset_id,
        instrument,
        session,
        strategy_bars,
        quotes,
        fx,
        (
            ("dataset_id", dataset_id),
            ("origin", manifest["origin"]),
            ("provider", manifest["provider"]),
            ("availability_mode", config.availability_mode.value),
        ),
        dataset_id,
    )
    max_lag = max(
        (effective[bar.start] - bar.start for bar in selected),
        default=timedelta(seconds=60),
    )
    policy = RiskPolicy(
        version="historical-risk-v1",
        quote_max_age=max(timedelta(seconds=60), max_lag + timedelta(seconds=1)),
    )
    snapshots = []

    def capture(kind, snapshot, _value, _kill_switch):
        if kind == "portfolio.valuation":
            snapshots.append(snapshot)

    result = run_backtest(
        replay,
        config.settings,
        label=label,
        costs=_costs(config, instrument.venue, session.open, session.close),
        orb=config.orb,
        policy=policy,
        calendar=calendar,
        ignore_predecision_quotes=True,
        observer=capture,
    )
    equity_curve = tuple(
        (item.timestamp.isoformat(), item.equity_gbp, item.available_cash_gbp) for item in snapshots
    )
    peak = config.settings.starting_capital_gbp
    drawdowns = []
    for timestamp, equity, _cash in equity_curve:
        peak = max(peak, equity)
        drawdowns.append((timestamp, peak - equity))
    configuration = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "dataset_manifest_checksum": digest(manifest),
        "historical_backtest": config.content(),
        "engine": "atlas-historical-backtest-v1",
    }
    configuration_checksum = digest(configuration)
    run_id = digest(
        {"configuration": configuration, "result_config": result.configuration_checksum}
    )
    if store.inspect(dataset_id) != before:
        raise AtlasError("Historical dataset changed during read-only replay.")
    return HistoricalBacktestReport(
        label,
        run_id,
        dataset_id,
        digest(manifest),
        configuration_checksum,
        config.availability_mode.value,
        point_in_time,
        len(bars),
        len(selected),
        len(strategy_bars),
        len(quotes),
        config.evaluation.role.value,
        (
            config.evaluation.parameter_selection_cutoff.isoformat()
            if config.evaluation.parameter_selection_cutoff
            else None
        ),
        tuple(assumptions),
        equity_curve,
        tuple(drawdowns),
        result,
    )
