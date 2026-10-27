import logging
from datetime import timedelta

import httpx
import pytest

from atlas.activity import ActivityEvent, EventType, now_utc
from atlas.config import AtlasError
from atlas.history import HistoryStore
from atlas.main import main
from atlas.notifications import (
    TelegramProvider,
    TelegramSettings,
    deliver_one,
    format_message,
)
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs


@pytest.fixture
def queued(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "true")
    store = HistoryStore(tmp_path / "notifications.sqlite3")
    report, run_id = recorded_backtest(
        generate(),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
        notify=True,
    )
    return store, report, run_id


def settings(**kwargs):
    return TelegramSettings(True, "123456:" + "x" * 32, "123", True, **kwargs)


def success(request):
    assert request.url.host == "api.telegram.org" and request.method == "POST"
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def test_default_disable_does_not_read_tokens_or_make_network_calls(monkeypatch):
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "false")
    original = __import__("os").environ.get

    def forbidden(name, default=None):
        if name in ("ATLAS_TELEGRAM_BOT_TOKEN", "ATLAS_TELEGRAM_CHAT_ID"):
            pytest.fail("Disabled provider tried to read credentials")
        return original(name, default)

    monkeypatch.setattr(__import__("os").environ, "get", forbidden)
    assert not TelegramSettings.from_environment().enabled


def test_disabled_outbox_is_not_claimed_and_preview_has_no_network(queued, monkeypatch, capsys):
    store, _, _ = queued
    monkeypatch.setenv("ATLAS_HISTORY_DB", str(store.path))

    def forbidden(*args, **kwargs):
        pytest.fail("Preview or disabled worker attempted network")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert deliver_one(store, TelegramProvider(TelegramSettings()), enabled=False) == "DISABLED"
    assert main(["notifications-preview"]) == 0
    text = capsys.readouterr().out
    assert "OFFLINE PREVIEW" in text and "SYNTHETIC" in text
    assert store.notification_status()["counts"] == {"PENDING": 2}


def test_successful_mock_delivery_is_audited_idempotent_and_has_no_portfolio_effect(queued):
    store, report, run_id = queued
    provider = TelegramProvider(settings(), transport=httpx.MockTransport(success))
    now = now_utc()
    assert deliver_one(store, provider, enabled=True, now=now) == "DELIVERED"
    assert deliver_one(store, provider, enabled=True, now=now) == "NOT_DUE"
    assert deliver_one(store, provider, enabled=True, now=now + timedelta(seconds=2)) == "DELIVERED"
    assert deliver_one(store, provider, enabled=True, now=now + timedelta(seconds=4)) == "NOT_DUE"
    assert store.notification_status()["counts"] == {"SENT": 2}
    assert store.run(run_id)["report"] == report.to_dict()
    assert store.events(run_id, kinds=(EventType.DELIVERY_ATTEMPT,))[1] == 2
    assert store.events(run_id, kinds=(EventType.DELIVERY_OK,))[1] == 2


@pytest.mark.parametrize(
    "status,retry",
    [(401, False), (403, False), (400, False), (429, True), (500, True), (503, True)],
)
def test_telegram_failures_have_safe_static_codes_and_retry_policy(queued, status, retry):
    store, _, run_id = queued
    event = ActivityEvent.model_validate(
        store.events(run_id, kinds=(EventType.FILL,), limit=1)[0][0]
    )
    token = settings().token

    def response(request):
        return httpx.Response(
            status, json={"ok": False, "description": token, "parameters": {"retry_after": 90}}
        )

    result = TelegramProvider(settings(), transport=httpx.MockTransport(response)).send(event)
    assert result.retry is retry and token not in str(result)
    if status == 429:
        assert result.delay == 90


def test_lost_response_has_bounded_retries_and_final_failure(queued):
    store, _, run_id = queued
    first = store.events(run_id, kinds=(EventType.FILL,), limit=1)[0][0]["event_id"]

    def timeout(request):
        raise httpx.ReadTimeout("private URL must not be logged", request=request)

    provider = TelegramProvider(settings(), transport=httpx.MockTransport(timeout))
    now = now_utc()
    for attempt in range(5):
        assert (
            deliver_one(
                store,
                provider,
                enabled=True,
                event_id=first,
                now=now + timedelta(seconds=100 * attempt),
            )
            == "NETWORK_OUTCOME_UNCERTAIN"
        )
    assert (
        deliver_one(
            store, provider, enabled=True, event_id=first, now=now + timedelta(seconds=1000)
        )
        == "NOT_DUE"
    )
    with store.connect() as db:
        row = db.execute("SELECT state,attempts FROM outbox WHERE event_id=?", (first,)).fetchone()
    assert row["state"] == "FAILED" and row["attempts"] == 5


