"""Offline HTML, PNG and durable fallback coverage; real HTTP remains forbidden."""

import json
import logging
from datetime import timedelta
from html.parser import HTMLParser
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from atlas.activity import ActivityEvent, EventType, now_utc
from atlas.config import AtlasError
from atlas.history import HistoryStore
from atlas.main import main
from atlas.notifications import TelegramProvider, TelegramSettings, deliver_one, format_message
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs
from atlas.telegram_cards import render_card
from atlas.telegram_media import record_result, route
from atlas.telegram_presentation import describe, gbp, native_price, plain_text, telegram_length
from atlas.telegram_preview import fictional_events


class ValidHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "code"} and not attrs
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


@pytest.fixture
def queued(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    store = HistoryStore(tmp_path / "visuals.sqlite3")
    report, run_id = recorded_backtest(
        generate(),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
        notify=True,
    )
    raw = store.events(run_id, kinds=(EventType.FILL,), descending=False)[0][0]
    return store, ActivityEvent.model_validate(raw), report


def settings(**changes):
    return TelegramSettings(True, "123456:" + "x" * 32, "123", True, cards_enabled=True, **changes)


def status(store, event):
    with store.connect() as db:
        return dict(
            db.execute("SELECT * FROM outbox WHERE event_id=?", (event.event_id,)).fetchone()
        )


@pytest.mark.parametrize("name", list(fictional_events()))
def test_all_designs_have_valid_html_and_required_provenance(name):
    event = fictional_events()[name]
    message = format_message(event)
    parser = ValidHTML()
    parser.feed(message)
    assert not parser.stack
    assert "<b>PROJECT ATLAS</b>" in message and "SYNTHETIC RESEARCH ONLY" in message
    assert event.event_id in message and event.run_id in message
    assert "fictional-orb-v1" in message and "14:32:00 UTC" in message
    assert telegram_length(message) <= 4096


@pytest.mark.parametrize(
    "name,expected",
    [
        ("buy", "🟢"),
        ("sell", "🔴"),
        ("profit", "🟢"),
        ("loss", "🔴"),
        ("partial", "PARTIAL FILL"),
        ("risk", "🟠"),
        ("halt", "🛑"),
        ("error", "🚨"),
        ("summary", "📊"),
    ],
)
def test_status_indicators_and_clear_headlines(name, expected):
    assert expected in format_message(fictional_events()[name])


@pytest.mark.parametrize(
    "unit,currency,price,expected",
    [
        ("GBX", "GBP", "250", "250 GBX (£2.50 GBP)"),
        ("GBX", "GBP", "2.5", "2.5 GBX (£0.025 GBP)"),
        ("GBP", "GBP", "2.5", "£2.50 GBP"),
        ("USD", "USD", "245", "$245.00 USD"),
        ("USD", "USD", "12.125", "$12.125 USD"),
    ],
)
def test_native_prices_preserve_units_factor_100_and_tick_precision(
    unit, currency, price, expected
):
    assert native_price(dict(quote_unit=unit, currency=currency, price=price)) == expected


def test_mismatched_quote_unit_is_unavailable():
    assert "N/A" in native_price(dict(quote_unit="GBX", currency="USD", price="250"))


def test_profit_loss_and_missing_values_are_not_invented():
    events = fictional_events()
    assert "+£4.80 GBP" in format_message(events["profit"])
    assert "-£6.25 GBP" in format_message(events["loss"])
    assert "POSITION CLOSED · LOSS" in format_message(events["loss"])
    assert "REALISED NET P/L: N/A" in plain_text(format_message(events["sell"]))
    assert "accounting has not confirmed" in format_message(events["sell"])
    assert "Gross GBP: N/A" in plain_text(format_message(events["buy"]))
    assert "validated FX" in format_message(events["buy"])
    assert "£0.20 GBP" in format_message(events["buy"])
    assert "Ending equity: N/A" in plain_text(format_message(events["summary"]))
    assert gbp("0", signed=True) == "£0.00 GBP"
    assert "outside display range" in gbp("1e1000000")


def test_partial_sell_does_not_claim_position_closed():
    event = fictional_events()["loss"]
    event = event.model_copy(
        update={"event_type": EventType.PARTIAL, "payload": {**event.payload, "partial": True}}
    )
    message = format_message(event)
    assert "SELL · PARTIAL FILL" in message and "POSITION CLOSED" not in message
    assert "Remaining position is not closed" in message and "-£6.25 GBP" in message


def test_untrusted_text_is_escaped_once_and_cannot_add_markup():
    event = fictional_events()["buy"]
    event.payload["unavailable_reason"] = '<b>fake</b> & "price" &lt;tag&gt;'
    message = format_message(event)
    assert "&lt;b&gt;fake&lt;/b&gt; &amp; &quot;price&quot; &amp;lt;tag&amp;gt;" in message
    assert '<b>fake</b> & "price" &lt;tag&gt;' in plain_text(message)
    event = event.model_copy(update={"strategy": "<script>fake</script>"})
    with pytest.raises(ValidationError):
        format_message(event)


def test_long_reasons_are_bounded_without_cutting_html_or_financial_fields():
    event = fictional_events()["risk"]
    event.payload["reasons"] = ["X" * 180] * 100
    message = format_message(event)
    parser = ValidHTML()
    parser.feed(message)
    assert not parser.stack and telegram_length(message) < 4096
    assert "[shortened]" in message and "£12.00 GBP" in message
    assert "not a guaranteed loss limit" in message


def test_position_transition_without_fill_does_not_invent_execution_or_pnl():
    event = ActivityEvent.create(
        run_id="a" * 64,
        event_type=EventType.CLOSED,
        ordinal=1,
        at=now_utc(),
        payload={"quantity_before": "2", "quantity_after": "0"},
    )
    message = format_message(event)
    assert "REALISED NET P/L: N/A" in plain_text(message)
    assert "Confirmed simulated fill" not in message


@pytest.mark.parametrize("name", list(fictional_events()))
def test_png_cards_use_local_values_and_are_mobile_sized(name):
    event = fictional_events()[name]
    data = render_card(event, fictional=True)
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) < 10_000_000
    with Image.open(BytesIO(data)) as image:
        assert image.size == (1080, 1120)
        assert image.mode == "RGB"
        assert image.getpixel((40, 40)) == tuple(bytes.fromhex(describe(event).accent[1:]))


