"""Original fictional notification fixtures, separate from persisted research history."""

from datetime import UTC, datetime
from html import escape
from pathlib import Path

from atlas.activity import ActivityEvent, EventType
from atlas.config import AtlasError
from atlas.telegram_cards import render_card
from atlas.telegram_presentation import format_message, plain_text


def fictional_events():
    at = datetime(2026, 10, 26, 14, 32, tzinfo=UTC)
    base = dict(
        fill_id="fictional-fill",
        intent_id="fictional-intent",
        side="buy",
        quantity="2",
        price="245",
        quote_unit="USD",
        currency="USD",
        venue="XNAS",
        fx_gbp_per_unit=None,
        gross_gbp=None,
        fees_gbp="0.20",
        realized_pnl_gbp=None,
        components=(),
        partial=False,
        unavailable_reason="Fictional fixture supplies no validated FX",
    )
    cases = {
        "buy": (EventType.FILL, base),
        "sell": (
            EventType.FILL,
            {
                **base,
                "side": "sell",
                "quote_unit": "GBX",
                "currency": "GBP",
                "venue": "XLON",
                "price": "250",
                "gross_gbp": "5",
                "fx_gbp_per_unit": "1",
            },
        ),
        "profit": (
            EventType.FILL,
            {
                **base,
                "side": "sell",
                "quote_unit": "GBP",
                "currency": "GBP",
                "venue": "XLON",
                "price": "25.50",
                "gross_gbp": "51",
                "fx_gbp_per_unit": "1",
                "realized_pnl_gbp": "4.80",
            },
        ),
        "loss": (EventType.FILL, {**base, "side": "sell", "realized_pnl_gbp": "-6.25"}),
        "partial": (EventType.PARTIAL, {**base, "quantity": "0.5", "partial": True}),
        "risk": (
            EventType.BREACH,
            {
                "code": "FICTIONAL_GAP_LOSS",
                "actual_loss_gbp": "12",
                "planned_risk_gbp": "5",
                "reasons": ("GAP_BEYOND_STOP",),
            },
        ),
        "halt": (EventType.KILL, {"code": "FICTIONAL_KILL_SWITCH"}),
        "error": (EventType.ERROR, {"code": "FICTIONAL_SYSTEM_ERROR"}),
        "summary": (
            EventType.RUN_COMPLETED,
            {
                "dataset": "fictional-design-fixture",
                "venue": "XLON",
                "dataset_checksum": "b" * 64,
                "configuration_checksum": "c" * 64,
                "initial_capital_gbp": "1000",
                "ending_equity_gbp": None,
                "net_pnl_gbp": None,
                "completed_trades": None,
            },
        ),
    }
    return {
        name: ActivityEvent.create(
            run_id="f" * 64,
            event_type=kind,
            ordinal="fictional-" + name,
            at=at,
            processed_at=at,
            payload=payload,
            instrument_id="FICTIONAL-EQUITY",
            strategy="fictional-orb-v1",
        )
        for name, (kind, payload) in cases.items()
    }


def _preview_root():
    return Path(__file__).resolve().parents[2] / "local" / "telegram-previews"


def save_previews() -> Path:
    # Fixed ignored project-local directory; never reads user credentials or the history DB.
    root = _preview_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        sections = []
        for name, event in fictional_events().items():
            message = format_message(event)
            (root / (name + ".txt")).write_text(
                "FICTIONAL DESIGN PREVIEW — NO MESSAGE SENT\n\n" + plain_text(message),
                encoding="utf-8",
            )
            (root / (name + ".telegram.html")).write_text(message, encoding="utf-8")
            (root / (name + ".png")).write_bytes(render_card(event, fictional=True))
            sections.append(
                f"<section><h2>{escape(name.upper())}</h2>"
                f'<img src="{name}.png" alt="Fictional {name} trade card">'
                f'<div class="message">{message}</div></section>'
            )
        (root / "index.html").write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'\">"
            "<title>Atlas — fictional Telegram previews</title><style>"
            "body{background:#080f1e;color:#edf4ff;font:16px system-ui;margin:28px}"
            "main{max-width:1100px;margin:auto}h1{color:#73dfed}"
            "section{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:40px 0}"
            "h2{grid-column:1/-1}img{width:100%;border-radius:18px}"
            ".message{white-space:pre-wrap;background:#142238;border-radius:18px;padding:24px;"
            "overflow-wrap:anywhere;line-height:1.6}code{font-size:12px;color:#a6dbe2}"
            "@media(max-width:700px){section{grid-template-columns:1fr}body{margin:16px}}"
            "</style><main><h1>PROJECT ATLAS</h1><p>FICTIONAL DESIGN PREVIEW · SYNTHETIC</p>"
            "<p>No messages sent. These invented examples are not performance "
            "or fee assumptions.</p>" + "".join(sections) + "</main></html>",
            encoding="utf-8",
        )
    except (OSError, ImportError, ValueError):
        raise AtlasError(
            "Preview unavailable; install the approved cards extra and check local permissions."
        ) from None
    return root
