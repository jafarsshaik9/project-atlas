"""Read models derived only from selected-run persisted events and ledger reports."""

import json
import os
from dataclasses import fields
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from atlas.activity import ActivityEvent, Contract, EventType, Money, now_utc
from atlas.config import AtlasError
from atlas.research import ResearchSettings


class Envelope[T](Contract):
    environment: Literal["SYNTHETIC"] = "SYNTHETIC"
    run_id: str | None
    updated_at: datetime | None
    stale: bool = True
    reason: str | None
    data: T


class Summary(Contract):
    starting_capital_gbp: Money | None = None
    equity_gbp: Money | None = None
    available_cash_gbp: Money | None = None
    invested_value_gbp: Money | None = None
    reserved_cash_gbp: Money | None = None
    realized_pnl_gbp: Money | None = None
    unrealized_pnl_gbp: Money | None = None
    total_pnl_gbp: Money | None = None
    total_return_pct: Money | None = None
    today_pnl_gbp: Money | None = None
    today_reason: str | None = None
    research_day_pnl_gbp: Money | None = None
    open_positions: int = 0
    completed_trades: int = 0
    unrealized_basis: str = "Entry fees included; future exit costs excluded."


class HistoryPoint(Contract):
    at: datetime
    equity_gbp: Money
    realized_pnl_gbp: Money
    unrealized_pnl_gbp: Money
    total_pnl_gbp: Money
    drawdown_gbp: Money | None


class ChartHistory(Contract):
    points: list[HistoryPoint]
    starting_capital_gbp: Money | None
    available_ranges: list[str]
    truncated: bool
    total_points: int


class Page[T](Contract):
    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=1000)
    offset: int = Field(ge=0)


class RiskView(Contract):
    starting_capital_gbp: Money | None
    per_trade_pct: Money | None
    per_trade_budget_gbp: Money | None
    daily_pct: Money | None
    daily_threshold_gbp: Money | None
    concurrent_pct: Money | None
    concurrent_limit_gbp: Money | None
    planned_open_risk_gbp: Money | None
    daily_realized_loss_gbp: Money | None
    exposure_gbp: Money | None
    kill_switch: bool | None
    remaining_budget_gbp: Money | None = None
    remaining_budget_reason: str = "Historical snapshot; not a fresh risk authorization."
    margin_gbp: Literal["0.00"] = "0.00"
    margin_reason: str = "Not applicable: long-only, unleveraged research."
    leverage: Literal["None"] = "None"
    alerts: list[ActivityEvent]


class HealthView(Contract):
    dashboard: Literal["RUNNING"] = "RUNNING"
    engine: Literal["STOPPED", "IDLE", "RUNNING", "ERROR"]
    database: Literal["OK"] = "OK"
    database_schema: int
    last_observed_activity: datetime | None
    active_heartbeat: datetime | None
    last_successful_run: str | None
    last_market_scan: None = None
    scanner_reason: str = "No market scanner or continuous trading worker is implemented."
    strategy: str | None
    latest_signal: dict | None
    latest_error: dict | None
    last_event_at: datetime | None
    notification_configuration: str
    notification_delivery: dict
    freshness: str = "Historical synthetic fixture, not a live market feed."


class RunView(Contract):
    run_id: str
    status: str
    created_at: datetime
    dataset: str
    venue: str