def test_renderer_has_a_portable_font_fallback(monkeypatch):
    monkeypatch.setattr("atlas.telegram_cards._font_file", lambda: None)
    assert render_card(fictional_events()["profit"]).startswith(b"\x89PNG")


def test_design_preview_never_reads_credentials_database_or_network(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline preview attempted a database or network call")

    monkeypatch.setattr("atlas.telegram_preview._preview_root", lambda: tmp_path)
    monkeypatch.setattr("atlas.dashboard_cli.HistoryStore", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(TelegramSettings, "from_environment", forbidden)
    assert main(["notifications-design-preview"]) == 0
    assert "NO MESSAGE SENT" in capsys.readouterr().out
    for name in fictional_events():
        assert (tmp_path / (name + ".png")).is_file()
        assert (tmp_path / (name + ".telegram.html")).is_file()
        assert "FICTIONAL" in (tmp_path / (name + ".txt")).read_text(encoding="utf-8")
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "FICTIONAL DESIGN PREVIEW" in index and "<script" not in index


def test_cards_are_disabled_by_default_and_require_explicit_boolean(monkeypatch):
    assert not TelegramSettings().cards_enabled
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("ATLAS_TELEGRAM_BOT_TOKEN", settings().token)
    monkeypatch.setenv("ATLAS_TELEGRAM_CHAT_ID", settings().chat_id)
    monkeypatch.delenv("ATLAS_TELEGRAM_CARDS_ENABLED", raising=False)
    assert not TelegramSettings.from_environment(allow_synthetic=True).cards_enabled
    monkeypatch.setenv("ATLAS_TELEGRAM_CARDS_ENABLED", "true")
    assert TelegramSettings.from_environment(allow_synthetic=True).cards_enabled
    monkeypatch.setenv("ATLAS_TELEGRAM_CARDS_ENABLED", "invalid")
    with pytest.raises(AtlasError, match="CARDS_ENABLED"):
        TelegramSettings.from_environment(allow_synthetic=True)


def test_successful_card_delivery_is_deduplicated_and_does_not_mutate_portfolio(queued):
    store, event, report = queued
    requests = []

    def accept(request):
        requests.append(request)
        assert request.url.host == "api.telegram.org" and request.url.path.endswith("/sendPhoto")
        body = request.read()
        assert b"image/png" in body and b'filename="atlas.png"' in body
        assert b"HTML" in body and event.event_id.encode() in body
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    provider = TelegramProvider(settings(), transport=httpx.MockTransport(accept))
    now = now_utc()
    assert (
        deliver_one(store, provider, enabled=True, event_id=event.event_id, now=now) == "DELIVERED"
    )
    assert (
        deliver_one(
            store, provider, enabled=True, event_id=event.event_id, now=now + timedelta(seconds=10)
        )
        == "NOT_DUE"
    )
    assert len(requests) == 1 and status(store, event)["state"] == "SENT"
    assert store.run(event.run_id)["report"] == report.to_dict()


def test_render_failure_falls_back_to_complete_html(queued, monkeypatch):
    store, event, _ = queued

    def broken(event):
        raise OSError("private renderer detail")

    monkeypatch.setattr("atlas.telegram_cards.render_card", broken)
    calls = []

    def accept(request):
        calls.append(request.url.path.rsplit("/", 1)[1])
        body = json.loads(request.content)
        assert body["parse_mode"] == "HTML"
        assert event.event_id in body["text"] and "Fees / costs" in body["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    provider = TelegramProvider(settings(), transport=httpx.MockTransport(accept))
    assert deliver_one(store, provider, enabled=True, event_id=event.event_id) == "DELIVERED"
    assert calls == ["sendMessage"]


@pytest.mark.parametrize("http_status", [400, 403, 429])
def test_definite_photo_rejection_uses_persisted_backoff_then_html_fallback(queued, http_status):
    store, event, _ = queued
    calls = []

    def response(request):
        method = request.url.path.rsplit("/", 1)[1]
        calls.append(method)
        if method == "sendPhoto":
            return httpx.Response(
                http_status,
                json={"ok": False, "error_code": http_status, "parameters": {"retry_after": 2}},
            )
        assert json.loads(request.content)["parse_mode"] == "HTML"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    provider = TelegramProvider(settings(), transport=httpx.MockTransport(response))
    now = now_utc()
    result = deliver_one(store, provider, enabled=True, event_id=event.event_id, now=now)
    assert result in ("PHOTO_REJECTED_TEXT_FALLBACK", "TELEGRAM_RATE_LIMIT")
    assert calls == ["sendPhoto"] and status(store, event)["state"] == "RETRY"
    assert deliver_one(store, provider, enabled=True, event_id=event.event_id, now=now) == "NOT_DUE"
    reopened = HistoryStore(store.path)
    assert (
        deliver_one(
            reopened,
            provider,
            enabled=True,
            event_id=event.event_id,
            now=now + timedelta(seconds=100),
        )
        == "DELIVERED"
    )
    assert calls == ["sendPhoto", "sendMessage"]


@pytest.mark.parametrize(
    "outcome", ["timeout", "server_error", "invalid_json", "missing_id", "missing_ok", "redirect"]
)
def test_uncertain_photo_response_is_terminal_with_no_fallback_or_blind_retry(queued, outcome):
    store, event, report = queued
    calls = []

    def uncertain(request):
        calls.append(request.url.path.rsplit("/", 1)[1])
        if outcome == "timeout":
            raise httpx.ReadTimeout("must not disclose private URL", request=request)
        if outcome == "server_error":
            return httpx.Response(503)
        if outcome == "invalid_json":
            return httpx.Response(200, text="invalid")
        if outcome == "missing_ok":
            return httpx.Response(200, json={})
        if outcome == "redirect":
            return httpx.Response(302, json={}, headers={"Location": "https://example.invalid"})
        return httpx.Response(200, json={"ok": True, "result": {}})

    provider = TelegramProvider(settings(), transport=httpx.MockTransport(uncertain))
    now = now_utc()
    assert (
        deliver_one(store, provider, enabled=True, event_id=event.event_id, now=now)
        == "PHOTO_OUTCOME_UNCERTAIN"
    )
    assert (
        deliver_one(
            HistoryStore(store.path),
            provider,
            enabled=True,
            event_id=event.event_id,
            now=now + timedelta(seconds=100),
        )
        == "NOT_DUE"
    )
    assert calls == ["sendPhoto"] and status(store, event)["state"] == "FAILED"
    assert store.run(event.run_id)["report"] == report.to_dict()


@pytest.mark.parametrize("recorded_success", [False, True])
@pytest.mark.parametrize("cards_enabled_after_restart", [False, True])
def test_crashed_photo_attempt_cannot_resend_after_restart_or_card_toggle(
    queued, recorded_success, cards_enabled_after_restart
):
    store, event, _ = queued
    now = now_utc()
    source, attempt = store.claim_notification("crashed-worker", now, event_id=event.event_id)
    assert route(store, source, "crashed-worker", attempt, want_photo=True) == "photo"
    if recorded_success:
        record_result(store, source, "crashed-worker", attempt, "PHOTO_DELIVERED")

    def forbidden(request):
        pytest.fail("An old photo upload must not trigger any additional user-visible message")

    config = TelegramSettings(
        True, settings().token, "123", True, cards_enabled=cards_enabled_after_restart
    )
    provider = TelegramProvider(config, transport=httpx.MockTransport(forbidden))
    result = deliver_one(
        HistoryStore(store.path),
        provider,
        enabled=True,
        event_id=event.event_id,
        now=now + timedelta(seconds=31),
    )
    assert result == ("DELIVERED" if recorded_success else "PHOTO_OUTCOME_UNCERTAIN")
    assert status(store, event)["state"] == ("SENT" if recorded_success else "FAILED")


def test_lost_outbox_claim_cannot_record_photo_or_upload(queued):
    store, event, _ = queued
    store.claim_notification("current-worker", event_id=event.event_id)
    with pytest.raises(AtlasError, match="lease lost"):
        route(store, event, "obsolete-worker", 1, want_photo=True)
    assert store.events(event.run_id, kinds=(EventType.DELIVERY_ATTEMPT,))[1] == 1


def test_oversize_caption_uses_complete_html_without_photo(queued, monkeypatch):
    store, event, _ = queued
    monkeypatch.setattr("atlas.notifications.telegram_length", lambda message: 1025)
    calls = []

    def accept(request):
        calls.append(request.url.path.rsplit("/", 1)[1])
        assert event.event_id in json.loads(request.content)["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    provider = TelegramProvider(settings(), transport=httpx.MockTransport(accept))
    assert deliver_one(store, provider, enabled=True, event_id=event.event_id) == "DELIVERED"
    assert calls == ["sendMessage"]


def test_no_execution_or_strategy_authority_added():
    from atlas.notifications import NOTIFIABLE

    assert EventType.SIGNAL not in NOTIFIABLE and EventType.INTENT not in NOTIFIABLE
    assert not hasattr(TelegramProvider, "place_order")


def test_photo_transport_logs_redact_credentials(queued, caplog):
    store, event, _ = queued
    provider = TelegramProvider(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        ),
    )
    with caplog.at_level(logging.DEBUG):
        assert deliver_one(store, provider, enabled=True, event_id=event.event_id) == "DELIVERED"
    assert settings().token not in caplog.text
    assert "[REDACTED]" in caplog.text


@pytest.mark.parametrize("name", ["profit", "loss", "risk"])
def test_card_text_comes_from_event_values_and_labels_fictional_samples(name, monkeypatch):
    from PIL import ImageDraw

    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    event = fictional_events()[name]
    render_card(event, fictional=True)
    assert describe(event).figure in drawn
    assert any("FICTIONAL DESIGN PREVIEW" in line for line in drawn)


def test_disabling_notifications_cannot_upload_or_generate_a_card(queued, monkeypatch):
    store, event, _ = queued

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled delivery tried to generate or send a card")

    monkeypatch.setattr("atlas.telegram_cards.render_card", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    config = TelegramSettings(cards_enabled=True)
    assert deliver_one(store, TelegramProvider(config), enabled=False) == "DISABLED"
    assert status(store, event)["attempts"] == 0


def test_receipt_failure_before_upload_is_atomic_and_makes_no_request(queued, monkeypatch):
    store, event, _ = queued
    source, attempt = store.claim_notification("current-worker", event_id=event.event_id)
    before = store.events(event.run_id)[1]
    append = store.append

    def interrupted(db, event):
        append(db, event)
        raise OSError("simulated local interruption")

    monkeypatch.setattr(store, "append", interrupted)
    with pytest.raises(OSError):
        route(store, source, "current-worker", attempt, want_photo=True)
    assert store.events(event.run_id)[1] == before
    monkeypatch.setattr(store, "append", append)
    assert route(store, source, "current-worker", attempt, want_photo=True) == "photo"
