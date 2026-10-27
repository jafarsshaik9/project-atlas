"""Presentation of confirmed event values; no ledger, strategy or delivery mutations."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from html import escape
from html.parser import HTMLParser

from atlas.activity import ActivityEvent, Environment, EventType


@dataclass(frozen=True)
class Notice:
    title: str
    icon: str
    accent: str
    figure: str
    figure_label: str
    details: tuple[tuple[str, str], ...]
    note: str


def bounded(value, maximum=180):
    text = str(value) if value is not None else "N/A"
    # No control or bidi characters that can disguise financial values or labels.
    text = "".join(c for c in text if c.isprintable())
    return text if len(text) <= maximum else text[: maximum - 14] + "… [shortened]"


def number(value, *, money=False, signed=False):
    if value is None:
        return "N/A"
    value = Decimal(value)
    if (
        not value.is_finite()
        or (value and abs(value.adjusted()) > 18)
        or len(value.as_tuple().digits) > 36
    ):
        return "N/A (outside display range)"
    result = format(value, ",.2f") if money else format(value, "f")
    if not money:
        result = format(value, "f")
        if "." in result:
            result = result.rstrip("0").rstrip(".")
    return ("+" if signed and value > 0 else "") + result


def gbp(value, *, signed=False):
    if value is None:
        return "N/A"
    value = Decimal(value)
    sign = "-" if value < 0 else "+" if signed and value > 0 else ""
    return sign + "£" + number(value.copy_abs(), money=True) + " GBP"


def native_price(payload):
    unit, currency = payload["quote_unit"], payload["currency"]
    if currency != ("GBP" if unit in ("GBP", "GBX") else "USD"):
        return "N/A — inconsistent quotation units"
    value = Decimal(payload["price"])

    def price(amount):
        result = number(amount)
        if "N/A" not in result:
            if "." not in result:
                return result + ".00"
            if len(result.rsplit(".", 1)[1]) == 1:
                return result + "0"
        return result

    if unit == "GBX":
        if "N/A" in number(value):
            return "N/A (outside display range)"
        with localcontext() as context:
            context.prec = 40
            return f"{number(value)} GBX (£{price(value / 100)} GBP)"
    symbol = "£" if unit == "GBP" else "$"
    return f"{symbol}{price(value)} {unit}"


def describe(event: ActivityEvent) -> Notice:
    """Project values for display; missing accounting/FX stays explicitly unavailable."""
    p = event.payload
    if event.event_type in (EventType.FILL, EventType.PARTIAL):
        buy, partial = p["side"] == "buy", p["partial"]
        pnl = p["realized_pnl_gbp"] if not buy else None
        title = ("BUY" if buy else "SELL") + (" · PARTIAL FILL" if partial else " FILLED")
        accent = "#39d7a0" if buy else "#ff7185"
        figure, label = gbp(p["gross_gbp"]), "GROSS REPORTING VALUE"
        if not buy:
            figure, label = gbp(pnl, signed=True), "REALISED NET P/L"
            if pnl is not None:
                positive, negative = Decimal(pnl) > 0, Decimal(pnl) < 0
                if not partial:
                    title = "POSITION CLOSED · " + (
                        "PROFIT" if positive else "LOSS" if negative else "BREAK EVEN"
                    )
                accent = "#39d7a0" if positive else "#ff7185" if negative else "#9eafc8"
        note = "Confirmed simulated " + ("partial fill" if partial else "fill")
        details = [
            ("Instrument", bounded(event.instrument_id)),
            ("Exchange", p["venue"]),
            ("Side", p["side"].upper()),
            ("Filled quantity", number(p["quantity"]) + " shares"),
            ("Fill price", native_price(p)),
            ("Native currency", p["currency"]),
            ("Gross GBP", gbp(p["gross_gbp"])),
            ("Fees / costs", gbp(p["fees_gbp"])),
        ]
        if p["gross_gbp"] is None:
            note += " · GBP value N/A: " + bounded(
                p.get("unavailable_reason") or "validated FX unavailable", 100
            )
        if not buy:
            details.append(("Realised net P/L", gbp(pnl, signed=True)))
            if pnl is None:
                note += " · P/L N/A: accounting has not confirmed a value"
            if partial:
                note += " · Remaining position is not closed"
        icon = "🟢" if buy or (pnl is not None and Decimal(pnl) > 0) else "🔴"
        return Notice(title, icon, accent, figure, label, tuple(details), note)
    if event.event_type == EventType.RUN_COMPLETED:
        return Notice(
            "RESEARCH COMPLETE",
            "📊",
            "#52d8e8",
            gbp(p["net_pnl_gbp"], signed=True),
            "RESEARCH NET P/L",
            (
                ("Dataset", bounded(p["dataset"])),
                ("Exchange", p["venue"]),
                ("Starting capital", gbp(p["initial_capital_gbp"])),
                ("Ending equity", gbp(p["ending_equity_gbp"])),
                ("Completed trades", number(p["completed_trades"])),
            ),
            "Artificial research results do not establish profitability.",
        )
    if event.event_type == EventType.CLOSED:
        return Notice(
            "POSITION CLOSED",
            "🔴",
            "#9eafc8",
            "N/A",
            "REALISED NET P/L",
            (
                ("Instrument", bounded(event.instrument_id)),
                ("Quantity before", number(p["quantity_before"])),
                ("Quantity after", number(p["quantity_after"])),
            ),
            "Position transition only; financial values require a confirmed fill event.",
        )
    titles = {
        EventType.BREACH: ("RISK WARNING", "🟠", "#ffbd69"),
        EventType.KILL: ("TRADING HALT", "🛑", "#ffbd69"),
        EventType.ERROR: ("CRITICAL SYSTEM ERROR", "🚨", "#ff7185"),
        EventType.TEST: ("CONNECTION TEST", "🧪", "#52d8e8"),
    }
    title, icon, accent = titles.get(
        event.event_type, ("RESEARCH EVENT · NO FILL", "📊", "#9eafc8")
    )
    details = [("Code", bounded(p.get("code") or event.event_type.value))]
    if event.instrument_id:
        details.append(("Instrument", bounded(event.instrument_id)))
    for key, label in (("actual_loss_gbp", "Actual loss"), ("planned_risk_gbp", "Planned risk")):
        if p.get(key) is not None:
            details.append((label, gbp(p[key])))
    if p.get("reasons"):
        details.append(("Reasons", bounded(" · ".join(p["reasons"]), 320)))
    note = "No trade executed by this alert."
    if event.event_type in (EventType.BREACH, EventType.KILL):
        note += " Planned stop risk is not a guaranteed loss limit."
    status = {EventType.KILL: "HALTED", EventType.ERROR: "ERROR", EventType.TEST: "TEST"}.get(
        event.event_type, "REVIEW"
    )
    return Notice(
        title,
        icon,
        accent,
        gbp(p["actual_loss_gbp"]) if p.get("actual_loss_gbp") is not None else status,
        "ACTUAL LOSS" if p.get("actual_loss_gbp") is not None else "RESEARCH ALERT",
        tuple(details),
        note,
    )


def format_message(event: ActivityEvent) -> str:
    event = ActivityEvent.model_validate(event.model_dump())
    notice = describe(event)
    environment = event.environment.value
    badge = "🧪" if event.environment == Environment.SYNTHETIC else "◯"
    lines = [
        f"{badge} <b>PROJECT ATLAS</b>",
        f"<b>{environment} RESEARCH ONLY</b>",
        "",
        f"{notice.icon} <b>{escape(notice.title)}</b>",
        f"<b>{escape(notice.figure_label)}: {escape(notice.figure)}</b>",
        "━━━━━━━━━━━━━━━━",
    ]
    lines.extend(f"{escape(label)}: <b>{escape(value)}</b>" for label, value in notice.details)
    lines.extend(
        [
            "━━━━━━━━━━━━━━━━",
            f"<i>{escape(bounded(notice.note, 360))}</i>",
            f"Strategy: {escape(bounded(event.strategy))}",
            f"UTC: {event.event_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Event: <code>{event.event_id}</code>",
            f"Run: <code>{event.run_id}</code>",
        ]
    )
    return "\n".join(lines)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(html):
    parser = _Text()
    parser.feed(html)
    return "".join(parser.parts)


def telegram_length(html):
    """Conservative UTF-16 entity length; never slice through tags or escaped entities."""
    return len(plain_text(html).encode("utf-16-le")) // 2
