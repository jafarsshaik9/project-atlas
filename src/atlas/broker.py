"""Single read-only endpoint; fixed demo origin, no redirects or environment proxies."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from email.utils import parsedate_to_datetime

import httpx

from atlas.config import AtlasError, Settings

_SUMMARY_URL = "https://demo.trading212.com/api/v0/equity/account/summary"


@dataclass(frozen=True)
class AccountSummary:
    currency: str
    total_value: Decimal
    available_cash: Decimal

    @classmethod
    def parse(cls, payload: object) -> "AccountSummary":
        try:
            if not isinstance(payload, dict):
                raise ValueError
            currency = payload["currency"]
            if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError
            amounts = (payload["totalValue"], payload["cash"]["availableToTrade"])
            if any(isinstance(v, bool) or not isinstance(v, (int, Decimal)) for v in amounts):
                raise ValueError
            total, cash = map(Decimal, amounts)
            if not total.is_finite() or not cash.is_finite():
                raise ValueError
            return cls(currency, total, cash)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise AtlasError("Invalid account-summary response schema.") from None


def _retry_delay(headers: httpx.Headers, now: float) -> float:
    """Respect broker delays; refuse delays beyond our 30-second wait budget."""
    delays = [5.0]
    for name in ("retry-after", "x-ratelimit-reset"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw)
            delays.append(value - now if name == "x-ratelimit-reset" else value)
        except ValueError:
            if name == "retry-after":
                try:
                    delays.append(parsedate_to_datetime(raw).timestamp() - now)
                except (ValueError, TypeError, OverflowError):
                    pass
    delay = max(delays)
    if not 0 <= delay <= 30:
        raise AtlasError("Broker requested a longer wait; retry manually later.")
    return delay


class Trading212Client:
    """At most three attempts, spaced at least five seconds within this client.

    Rate limits are account-wide; coordinate other processes and API keys separately.
    Transport injection is for offline tests only.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        settings.require_credentials()
        self._auth = httpx.BasicAuth(settings.api_key, settings.api_secret)
        self._transport = transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None

    def account_summary(self) -> AccountSummary:
        with httpx.Client(
            auth=self._auth,
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        ) as client:
            for attempt in range(3):
                if self._last_request is not None:
                    self._sleep(max(0, 5 - (self._monotonic() - self._last_request)))
                self._last_request = self._monotonic()
                try:
                    response = client.get(_SUMMARY_URL)
                except httpx.TransportError:
                    if attempt == 2:
                        raise AtlasError(
                            "Broker network/TLS/timeout failure after 3 attempts."
                        ) from None
                    continue
                status = response.status_code
                if status == 200:
                    try:
                        payload = json.loads(response.content, parse_float=Decimal)
                    except (ValueError, UnicodeError):
                        raise AtlasError("Broker returned invalid JSON.") from None
                    return AccountSummary.parse(payload)
                if status == 401:
                    raise AtlasError("HTTP 401: check the demo API Key and API Secret.")
                if status == 403:
                    raise AtlasError("HTTP 403: check account read permission and IP restrictions.")
                if status == 429 or status == 408 or 500 <= status <= 599:
                    if attempt == 2:
                        raise AtlasError(
                            f"HTTP {status}: broker unavailable/rate limited after 3 attempts."
                        )
                    self._sleep(_retry_delay(response.headers, time.time()))
                    continue
                raise AtlasError(f"HTTP {status}: request refused; redirects are disabled.")
        raise AssertionError("Unreachable retry state")
