"""Frozen-hypothesis synthetic shadow preparation. No provider, credentials or network I/O."""

import hashlib
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from atlas.config import AtlasError
from atlas.domain import utc
from atlas.forex.execution.calendar import window
from atlas.forex.execution.contracts import (
    MINUTE,
    Costs,
    EntryRule,
    Frame,
    Intent,
    Overlay,
    Run,
    Settings,
    arithmetic_context,
    instruments_valid,
)
from atlas.forex.execution.engine import implementation_hash
from atlas.forex.execution.market import entry_reason, reason
from atlas.forex.models import FxInstrument
from atlas.forex.research.execution import evaluate_intent
from atlas.market_data.contracts import Contract, Identifier, canonical, digest

ROOT = Path(__file__).resolve().parents[3]
SHA = "6f84852343fbeb0971bf94ebb7a2c670c3ae60c7ce04212d8830503b2356a4a9"
D = Decimal
SCENARIOS = ("C0", "C1", "C2")


def design():
    raw = (ROOT / "examples/forex-lean-profitability-v1.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA:
        raise AtlasError("PILOT_FROZEN_MANIFEST_CHANGED")
    return json.loads(raw)


class ReceivedFrame(Contract):
    frame: Frame
    collected_at: datetime

    @model_validator(mode="after")
    def received(self):
        utc(self.collected_at)
        if self.collected_at < self.frame.start + MINUTE:
            raise ValueError("Completed candle cannot be acquired before its end")
        return self


class Batch(Contract):
    origin: Literal["ARTIFICIAL_OFFLINE_FIXTURE", "FORWARD_OBSERVATIONS"]
    source: Identifier
    day: date
    received: tuple[ReceivedFrame, ...] = Field(max_length=600)
    instruments: tuple[FxInstrument, ...]
    granularity: Literal["M1"] = "M1"

    @model_validator(mode="after")
    def ordered(self):
        starts = [r.frame.start for r in self.received]
        if starts != sorted(set(starts)):
            raise ValueError("Duplicate or unordered candle frame")
        if not instruments_valid(self.instruments):
            raise ValueError("Supported, unique instrument metadata and pip conventions required")
        return self


def configuration(scenario, cash):
    d = design()
    c = d["costs"][scenario]
    if not 0 < cash <= D(1000):
        raise AtlasError("PILOT_CAPITAL_BLOCKED")
    return Settings(
        costs=Costs(
            version=d["costs"]["model"],
            provenance="PILOT_FROZEN_ASSUMED_COSTS",
            known_at=datetime.fromisoformat(d["registered_at_utc"]),
            slippage_pips=c["slippage_pips_per_side"],
            spread_multiplier=c["spread_multiplier"],
            commission_gbp_per_unit_per_side=d["costs"]["commission_gbp_per_unit_per_side"],
            spread_floor_pips=0,
            additional_spread_pips=0,
            max_entry_spread_pips=d["costs"]["max_entry_spread_pips"],
        ),
        starting_capital_gbp=cash,
        max_exposure_gbp=min(D(1000), cash),
        risk_per_trade_gbp=min(D(5), cash),
        max_concurrent_risk_gbp=min(D(10), cash),
        daily_loss_limit_gbp=min(D(10), cash),
        max_positions=1,
        max_holding_minutes=d["rule"]["max_holding_minutes"],
        collision=d["rule"]["collision"],
    )


def signal(prefix, settings, win, meta):
    """Inspect exactly 60 completed slots. Never iterate beyond the signal cutoff."""
    d = design()
    hour = []
    for index in range(60):
        try:
            f = next(prefix)
        except StopIteration:
            return None, "PILOT_MISSING_OPENING_HOUR"
        if f.start != win.opening + index * MINUTE:
            return None, "PILOT_MISSING_OPENING_HOUR"
        o = next((o for o in f.observations if o.instrument == "EUR_USD"), None)
        if issue := reason(o, Overlay()):
            return None, issue
        if issue := entry_reason(o, meta, settings):
            return None, issue
        hour.append(o)
    conversion = next((o for o in f.observations if o.instrument == "GBP_USD"), None)
    if issue := reason(conversion, Overlay()):
        return None, "PILOT_CONVERSION_" + issue
    # Existing conversion eligibility requires a current, complete, uncrossed quote.
    if conversion.candle.ask.c <= 0:
        return None, "PILOT_INVALID_CONVERSION"
    first, last = hour[0].candle.mid.o, hour[-1].candle.mid.c
    displacement = last - first
    spread = (hour[-1].candle.ask.c - hour[-1].candle.bid.c) * settings.costs.spread_multiplier
    friction = (
        spread
        + 2 * settings.costs.slippage_pips * meta.pip
        + 2 * settings.costs.commission_gbp_per_unit_per_side * conversion.candle.ask.c
    )
    if abs(displacement) / first < D(d["rule"]["minimum_absolute_return"]):
        return None, "PILOT_RETURN_BELOW_THRESHOLD"
    if (
        abs(displacement)
        < D(d["rule"]["minimum_displacement_to_estimated_roundtrip_cost"]) * friction
    ):
        return None, "PILOT_MOVE_BELOW_COST_THRESHOLD"
    direction = D(1) if displacement > 0 else D(-1)
    stop_fraction = D(d["rule"]["stop_fraction_of_actual_entry"])
    target_fraction = D(d["rule"]["target_fraction_of_actual_entry"])
    cutoff = win.opening + 60 * MINUTE
    return Intent(
        identity=digest([SHA, cutoff.isoformat(), str(direction)]),
        instrument="EUR_USD",
        side="long" if direction > 0 else "short",
        units=meta.maximum_units or D("1000000000"),
        stop=last * (1 - direction * stop_fraction),
        target=last * (1 + direction * target_fraction),
        signal_at=cutoff,
        information_cutoff=cutoff,
        evidence_start=win.opening,
        entry_rule=EntryRule(
            kind="PERCENT", stop_fraction=stop_fraction, target_fraction=target_fraction
        ),
    ), ""


def _rows(folder):
    previous, rows = "GENESIS", []
    for index, path in enumerate(sorted(folder.glob("*.json")), 1):
        if path.name != f"{index:06d}.json":
            raise AtlasError("PILOT_EVIDENCE_SEQUENCE_BROKEN")
        row = json.loads(path.read_text(encoding="utf-8"))
        seal = row.pop("checksum")
        if row["previous"] != previous or digest(row) != seal:
            raise AtlasError("PILOT_EVIDENCE_HASH_MISMATCH")
        row["checksum"] = seal
        rows.append(row)
        previous = seal
    return rows


def prepare_day(batch: Batch, folder: Path):
    """Append one synthetic day, three independent cost ledgers. Real data hard-blocked.

    Acquisition times are retained, not used to pretend candle-close quotes were
    actionable when actually received. This is retrospective assumed-price replay.
    """
    if batch.origin != "ARTIFICIAL_OFFLINE_FIXTURE":
        raise AtlasError("PILOT_EXTERNAL_DATA_NOT_AUTHORIZED")
    allowed = ROOT / "local/forex-forward-pilot/synthetic"
    folder = Path(folder).resolve()
    if not folder.is_relative_to(allowed):
        raise AtlasError("PILOT_SEPARATE_SYNTHETIC_DIRECTORY_REQUIRED")
    folder.mkdir(parents=True, exist_ok=True)
    # Cross-process exclusion: fail closed after an interrupted writer, never steal locks.
    lock = folder / ".writer.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with localcontext(arithmetic_context()):
            return _prepare_locked(batch, folder)
    finally:
        os.close(fd)
        lock.unlink()


def _prepare_locked(batch, folder):
    d = design()
    day_start = datetime.combine(batch.day, datetime.min.time(), UTC)
    begin = datetime.fromisoformat(d["evaluation"]["fixed_sample_start_utc"])
    end = datetime.fromisoformat(d["evaluation"]["fixed_sample_end_utc_exclusive"])
    if not begin <= day_start < end:
        raise AtlasError("PILOT_OUTSIDE_PREREGISTERED_WINDOW")
    prior = _rows(folder)
    processing_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if prior and (
        prior[-1]["processing_sha256"] != processing_sha
        or prior[-1]["execution_sha256"] != implementation_hash()
        or prior[-1]["source"]["instruments"] != batch.model_dump(mode="json")["instruments"]
    ):
        raise AtlasError("PILOT_PROCESSING_OR_INSTRUMENT_CONTRACT_CHANGED")
    if prior and batch.day.isoformat() <= prior[-1]["day"]:
        raise AtlasError("PILOT_DAY_ALREADY_PROCESSED_OR_OUT_OF_ORDER")
    frames = tuple(r.frame for r in batch.received)
    payload = batch.model_dump(mode="json")
    source_hash = digest(payload)
    run = Run(
        day=batch.day,
        session="LONDON",
        coverage_start=day_start,
        coverage_end=day_start + 1440 * MINUTE,
        code_version="PILOT_SYNTHETIC_V1",
        calendar_provenance="EXISTING_PHASE6B2_CALENDAR",
        origin="ARTIFICIAL_OFFLINE_FIXTURE",
    )
    meta = next((i for i in batch.instruments if i.name == "EUR_USD"), None)
    if meta is None:
        raise AtlasError("PILOT_INSTRUMENT_METADATA_MISSING")
    scenarios = {}
    for name in SCENARIOS:
        previous = prior[-1]["scenarios"][name] if prior else None
        cash = D(previous["ending_cash_gbp"]) if previous else D(1000)
        peak = D(previous["peak_equity_gbp"]) if previous else D(1000)
        if previous and previous["blocked"]:
            scenarios[name] = previous | {
                "reason": "PILOT_UNKNOWN_OR_STOPPED_CAPITAL",
                "execution": None,
            }
            continue
        cfg = configuration(name, min(cash, D(1000)))
        # Profits remain cash; do not silently discard them or raise risk/exposure caps.
        cfg = cfg.model_copy(update={"starting_capital_gbp": cash})
        try:
            win = window(run, cfg)
        except AtlasError as exc:
            scenarios[name] = {
                "ending_cash_gbp": str(cash),
                "peak_equity_gbp": str(peak),
                "blocked": False,
                "reason": str(exc),
                "execution": None,
            }
            continue
        if any(not win.opening <= f.start < win.flatten_at for f in frames):
            raise AtlasError("PILOT_FRAME_OUTSIDE_SESSION")
        by_time = {f.start: f for f in frames}
        # Empty clock slots are missing observations, never fabricated/filled prices.
        grid = tuple(
            by_time.get(
                win.opening + i * MINUTE, Frame(start=win.opening + i * MINUTE, observations=())
            )
            for i in range(int((win.flatten_at - win.opening) / MINUTE))
        )
        intent, issue = signal(iter(grid), cfg, win, meta)
        execution = evaluate_intent(
            cfg, run, batch.instruments, grid, intent, 59 if intent else None, source_hash
        )
        curve = [D(p[1]) for p in execution["curve"]]
        drawdown = D(0)
        for mark in curve:
            peak = max(peak, mark)
            drawdown = max(drawdown, peak - mark)
        ending = D(execution["cash_partial"])
        blocked = execution["invalidated"] > 0 or drawdown >= D(100) or ending <= 0
        scenarios[name] = {
            "starting_cash_gbp": str(cash),
            "ending_cash_gbp": str(ending),
            "peak_equity_gbp": str(peak),
            "blocked": blocked,
            "reason": issue,
            "execution": execution,
        }
    gap_start = (
        datetime.fromisoformat(prior[-1]["day"]).replace(tzinfo=UTC) + 1440 * MINUTE
        if prior
        else begin
    )
    row = {
        "version": "PILOT_SYNTHETIC_V1",
        "day": batch.day.isoformat(),
        "origin": batch.origin,
        "market_performance": "NOT EVALUATED",
        "strategy_sha256": SHA,
        "input_sha256": source_hash,
        "processing_sha256": processing_sha,
        "execution_sha256": implementation_hash(),
        "source": payload,
        "unrecorded_calendar_days": [
            (gap_start + i * 1440 * MINUTE).date().isoformat()
            for i in range(max(0, (day_start - gap_start).days))
        ],
        "previous": prior[-1]["checksum"] if prior else "GENESIS",
        "scenarios": scenarios,
    }
    row["checksum"] = digest(row)
    destination = folder / f"{len(prior) + 1:06d}.json"
    # Exclusive append-only record; checksum receipt returned to caller for external anchoring.
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return row | {"file_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def status_report():
    """Never mix fixture economics into a market-performance report."""
    d = design()
    return {
        "decision": "BLOCKED — DATA SOURCE",
        "strategy_sha256": SHA,
        "observation_period": [
            d["evaluation"]["fixed_sample_start_utc"],
            d["evaluation"]["fixed_sample_end_utc_exclusive"],
        ],
        "real_forward_data_authorized": False,
        "sample_sufficient": False,
        "metrics": {
            k: "NOT EVALUATED"
            for k in (
                "eligible_days",
                "missing_data_days",
                "signals",
                "trades",
                "net_pnl_gbp",
                "expectancy_gbp",
                "profit_factor",
                "maximum_drawdown_gbp",
                "capital_utilization",
                "cost_sensitivity",
                "data_quality_warnings",
                "strategy_rule_violations",
            )
        },
    }