def test_crashed_final_attempt_is_reconciled_without_sixth_send(queued):
    store, _, run_id = queued
    now = now_utc()
    event = store.events(run_id, kinds=(EventType.FILL,), limit=1)[0][0]
    with store.connect(write=True) as db:
        db.execute(
            "UPDATE outbox SET state='SENDING',attempts=5,lease_until=? WHERE event_id=?",
            ((now - timedelta(seconds=1)).isoformat(), event["event_id"]),
        )

    class Provider:
        def send(self, event):
            pytest.fail("Sixth send is prohibited")

    assert (
        deliver_one(store, Provider(), enabled=True, event_id=event["event_id"], now=now)
        == "NOT_DUE"
    )
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT state FROM outbox WHERE event_id=?", (event["event_id"],)
            ).fetchone()[0]
            == "FAILED"
        )


def test_http_logging_redacts_telegram_token_and_config_repr(queued, caplog):
    store, _, run_id = queued
    event = ActivityEvent.model_validate(
        store.events(run_id, kinds=(EventType.FILL,), limit=1)[0][0]
    )
    with caplog.at_level(logging.DEBUG):
        result = TelegramProvider(settings(), transport=httpx.MockTransport(success)).send(event)
    assert result.code == "DELIVERED"
    assert settings().token not in caplog.text and settings().token not in repr(settings())
    assert settings().chat_id not in repr(settings())


def test_notification_requires_confirmed_fill_and_preserves_native_units(queued):
    store, _, run_id = queued
    events, _ = store.events(run_id, kinds=(EventType.FILL,), limit=100)
    for raw in events:
        message = format_message(ActivityEvent.model_validate(raw))
        assert "SYNTHETIC" in message and "GBX" in message and "GBP" in message
        assert "Confirmed simulated" in message and raw["event_id"] in message
    assert all(e["event_type"] not in (EventType.SIGNAL, EventType.INTENT) for e in events)


@pytest.mark.parametrize("kind", [EventType.BREACH, EventType.KILL, EventType.ERROR])
def test_critical_alert_messages_are_supported_without_trade_claims(kind):
    event = ActivityEvent.create(
        run_id="a" * 64,
        event_type=kind,
        ordinal=0,
        at=now_utc(),
        payload={"code": "TEST_RESEARCH_ALERT"},
    )
    message = format_message(event)
    assert "SYNTHETIC" in message and "Confirmed simulated" not in message


def test_reimport_does_not_enqueue_duplicate_notifications(queued):
    store, _, _ = queued
    before = store.notification_status()
    recorded_backtest(
        generate(),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
        notify=True,
    )
    assert store.notification_status() == before


def test_send_command_requires_local_config_and_explicit_synthetic_opt_in(monkeypatch, capsys):
    assert main(["notifications-send"]) == 1
    assert "--allow-synthetic" in capsys.readouterr().err
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "false")
    assert main(["notifications-test", "--allow-synthetic"]) == 1


def test_recipient_discovery_verifies_nonce_without_printing_chat_id(monkeypatch, capsys):
    from atlas.telegram_setup import discover_private_chat

    monkeypatch.setattr("atlas.telegram_setup.secrets.token_hex", lambda size: "nonce-test")
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    def updates(self, method, payload):
        assert method == "getUpdates"
        return [
            {
                "message": {
                    "text": "/atlas_setup nonce-test",
                    "chat": {"type": "private", "id": 987654},
                    "from": {"id": 987654},
                }
            }
        ], None

    monkeypatch.setattr(TelegramProvider, "request", updates)
    assert discover_private_chat(settings().token) == "987654"
    assert "987654" not in capsys.readouterr().out


def test_discovery_mode_cannot_send_a_message():
    provider = TelegramProvider(
        TelegramSettings(enabled=True, token=settings().token, discovery_only=True)
    )
    with pytest.raises(AtlasError, match="cannot send"):
        provider.request("sendMessage", {"text": "must-not-send"})