class ReadModels:
    def __init__(self, store):
        self.store = store

    def context(self, run_id):
        run = self.store.run(run_id)
        if run_id and run is None:
            raise AtlasError("Research run was not found.")
        events = (
            self.store.events(run["run_id"], kinds=(EventType.VALUATION,), limit=1)[0]
            if run
            else []
        )
        snapshot = events[0] if events else None
        return run, snapshot

    @staticmethod
    def envelope(run, snapshot, data):
        return {
            "run_id": run["run_id"] if run else None,
            "updated_at": snapshot["event_at"] if snapshot else None,
            "stale": True,
            "reason": "Historical synthetic fixture; values are as-of the selected run."
            if snapshot
            else "No completed portfolio valuation is available. Run synthetic-backtest.",
            "data": data,
        }

    def summary(self, run_id=None):
        run, event = self.context(run_id)
        if event is None:
            return self.envelope(run, event, Summary())
        payload = event["payload"]
        amount = {
            key: Decimal(value)
            for key, value in payload.items()
            if key.endswith("_gbp") and value is not None
        }
        total = amount["equity_gbp"] - amount["initial_capital_gbp"]
        today = datetime.fromisoformat(event["event_at"]).date() == now_utc().date()
        completed = self.store.events(run["run_id"], kinds=(EventType.CLOSED,), limit=1)[1]
        data = Summary(
            starting_capital_gbp=amount["initial_capital_gbp"],
            equity_gbp=amount["equity_gbp"],
            available_cash_gbp=amount["available_cash_gbp"],
            invested_value_gbp=amount["invested_value_gbp"],
            reserved_cash_gbp=amount["reserved_cash_gbp"],
            realized_pnl_gbp=amount["realized_pnl_gbp"],
            unrealized_pnl_gbp=amount["unrealized_pnl_gbp"],
            total_pnl_gbp=total,
            total_return_pct=total / amount["initial_capital_gbp"] * 100,
            today_pnl_gbp=amount["daily_pnl_gbp"] if today else None,
            today_reason=None if today else "No persisted valuation dated today (UTC).",
            research_day_pnl_gbp=amount["daily_pnl_gbp"],
            open_positions=len(payload["positions"]),
            completed_trades=completed,
        )
        return self.envelope(run, event, data)

    def history(self, run_id=None, *, range_name="all", limit=1000):
        run, snapshot = self.context(run_id)
        if run is None:
            return self.envelope(
                run,
                snapshot,
                ChartHistory(
                    points=[],
                    starting_capital_gbp=None,
                    available_ranges=["all"],
                    truncated=False,
                    total_points=0,
                ),
            )
        events, count = self.store.events(
            run["run_id"], kinds=(EventType.VALUATION,), limit=limit, descending=True
        )
        events.reverse()
        if snapshot is None and events:
            snapshot = events[-1]
        first = self.store.events(
            run["run_id"], kinds=(EventType.VALUATION,), limit=1, descending=False
        )[0]
        ranges = ["all"]
        if events:
            last = datetime.fromisoformat(events[-1]["event_at"])
            span = last - datetime.fromisoformat(first[0]["event_at"])
            ranges += [
                name
                for name, days in (("day", 1), ("week", 7), ("month", 31))
                if span >= timedelta(days=days)
            ]
            if range_name not in ranges:
                raise AtlasError("Insufficient persisted history for the selected period.")
            if range_name != "all":
                days = {"day": 1, "week": 7, "month": 31}[range_name]
                events = [
                    e
                    for e in events
                    if datetime.fromisoformat(e["event_at"]) >= last - timedelta(days=days)
                ]
        points = [
            HistoryPoint(
                at=e["event_at"],
                equity_gbp=e["payload"]["equity_gbp"],
                realized_pnl_gbp=e["payload"]["realized_pnl_gbp"],
                unrealized_pnl_gbp=e["payload"]["unrealized_pnl_gbp"],
                total_pnl_gbp=Decimal(e["payload"]["realized_pnl_gbp"])
                + Decimal(e["payload"]["unrealized_pnl_gbp"]),
                drawdown_gbp=e["payload"].get("drawdown_gbp"),
            )
            for e in events
        ]
        return self.envelope(
            run,
            snapshot,
            ChartHistory(
                points=points,
                starting_capital_gbp=snapshot["payload"]["initial_capital_gbp"]
                if snapshot
                else None,
                available_ranges=ranges,
                truncated=count > limit,
                total_points=count,
            ),
        )

    def positions(self, run_id=None):
        run, event = self.context(run_id)
        return self.envelope(run, event, event["payload"]["positions"] if event else [])

    def trades(
        self,
        run_id=None,
        *,
        search="",
        side=None,
        sort="timestamp",
        direction="desc",
        limit=25,
        offset=0,
    ):
        run, snapshot = self.context(run_id)
        if run is None:
            return self.envelope(run, snapshot, Page(items=[], total=0, limit=limit, offset=offset))
        where = ["run_id=?", "event_type IN (?,?)"]
        args = [run["run_id"], EventType.FILL.value, EventType.PARTIAL.value]
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("json_extract(content,'$.instrument_id') LIKE ? ESCAPE '\\'")
            args.append("%" + escaped + "%")
        if side:
            where.append("json_extract(content,'$.payload.side')=?")
            args.append(side)
        columns = {
            "timestamp": "event_at",
            "instrument": "json_extract(content,'$.instrument_id')",
            "side": "json_extract(content,'$.payload.side')",
        }
        if sort not in columns or direction not in ("asc", "desc"):
            raise AtlasError("Unsupported trade sort.")
        clause = " WHERE " + " AND ".join(where)
        with self.store.connect(snapshot=True) as db:
            count = db.execute("SELECT count(*) FROM events" + clause, args).fetchone()[0]
            rows = db.execute(
                "SELECT content FROM events"
                + clause
                + " ORDER BY "
                + columns[sort]
                + " "
                + direction
                + ",sequence "
                + direction
                + " LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return self.envelope(
            run,
            snapshot,
            Page(
                items=[json.loads(row[0]) for row in rows], total=count, limit=limit, offset=offset
            ),
        )

    def risk(self, run_id=None):
        run, event = self.context(run_id)
        raw = run["metadata"].get("settings", {}) if run else {}
        settings = (
            ResearchSettings(
                **{key: Decimal(value) for key, value in run["metadata"]["settings"].items()}
            )
            if run and set(raw) == {field.name for field in fields(ResearchSettings)}
            else None
        )
        payload = event["payload"] if event else {}
        alerts = (
            self.store.events(
                run["run_id"],
                kinds=(EventType.RISK_REJECTED, EventType.BREACH, EventType.KILL),
                limit=20,
            )[0]
            if run
            else []
        )
        data = RiskView(
            starting_capital_gbp=settings.starting_capital_gbp if settings else None,
            per_trade_pct=settings.planned_risk_per_trade_pct if settings else None,
            per_trade_budget_gbp=settings.planned_risk_per_trade_gbp if settings else None,
            daily_pct=settings.daily_loss_threshold_pct if settings else None,
            daily_threshold_gbp=settings.daily_loss_threshold_gbp if settings else None,
            concurrent_pct=settings.max_concurrent_planned_risk_pct if settings else None,
            concurrent_limit_gbp=settings.max_concurrent_planned_risk_gbp if settings else None,
            planned_open_risk_gbp=payload.get("planned_open_risk_gbp"),
            daily_realized_loss_gbp=max(Decimal(0), -Decimal(payload["daily_realized_pnl_gbp"]))
            if event
            else None,
            exposure_gbp=payload.get("invested_value_gbp"),
            kill_switch=payload.get("kill_switch"),
            alerts=alerts,
        )
        return self.envelope(run, event, data)

    def health(self, run_id=None):
        run, snapshot = self.context(run_id)
        events = self.store.events(run["run_id"], limit=1)[0] if run else []
        signals = (
            self.store.events(run["run_id"], kinds=(EventType.SIGNAL,), limit=1)[0] if run else []
        )
        errors = (
            self.store.events(run["run_id"], kinds=(EventType.ERROR,), limit=1)[0] if run else []
        )
        with self.store.connect() as db:
            schema = db.execute("PRAGMA user_version").fetchone()[0]
            success = db.execute(
                "SELECT run_id FROM runs WHERE status='COMPLETED' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        engine = (
            (
                "ERROR"
                if run["status"] in ("ERROR", "INTERRUPTED")
                else "RUNNING"
                if run["status"] == "RUNNING"
                else "STOPPED"
            )
            if run
            else "STOPPED"
        )
        enabled = os.environ.get("ATLAS_TELEGRAM_ENABLED", "false").lower() == "true"
        present = (
            bool(os.environ.get("ATLAS_TELEGRAM_BOT_TOKEN"))
            and bool(os.environ.get("ATLAS_TELEGRAM_CHAT_ID"))
            if enabled
            else False
        )
        configuration = (
            "Disabled (default)"
            if not enabled
            else "Present; delivery unverified"
            if present
            else "Enabled; configuration missing"
        )
        from atlas.telegram_fanout import notification_status

        delivery = notification_status(self.store)
        if delivery.get("mode") == "VERIFIED_RECIPIENTS":
            configuration = (
                "Verified recipients: paused"
                if delivery["paused"]
                else "Verified recipients: sender disabled"
                if not enabled
                else "Verified recipients: configuration present; delivery unverified"
                if bool(os.environ.get("ATLAS_TELEGRAM_BOT_TOKEN"))
                else "Verified recipients: token must be supplied to the sender"
            )
        observed = delivery["last_sender_configuration"]
        if observed:
            configuration = (
                "Last sender configuration: enabled"
                if observed["enabled"]
                else "Last sender configuration: disabled"
            )
            configuration += " (not evidence of an active sender)"
        return self.envelope(
            run,
            snapshot,
            HealthView(
                database_schema=schema,
                engine=engine,
                last_observed_activity=run["updated_at"] if run else None,
                active_heartbeat=run["updated_at"] if engine == "RUNNING" else None,
                last_successful_run=success[0] if success else None,
                strategy=run["metadata"].get("strategy") if run else None,
                latest_signal=signals[0] if signals else None,
                latest_error=errors[0] if errors else None,
                last_event_at=events[0]["event_at"] if events else None,
                notification_configuration=configuration,
                notification_delivery=delivery,
            ),
        )
