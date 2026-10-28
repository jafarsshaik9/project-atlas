"""Replay journal and sealed projections within the scheduler's local SQLite database."""

import json
from decimal import Decimal

from atlas.config import AtlasError
from atlas.domain import Currency
from atlas.market_data.contracts import digest
from atlas.paper_execution.codec import decode, dump, public
from atlas.portfolio import PortfolioLedger
from atlas.research import ResearchSettings

TABLES = (
    "execution_state",
    "execution_inbox",
    "execution_signals",
    "execution_orders",
    "execution_fills",
    "execution_journal",
)
OPERATIONS = {"snapshot", "mark", "reserve", "cancel", "entry_fill", "exit_fill"}


def seal(db):
    return digest(
        {
            table: [tuple(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in TABLES
        }
    )


def save_seal(db):
    db.execute("UPDATE execution_meta SET seal=?", (seal(db),))


def state(db):
    return decode(json.loads(db.execute("SELECT content FROM execution_state").fetchone()[0]))


def write_state(db, value):
    db.execute("UPDATE execution_state SET content=?", (dump(value),))


def apply(ledger, command):
    if command["op"] not in OPERATIONS:
        raise AtlasError("Unknown paper accounting command.")
    return getattr(ledger, command["op"])(*command["args"], **command["kwargs"])


def record(db, ledger, op, *args, **kwargs):
    command = {"op": op, "args": args, "kwargs": kwargs}
    result = apply(ledger, command)
    journal_applied(db, op, *args, **kwargs)
    return result


def journal_applied(db, op, *args, **kwargs):
    """Append an already-applied public operation (RiskEngine.authorize reserves itself)."""
    command = {"op": op, "args": args, "kwargs": kwargs}
    prior = db.execute(
        "SELECT seq,hash FROM execution_journal ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    seq, previous = (prior[0] + 1, prior[1]) if prior else (0, "GENESIS")
    content = dump(command)
    db.execute(
        "INSERT INTO execution_journal VALUES(?,?,?,?)",
        (seq, content, previous, digest([seq, content, previous])),
    )


def load(db):
    """Never restore private ledger fields: replay its public validated operations."""
    try:
        meta = db.execute("SELECT version,seal FROM execution_meta").fetchall()
        if len(meta) != 1 or meta[0][0] != 1 or meta[0][1] != seal(db):
            raise AtlasError("Paper ledger seal/schema mismatch.")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise AtlasError("Paper ledger SQLite integrity failure.")
        ledger = PortfolioLedger(ResearchSettings())
        previous = "GENESIS"
        for expected, row in enumerate(db.execute("SELECT * FROM execution_journal ORDER BY seq")):
            seq, content, prev, checksum = row
            if seq != expected or prev != previous or checksum != digest([seq, content, prev]):
                raise AtlasError("Paper journal chain mismatch.")
            apply(ledger, decode(json.loads(content)))
            previous = checksum
        saved = state(db)
        actual = ledger.snapshot(saved["last_time"])
        if not ledger.reconcile() or dump(actual) != dump(saved["snapshot"]):
            raise AtlasError("Paper account reconciliation failure.")
        if len(ledger.history) != db.execute("SELECT count(*) FROM execution_fills").fetchone()[0]:
            raise AtlasError("Paper fill projection mismatch.")
        projected = {f["fill_id"]: f for f in rows(db, "execution_fills")}
        orders = {order["id"]: order for order in rows(db, "execution_orders")}
        for fill in ledger.history:
            item = projected.get(fill.fill_id)
            if item is None or (
                item["side"],
                item["quantity"],
                item["price_usd"],
                item["fx_gbp_per_usd"],
                item["fees_gbp"],
                item["timestamp"],
                item["realised_pnl_gbp"],
                item["gross_pnl_gbp"],
                item["cost_components"],
            ) != (
                fill.side,
                fill.quantity,
                fill.price,
                fill.fx_rate,
                fill.fees_gbp,
                fill.timestamp,
                fill.realized_pnl_gbp,
                fill.gross_pnl_gbp,
                fill.cost_components,
            ):
                raise AtlasError("Paper fill/accounting reconciliation failure.")
            order = orders[item["order_id"]]
            risk_id = order["signal"].intent_id if fill.side == "buy" else order["id"]
            if not any(
                d.intent_id == risk_id
                and d.status.value == "approved"
                and d.timestamp <= fill.timestamp
                for d in saved["decisions"]
            ):
                raise AtlasError("Paper fill has no independent risk approval.")
        for order in orders.values():
            filled = sum(
                (f["quantity"] for f in projected.values() if f["order_id"] == order["id"]),
                Decimal("0"),
            )
            if order["quantity"] is not None and (
                order["quantity"] - filled != order["remaining"]
                or order["remaining"] < 0
                or (order["status"] == "FILLED" and order["remaining"] != 0)
            ):
                raise AtlasError("Paper order quantity reconciliation failure.")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise AtlasError("Paper ledger foreign key failure.")
        if saved["ack"] != db.execute("SELECT count(*)-1 FROM execution_inbox").fetchone()[0]:
            raise AtlasError("Paper inbox reconciliation failure.")
        for expected, inbox in enumerate(
            db.execute("SELECT * FROM execution_inbox ORDER BY ordinal")
        ):
            source = db.execute(
                "SELECT content FROM paper_results WHERE stream_id=? AND ordinal=?",
                (saved["stream"], expected),
            ).fetchone()
            if inbox[0] != expected or source is None or digest(json.loads(source[0])) != inbox[1]:
                raise AtlasError("Paper source inbox mismatch.")
        return ledger, saved
    except (ValueError, KeyError, TypeError, AssertionError):
        raise AtlasError("Paper ledger replay/reconciliation failure.") from None


def initialize(store, stream, binding, at):
    with store.connect(write=True) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "execution_meta" in names:
            _, saved = load(db)
            if saved["stream"] != stream or saved["binding"] != binding:
                raise AtlasError("Paper execution configuration mismatch.")
            return
        if any(name.startswith("execution_") for name in names):
            raise AtlasError("Partial execution schema; migration refused.")
        db.execute("CREATE TABLE execution_meta(version INTEGER NOT NULL,seal TEXT NOT NULL)")
        db.execute("INSERT INTO execution_meta VALUES(1,'')")
        db.execute("CREATE TABLE execution_state(id INTEGER PRIMARY KEY CHECK(id=1),content TEXT)")
        db.execute(
            "CREATE TABLE execution_inbox(ordinal INTEGER PRIMARY KEY,checksum TEXT,outcome TEXT)"
        )
        db.execute("CREATE TABLE execution_signals(id TEXT PRIMARY KEY,content TEXT NOT NULL)")
        db.execute("""CREATE TABLE execution_orders(id TEXT PRIMARY KEY,
                   signal_id TEXT NOT NULL REFERENCES execution_signals(id),
                   content TEXT NOT NULL)""")
        db.execute("""CREATE TABLE execution_fills(id TEXT PRIMARY KEY,
                   order_id TEXT NOT NULL REFERENCES execution_orders(id),
                   observation_id TEXT NOT NULL,
                   content TEXT NOT NULL,UNIQUE(order_id,observation_id))""")
        db.execute("""CREATE TABLE execution_journal(seq INTEGER PRIMARY KEY,content TEXT NOT NULL,
                   previous TEXT NOT NULL,hash TEXT NOT NULL)""")
        ledger = PortfolioLedger(ResearchSettings())
        snapshot = record(db, ledger, "snapshot", at)
        saved = {
            "stream": stream,
            "binding": binding,
            "ack": -1,
            "last_time": at,
            "kill": False,
            "status": "READY",
            "snapshot": snapshot,
            "bars": (),
            "samples": (),
            "day": None,
            "decisions": [],
        }
        db.execute("INSERT INTO execution_state VALUES(1,?)", (dump(saved),))
        save_seal(db)


def rows(db, table):
    if table not in ("execution_orders", "execution_signals", "execution_fills"):
        raise AtlasError("Unknown paper projection.")
    return [
        decode(json.loads(r[0])) for r in db.execute(f"SELECT content FROM {table} ORDER BY id")
    ]


def report(store):
    with store.connect() as db:
        ledger, saved = load(db)
        snapshot = saved["snapshot"]
        return {
            "scope": "ARTIFICIAL_OFFLINE_PAPER",
            "broker_execution": False,
            "account_currency": "GBP",
            "initial_capital_gbp": "1000.00",
            "status": saved["status"],
            "kill_switch": saved["kill"],
            "trading_ack": saved["ack"],
            "as_of": saved["last_time"].isoformat(),
            "cash_gbp": str(dict(snapshot.cash_by_currency)[Currency.GBP]),
            "equity_gbp": str(snapshot.equity_gbp),
            "realised_pnl_gbp": str(snapshot.realized_pnl_gbp),
            "fees_gbp": str(ledger.total_fees_gbp),
            "reconciled": snapshot.consistent,
            "positions": public(snapshot.positions),
            "orders": public(rows(db, "execution_orders")),
            "signals": public(rows(db, "execution_signals")),
            "recent_simulated_trades": public(
                sorted(rows(db, "execution_fills"), key=lambda f: f["timestamp"])[-50:]
            ),
            "risk_decisions": public(saved["decisions"][-50:]),
        }
