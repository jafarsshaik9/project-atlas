"""Opt-in Telegram delivery; persistent retries have no portfolio side effects."""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

import httpx

from atlas.activity import ActivityEvent, Environment, EventType, now_utc
from atlas.config import AtlasError
from atlas.telegram_presentation import format_message, telegram_length

NOTIFIABLE = frozenset(
    {EventType.FILL, EventType.PARTIAL, EventType.BREACH, EventType.KILL, EventType.ERROR}
)


class RedactTelegram(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(r"(/bot)[^/\s'\"]+", r"\1[REDACTED]", message)
        message = re.sub(r"\d{6,}:[A-Za-z0-9_-]{20,}", "[REDACTED]", message)
        record.msg, record.args = message, ()
        return True


def protect_transport_logs():
    for name in (
        "httpx",
        "httpcore",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.connection",
        "httpcore.proxy",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(item, RedactTelegram) for item in logger.filters):
            logger.addFilter(RedactTelegram())


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool = False
    token: str = field(default="", repr=False)
    chat_id: str = field(default="", repr=False)
    allow_synthetic: bool = False
    discovery_only: bool = False
    cards_enabled: bool = False
    multi_recipient: bool = False

    def __post_init__(self):
        if self.enabled and (
            not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", self.token)
            or (
                not self.discovery_only
                and not self.multi_recipient
                and not re.fullmatch(r"-?\d{1,20}", self.chat_id)
            )
        ):
            raise AtlasError("Telegram configuration is missing or invalid; enter it locally.")

    @classmethod
    def from_environment(cls, *, allow_synthetic=False, multi_recipient=False):
        raw = os.environ.get("ATLAS_TELEGRAM_ENABLED", "false").lower()
        if raw not in ("true", "false"):
            raise AtlasError("ATLAS_TELEGRAM_ENABLED must be true or false.")
        if raw == "false":
            return cls()
        cards = os.environ.get("ATLAS_TELEGRAM_CARDS_ENABLED", "false").lower()
        if cards not in ("true", "false"):
            raise AtlasError("ATLAS_TELEGRAM_CARDS_ENABLED must be true or false.")
        return cls(
            True,
            os.environ.get("ATLAS_TELEGRAM_BOT_TOKEN", ""),
            "" if multi_recipient else os.environ.get("ATLAS_TELEGRAM_CHAT_ID", ""),
            allow_synthetic,
            cards_enabled=cards == "true",
            multi_recipient=multi_recipient,
        )


@dataclass(frozen=True)
class DeliveryResult:
    code: str
    retry: bool = False
    delay: int = 0


class NotificationProvider(Protocol):
    def send(self, event: ActivityEvent) -> DeliveryResult: ...


class TelegramProvider:
    def __init__(self, settings: TelegramSettings, *, transport=None):
        self.settings = settings
        self.transport = transport
        protect_transport_logs()

    def request(self, method, payload, *, photo=None):
        # Fixed official origin/methods; no redirects, proxies or exception URLs.
        if method not in ("sendMessage", "sendPhoto", "getUpdates"):
            raise AtlasError("Unsupported Telegram method.")
        if not self.settings.enabled:
            return None, DeliveryResult("DISABLED")
        if self.settings.discovery_only and method != "getUpdates":
            raise AtlasError("Private-chat discovery cannot send messages.")
        if (method == "sendPhoto") != (photo is not None):
            raise AtlasError("Invalid Telegram photo request.")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                url = f"https://api.telegram.org/bot{self.settings.token}/{method}"
                response = (
                    client.post(
                        url, data=payload, files={"photo": ("atlas.png", photo, "image/png")}
                    )
                    if photo is not None
                    else client.post(url, json=payload)
                )
        except httpx.RequestError:
            return None, DeliveryResult("NETWORK_OUTCOME_UNCERTAIN", True)
        if response.status_code >= 500:
            return None, DeliveryResult("TELEGRAM_UNAVAILABLE", True)
        if response.status_code in (401, 403):
            return None, DeliveryResult("TELEGRAM_AUTH_REJECTED")
        try:
            data = response.json()
        except ValueError:
            return None, DeliveryResult("TELEGRAM_INVALID_RESPONSE")
        if not isinstance(data, dict):
            return None, DeliveryResult("TELEGRAM_INVALID_RESPONSE")
        if response.status_code == 429 or data.get("error_code") == 429:
            parameters = data.get("parameters", {})
            delay = parameters.get("retry_after", 60) if isinstance(parameters, dict) else 60
            if not isinstance(delay, int) or isinstance(delay, bool) or not 1 <= delay <= 604800:
                delay = 60
            return None, DeliveryResult("TELEGRAM_RATE_LIMIT", True, delay)
        if data.get("ok") is False and 200 <= response.status_code < 500:
            return None, DeliveryResult("TELEGRAM_REJECTED")
        if not 200 <= response.status_code < 300 or data.get("ok") is not True:
            return None, DeliveryResult("TELEGRAM_INVALID_RESPONSE")
        return data.get("result"), None

    def send(self, event):
        if (
            not self.settings.enabled
            or not self.settings.allow_synthetic
            or self.settings.discovery_only
        ):
            return DeliveryResult("DISABLED")
        if event.environment != Environment.SYNTHETIC:
            return DeliveryResult("UNSUPPORTED_ENVIRONMENT")
        if self.settings.multi_recipient:
            return DeliveryResult("RECIPIENT_ROUTING_REQUIRED")
        result, error = self.request(
            "sendMessage",
            {
                "chat_id": self.settings.chat_id,
                "text": format_message(event),
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )
        if error:
            return error
        if not self.valid_message(result):
            return DeliveryResult("TELEGRAM_INVALID_RESPONSE")
        return DeliveryResult("DELIVERED")

    @staticmethod
    def valid_message(result):
        return (
            isinstance(result, dict)
            and type(result.get("message_id")) is int
            and result["message_id"] > 0
        )

    def send_claimed(self, event, store, owner, attempt, *, delivery_id=None):
        """Photo delivery requires a durable outbox claim; direct send stays HTML-only."""
        from atlas.telegram_cards import CARD_EVENTS, render_card
        from atlas.telegram_media import record_result, route

        if (
            not self.settings.enabled
            or not self.settings.allow_synthetic
            or self.settings.discovery_only
            or event.environment != Environment.SYNTHETIC
        ):
            return self.send(event)
        message = format_message(event)
        # Oversized captions select complete text; never silently cut financial fields or tags.
        want_photo = (
            self.settings.cards_enabled
            and event.event_type in CARD_EVENTS
            and telegram_length(message) <= 1024
        )
        selected = route(
            store, event, owner, attempt, want_photo=want_photo, delivery_id=delivery_id
        )
        if selected == "delivered":
            return DeliveryResult("DELIVERED")
        if selected == "uncertain":
            return DeliveryResult("PHOTO_OUTCOME_UNCERTAIN")
        if selected == "text":
            return self.send(event)
        try:
            photo = render_card(event)
        except (ImportError, OSError, ValueError):
            record_result(store, event, owner, attempt, "PHOTO_NOT_SENT", delivery_id=delivery_id)
            return self.send(event)
        result, error = self.request(
            "sendPhoto",
            {"chat_id": self.settings.chat_id, "caption": message, "parse_mode": "HTML"},
            photo=photo,
        )
        if error is None and self.valid_message(result):
            record_result(store, event, owner, attempt, "PHOTO_DELIVERED", delivery_id=delivery_id)
            return DeliveryResult("DELIVERED")
        if error and error.code == "TELEGRAM_RATE_LIMIT":
            record_result(store, event, owner, attempt, "PHOTO_RATE_LIMIT", delivery_id=delivery_id)
            return error
        if error and error.code in ("TELEGRAM_REJECTED", "TELEGRAM_AUTH_REJECTED"):
            record_result(store, event, owner, attempt, "PHOTO_REJECTED", delivery_id=delivery_id)
            # Use the existing backoff/rate gate for text fallback, not a second immediate send.
            return DeliveryResult("PHOTO_REJECTED_TEXT_FALLBACK", True)
        record_result(
            store, event, owner, attempt, "PHOTO_OUTCOME_UNCERTAIN", delivery_id=delivery_id
        )
        return DeliveryResult("PHOTO_OUTCOME_UNCERTAIN")


def deliver_one(store, provider: NotificationProvider, *, enabled=False, event_id=None, now=None):
    if not enabled:
        return "DISABLED"
    from atlas.telegram_recipients_store import recipient_mode

    if recipient_mode(store):
        from atlas.telegram_fanout import deliver_recipient_one

        if isinstance(provider, TelegramProvider) and not provider.settings.enabled:
            return "DISABLED"
        if not isinstance(provider, TelegramProvider) or not provider.settings.multi_recipient:
            raise AtlasError("Verified-recipient mode is active; use the recipient sender.")
        return deliver_recipient_one(store, provider, event_id=event_id, now=now)
    if isinstance(provider, TelegramProvider) and provider.settings.multi_recipient:
        raise AtlasError("Enable verified-recipient mode before starting its sender.")
    owner = uuid4().hex
    claimed = store.claim_notification(owner, now, event_id=event_id)
    if claimed is None:
        return "NOT_DUE"
    event, attempt = claimed
    result = (
        provider.send_claimed(event, store, owner, attempt)
        if isinstance(provider, TelegramProvider)
        else provider.send(event)
    )
    delay = max(result.delay, min(60, 2**attempt)) if result.retry else 0
    store.finish_notification(
        event, owner, attempt, code=result.code, retry=result.retry, delay=delay, now=now
    )
    return result.code


def enqueue_test(store):
    run = store.run()
    if run is None or run["status"] != "COMPLETED":
        raise AtlasError("Complete one persisted synthetic run before testing notifications.")
    event = ActivityEvent.create(
        run_id=run["run_id"],
        event_type=EventType.TEST,
        ordinal=uuid4().hex,
        at=now_utc(),
        payload={"code": "USER_REQUESTED_CONNECTION_TEST"},
    )
    with store.connect(write=True) as db:
        store.append(db, event)
        db.execute(
            "INSERT INTO outbox(event_id,next_at) VALUES (?,?)",
            (event.event_id, now_utc().isoformat()),
        )
    return event.event_id
