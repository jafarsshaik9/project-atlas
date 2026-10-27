"""Massive US common-stock history. No broker, research ledger or notification imports."""

import base64
import binascii
import hashlib
import json
import logging
import re
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote_to_bytes, urlsplit
from zoneinfo import ZoneInfo

import httpx

from atlas.domain import Currency, QuoteUnit, Venue
from atlas.market_data.contracts import (
    Adjustment,
    HistoricalInstrument,
    Origin,
    ProviderDescriptor,
    RawPage,
    SymbolMapping,
    canonical,
)
from atlas.market_data.massive_credentials import load_massive_key
from atlas.market_data.providers import ProviderError, _Redact

ORIGIN = "https://api.massive.com"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_RESPONSE_BYTES = 8_000_000
REQUEST_DEADLINE_SECONDS = 30
MAX_RETRY_AFTER_SECONDS = 86_400


def milliseconds(value):
    delta = value - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def identity_instrument(evidence):
    """Only dated, allowlisted reference evidence can establish this mapping."""
    if (
        evidence["locale"] != "us"
        or evidence["market"] != "stocks"
        or evidence["type"] != "CS"
        or evidence["active"] is not True
        or evidence["currency_name"] != "usd"
        or evidence["primary_exchange"] not in ("XNYS", "XNAS")
        or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,19}", evidence["ticker"])
        or not re.fullmatch(r"BBG[A-Z0-9]{9}", evidence["composite_figi"])
    ):
        raise ValueError("Unsupported or incomplete Massive identity.")
    day = date.fromisoformat(evidence["as_of"])
    zone = ZoneInfo("America/New_York")
    start = datetime.combine(day, datetime.min.time(), zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).astimezone(UTC)
    venue = Venue(evidence["primary_exchange"])
    return HistoricalInstrument(
        instrument_id="MASSIVE-" + evidence["composite_figi"],
        venue=venue,
        trading_currency=Currency.USD,
        quote_unit=QuoteUnit.USD,
        instrument_timezone="America/New_York",
        exchange_timezone="America/New_York",
        identifiers=(("composite_figi", evidence["composite_figi"]),),
        metadata_evidence="Massive dated ticker reference " + day.isoformat(),
        mappings=(
            SymbolMapping(
                provider="massive",
                symbol=evidence["ticker"],
                mic=venue,
                currency_code="USD",
                effective_from=start,
                effective_to=end,
                metadata_source="Massive v3 reference/tickers; as-of " + day.isoformat(),
            ),
        ),
    )


def verify_evidence(request, body):
    """Reused at publication/read boundaries, not just trusted at HTTP ingestion."""
    meta = body["meta"]
    identity = identity_instrument(meta["massive_identity"])
    if identity != request.instrument:
        raise ValueError("Massive identity does not match request.")
    evidence = body["aggregate_evidence"]
    if (
        evidence["ticker"]
        != request.instrument.mapping("massive", request.start, request.end).symbol
        or type(evidence["adjusted"]) is not bool
        or evidence["adjusted"] != (request.adjustment == Adjustment.SPLITS)
        or request.adjustment not in (Adjustment.RAW, Adjustment.SPLITS)
        or request.assumed_delay_seconds is not None
        or evidence["status"] != "OK"
        or evidence["resultsCount"] != len(body["values"])
        or type(evidence["resultsCount"]) is not int
        or type(evidence["queryCount"]) is not int
        or evidence["queryCount"] < evidence["resultsCount"]
        or evidence["queryCount"] > 5000
    ):
        raise ValueError("Massive aggregate evidence is inconsistent.")


class _TransportPrivacy(logging.Filter):
    """Remove the credential and every URL query from third-party transport logs."""

    def __init__(self, key):
        super().__init__()
        self.key = key

    def filter(self, record):
        message = record.getMessage().replace(self.key, "[REDACTED]")
        message = re.sub(
            r"(https://api\.massive\.com/[^\s?'\"]+)\?[^\s'\"]+", r"\1?[REDACTED_QUERY]", message
        )
        message = re.sub(
            r"(?i)(authorization[= :]+)(?:bearer\s+)?[^\s&'\"]+", r"\1[REDACTED]", message
        )
        record.msg, record.args = message, ()
        return True


@contextmanager
def transport_privacy(key):
    # httpcore logs have several child loggers; parent filters alone do not suffice.
    names = {
        "httpx",
        "httpcore",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.connection",
        "httpcore.proxy",
        "httpcore.http_proxy",
        "httpcore.socks_proxy",
        "httpcore.connection_pool",
    }
    names.update(
        n for n in logging.Logger.manager.loggerDict if n.startswith(("httpx.", "httpcore."))
    )
    guards = (_Redact(key), _TransportPrivacy(key))
    loggers = [logging.getLogger(n) for n in names]
    for logger in loggers:
        for guard in guards:
            logger.addFilter(guard)
    try:
        yield
    finally:
        for logger in loggers:
            for guard in guards:
                logger.removeFilter(guard)


class MassiveProvider:
    descriptor = ProviderDescriptor(
        provider_id="massive",
        origin=Origin.HISTORICAL,
        exchanges=(Venue.NYSE, Venue.NASDAQ),
        authentication="Lazy local key; Bearer header only",
        coverage=(
            "Dated US common stocks; single exchange-local session/day per request; "
            "Basic access unverified"
        ),
        pagination="Validated same-origin/same-path continuation; bounded 8192-character cursor",
        rate_limit="Shared SQLite; <=4 attempts/rolling 60s, 16s spacing, completion-time charging",
        retry_policy="<=3 attempts; persistent Retry-After; pilot 1 attempt/request and <=4 total",
        licensing="Owner-authorised internal research; account rights require separate review",
        network_required=True,
        activation_status="EXPLICIT_ACCESS_REQUIRED",
        generation_parameters=(("normalization", "massive-us-aggregates-v1"),),
    )

    def __init__(
        self,
        quota,
        *,
        allow_network=False,
        access_reference="",
        key=None,
        key_loader=None,
        transport=None,
        clock=lambda: datetime.now(UTC),
        monotonic=time.monotonic,
        attempts=3,
        pilot=None,
    ):
        if not allow_network or not access_reference or access_reference.startswith("replace_"):
            raise ProviderError("MASSIVE_ACCESS_NOT_AUTHORISED")
        if attempts not in (1, 2, 3) or (pilot is not None and attempts != 1):
            raise ProviderError("MASSIVE_RETRY_POLICY_INVALID")
        self.quota, self.transport, self.clock = quota, transport, clock
        self.monotonic = monotonic
        self.attempts, self.pilot, self.heartbeat = attempts, pilot, None
        self._key, self._loader = key, key_loader or load_massive_key
        self.identities = {}
        self.descriptor = self.descriptor.model_copy(
            update={
                "access_reference": access_reference,
                "activation_status": "OWNER_AUTHORISED_NOT_STRATEGY_VALIDATED",
            }
        )

    def set_heartbeat(self, callback):
        self.heartbeat = callback

    def _request(self, path, params, endpoint):
        if self._key is None:
            self._key = self._loader(authorized=True)
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", self._key):
            raise ProviderError("MASSIVE_CREDENTIAL_INVALID_OR_EMPTY")
        try:
            self._no_key({"path": path, "params": params})
            if "cursor" in params:
                self._cursor_privacy(params["cursor"])
        except ValueError:
            raise ProviderError("MASSIVE_SECRET_IN_REQUEST_REFUSED") from None
        for attempt in range(self.attempts):
            try:
                return self._attempt(path, params, endpoint)
            except ProviderError as error:
                if (
                    error.code
                    not in (
                        "MASSIVE_NETWORK_FAILURE",
                        "MASSIVE_RATE_LIMIT",
                        "MASSIVE_SERVER_FAILURE",
                    )
                    or attempt + 1 == self.attempts
                ):
                    raise
                # Account spacing dominates short exponential backoff; Retry-After is durable.
                self.quota.sleep(2**attempt)
        raise AssertionError("Unreachable bounded Massive retry loop")

    def _attempt(self, path, params, endpoint):
        token = self.quota.acquire(endpoint, pilot=self.pilot, heartbeat=self.heartbeat)
        status, count, retry_after = None, None, 0
        try:
            try:
                started = self.monotonic()
                with (
                    transport_privacy(self._key),
                    httpx.Client(
                        timeout=httpx.Timeout(15, connect=5, pool=5, write=5),
                        follow_redirects=False,
                        trust_env=False,
                        transport=self.transport,
                    ) as client,
                ):
                    with client.stream(
                        "GET",
                        ORIGIN + path,
                        params=params,
                        headers={
                            "Authorization": "Bearer " + self._key,
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        self._check_deadline(started)
                        status = response.status_code
                        if status == 429 or (status >= 500 and "Retry-After" in response.headers):
                            retry_after = self._retry_after(response.headers)
                        encoding = response.headers.get("Content-Encoding", "identity").lower()
                        if encoding not in ("", "identity"):
                            raise ProviderError("MASSIVE_COMPRESSED_RESPONSE_REFUSED")
                        length = response.headers.get("Content-Length")
                        if length is not None:
                            try:
                                if not length.isdigit() or int(length) > MAX_RESPONSE_BYTES:
                                    raise ProviderError("MASSIVE_OVERSIZED_RESPONSE")
                            except (ValueError, OverflowError):
                                raise ProviderError("MASSIVE_OVERSIZED_RESPONSE") from None
                        data = bytearray()
                        if response.is_stream_consumed:
                            chunks = iter((response.content,))
                        else:
                            chunks = iter(response.iter_raw())
                        while True:
                            remaining = self._check_deadline(started)
                            timeout = response.request.extensions.get("timeout")
                            if isinstance(timeout, dict):
                                timeout["read"] = max(0.001, min(15, remaining))
                            try:
                                chunk = next(chunks)
                            except StopIteration:
                                break
                            self._check_deadline(started)
                            if self.heartbeat:
                                self.heartbeat()
                            if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise ProviderError("MASSIVE_OVERSIZED_RESPONSE")
                            data.extend(chunk)
                        self._check_deadline(started)
            except httpx.RequestError:
                raise ProviderError("MASSIVE_NETWORK_FAILURE") from None
            if status in (401, 403):
                raise ProviderError("MASSIVE_AUTH_OR_ENTITLEMENT_REJECTED", status=status)
            if status == 429:
                raise ProviderError("MASSIVE_RATE_LIMIT", status=429, retry_after=retry_after)
            if status >= 500:
                raise ProviderError("MASSIVE_SERVER_FAILURE", status=status)
            if status != 200:
                raise ProviderError("MASSIVE_REDIRECT_OR_REQUEST_REJECTED", status=status)
            try:

                def invalid_constant(value):
                    raise ValueError("Non-finite JSON number")

                def unique_object(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError("Duplicate JSON object key")
                        value[key] = item
                    return value

                body = json.loads(
                    data,
                    parse_float=Decimal,
                    parse_constant=invalid_constant,
                    object_pairs_hook=unique_object,
                )
            except (ValueError, UnicodeError):
                raise ProviderError("MASSIVE_INVALID_JSON") from None
            if not isinstance(body, dict) or body.get("status") != "OK":
                raise ProviderError("MASSIVE_RESPONSE_STATUS_REJECTED", status=status)
            results = body.get("results")
            count = len(results) if isinstance(results, list) else int(isinstance(results, dict))
            return body, hashlib.sha256(data).hexdigest()
        finally:
            self.quota.finish(token, status=status, rows=count, retry_after=retry_after)

    def _check_deadline(self, started):
        elapsed = self.monotonic() - started
        if not isinstance(elapsed, (int, float)) or not 0 <= elapsed < REQUEST_DEADLINE_SECONDS:
            raise ProviderError("MASSIVE_NETWORK_FAILURE")
        return REQUEST_DEADLINE_SECONDS - elapsed

    def _retry_after(self, headers):
        """Return a conservative durable delay; None means operator review is required."""
        raw = headers.get("Retry-After", "60").strip()
        if raw.isdigit():
            if len(raw) > 6:
                return None
            value = int(raw)
            return value if 1 <= value <= MAX_RETRY_AFTER_SECONDS else 60 if value <= 0 else None
        try:
            retry_at = parsedate_to_datetime(raw)
        except (ValueError, TypeError, OverflowError):
            return 60
        server_raw = headers.get("Date")
        if server_raw is None:
            client_at = self.clock()
            if retry_at.tzinfo is None or client_at.tzinfo is None:
                return None
            seconds = int((retry_at - client_at).total_seconds()) + 1
            return seconds if 1 <= seconds <= 61 else None
        try:
            server_at = parsedate_to_datetime(server_raw)
        except (ValueError, TypeError, OverflowError):
            return None
        client_at = self.clock()
        if retry_at.tzinfo is None or server_at.tzinfo is None or client_at.tzinfo is None:
            return None
        if abs((server_at - client_at).total_seconds()) > 120:
            return None
        seconds = int((retry_at - server_at).total_seconds()) + 1
        if seconds <= 0:
            return 60
        return seconds if seconds <= MAX_RETRY_AFTER_SECONDS else None

    def identity(self, ticker, day):
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,19}", ticker):
            raise ProviderError("MASSIVE_UNSUPPORTED_TICKER")
        cache = (ticker, day)
        if cache not in self.identities:
            body, _ = self._request(
                "/v3/reference/tickers/" + ticker, {"date": day.isoformat()}, "ticker_metadata"
            )
            result = body.get("results")
            if not isinstance(result, dict):
                raise ProviderError("MASSIVE_METADATA_MISSING")
            fields = (
                "ticker",
                "locale",
                "market",
                "type",
                "active",
                "currency_name",
                "primary_exchange",
                "composite_figi",
            )
            evidence = {k: result.get(k) for k in fields}
            evidence["as_of"] = day.isoformat()
            try:
                if evidence["ticker"] != ticker:
                    raise ValueError
                identity_instrument(evidence)
                self._no_key(evidence)
            except (ValueError, TypeError, KeyError):
                raise ProviderError("MASSIVE_METADATA_IDENTITY_REJECTED") from None
            self.identities[cache] = evidence
        return dict(self.identities[cache])

    def _no_key(self, value):
        if self._key and self._key in canonical(value):
            raise ValueError("Sensitive provider echo refused")

    def _cursor_privacy(self, cursor):
        if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,6000}={0,2}", cursor):
            raise ValueError("Unsupported continuation grammar")
        pending, seen = [cursor.encode("ascii")], set()
        for _ in range(32):
            if not pending:
                return
            payload = pending.pop()
            if payload in seen:
                continue
            seen.add(payload)
            if (self._key and self._key.encode("ascii") in payload) or re.search(
                rb'(?i)(?:api_?key|authorization|api_secret|password)["\s]*[:=]', payload
            ):
                raise ValueError("Sensitive continuation refused")
            decoded = []
            if len(payload) % 2 == 0 and re.fullmatch(rb"[0-9A-Fa-f]+", payload):
                try:
                    decoded.append(bytes.fromhex(payload.decode("ascii")))
                except ValueError:
                    pass
            try:
                decoded.append(
                    base64.b64decode(
                        payload + b"=" * (-len(payload) % 4), altchars=b"-_", validate=True
                    )
                )
            except binascii.Error:
                pass
            if b"%" in payload:
                try:
                    decoded.append(unquote_to_bytes(payload.decode("ascii")))
                except UnicodeError:
                    pass
            pending.extend(
                item for item in decoded if item and item != payload and len(item) <= 6000
            )
        raise ValueError("Ambiguous nested continuation")

    @staticmethod
    def aggregate_path(request):
        mapping = request.instrument.mapping("massive", request.start, request.end)
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,19}", mapping.symbol):
            raise ProviderError("MASSIVE_UNSUPPORTED_TICKER")
        return (
            f"/v2/aggs/ticker/{mapping.symbol}/range/1/minute/"
            f"{milliseconds(request.start)}/{milliseconds(request.end) - 1}"
        )

    def continuation(self, value, path, adjusted, *, persisted=False):
        try:
            if persisted:
                self._cursor_privacy(value)
                return value, {
                    "cursor": value,
                    "adjusted": adjusted,
                    "sort": "asc",
                    "limit": "5000",
                }
            if (
                not isinstance(value, str)
                or len(value) > 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in value)
            ):
                raise ValueError
            self._no_key(value)
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "api.massive.com"
                or parsed.port not in (None, 443)
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.path != path
            ):
                raise ValueError
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            query = dict(pairs)
            if (
                len(query) != len(pairs)
                or not query.get("cursor")
                or set(query) - {"cursor", "adjusted", "sort", "limit"}
                or query.get("adjusted", adjusted) != adjusted
                or query.get("sort", "asc") != "asc"
                or query.get("limit", "5000") != "5000"
            ):
                raise ValueError
            self._cursor_privacy(query["cursor"])
            return query["cursor"], {
                "cursor": query["cursor"],
                "adjusted": adjusted,
                "sort": "asc",
                "limit": "5000",
            }
        except (ValueError, TypeError):
            raise ProviderError("MASSIVE_UNSAFE_CONTINUATION") from None

    def fetch_page(self, request, cursor=None):
        if (
            request.provider != "massive"
            or request.instrument.venue not in self.descriptor.exchanges
            or request.instrument.quote_unit != QuoteUnit.USD
            or request.adjustment not in (Adjustment.RAW, Adjustment.SPLITS)
            or request.assumed_delay_seconds is not None
        ):
            raise ProviderError("MASSIVE_REQUEST_POLICY_REJECTED")
        mapping = request.instrument.mapping("massive", request.start, request.end)
        day = request.start.astimezone(ZoneInfo("America/New_York")).date()
        if (request.end - timedelta(microseconds=1)).astimezone(
            ZoneInfo("America/New_York")
        ).date() != day:
            raise ProviderError("MASSIVE_REQUIRES_SINGLE_LOCAL_DATE")
        path = self.aggregate_path(request)
        adjusted = "true" if request.adjustment == Adjustment.SPLITS else "false"
        params = {"adjusted": adjusted, "sort": "asc", "limit": "5000"}
        if cursor is not None:
            _, params = self.continuation(cursor, path, adjusted, persisted=True)
        identity = self.identity(mapping.symbol, day)
        if identity_instrument(identity) != request.instrument:
            raise ProviderError("MASSIVE_REFERENCE_DOES_NOT_MATCH_REQUEST")
        body, wire_hash = self._request(path, params, "minute_aggregates")
        rows = body.get("results", [] if body.get("resultsCount") == 0 else None)
        evidence = {
            key: body.get(key)
            for key in ("ticker", "adjusted", "resultsCount", "queryCount", "status")
        }
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ProviderError("MASSIVE_RESULTS_MISSING_OR_OVERSIZED")
        values = []
        try:
            from atlas.market_data.quality import bounded_decimal

            previous = None
            for row in rows:
                timestamp = row["t"]
                if (
                    type(timestamp) is not int
                    or timestamp % 60000
                    or not milliseconds(request.start) <= timestamp < milliseconds(request.end)
                    or previous is not None
                    and timestamp <= previous
                    or row.get("otc", False)
                ):
                    raise ValueError
                previous = timestamp
                prices = {}
                for original, name in (
                    ("o", "open"),
                    ("h", "high"),
                    ("l", "low"),
                    ("c", "close"),
                    ("v", "volume"),
                ):
                    value = row[original]
                    if type(value) is not int and not isinstance(value, Decimal):
                        raise ValueError
                    value = Decimal(value)
                    if not bounded_decimal(value) or value < 0 or name != "volume" and value == 0:
                        raise ValueError
                    prices[name] = str(value).upper()
                if Decimal(prices["low"]) > min(
                    Decimal(prices["open"]), Decimal(prices["close"])
                ) or Decimal(prices["high"]) < max(
                    Decimal(prices["open"]), Decimal(prices["close"])
                ):
                    raise ValueError
                values.append(
                    dict(datetime=str(timestamp), **prices, adjustment=request.adjustment.value)
                )
            sanitized = {
                "meta": dict(
                    symbol=mapping.symbol,
                    mic_code=mapping.mic.value,
                    currency="USD",
                    interval="1min",
                    exchange_timezone="America/New_York",
                    massive_identity=identity,
                ),
                "values": values,
                "status": "ok",
                "aggregate_evidence": evidence,
            }
            verify_evidence(request, sanitized)
            if evidence["queryCount"] > 5000:
                raise ValueError
            self._no_key(sanitized)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise ProviderError("MASSIVE_AGGREGATE_CONTRACT_REJECTED") from None
        next_cursor = None
        if body.get("next_url") is not None:
            next_cursor, _ = self.continuation(body["next_url"], path, adjusted)
            if not values:
                raise ProviderError("MASSIVE_EMPTY_CONTINUATION")
        elif evidence["queryCount"] == 5000 and (
            not values or int(values[-1]["datetime"]) < milliseconds(request.end) - 60000
        ):
            raise ProviderError("MASSIVE_TRUNCATED_WITHOUT_CONTINUATION")
        return RawPage(
            body=sanitized,
            fetched_at=self.clock(),
            next_cursor=next_cursor,
            wire_sha256=wire_hash,
            normalization_order="ascending",
            original_availability="unknown",
        )
