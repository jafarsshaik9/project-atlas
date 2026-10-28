"""Explicit historical SIP/IEX bars. No Alpaca account or execution API is available."""

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from atlas.config import AtlasError
from atlas.domain import Currency, QuoteUnit, Venue
from atlas.market_data.alpaca_limits import DEFAULT_QUOTA, AlpacaQuota
from atlas.market_data.contracts import (
    Adjustment,
    HistoricalInstrument,
    Origin,
    ProviderDescriptor,
    RawPage,
    SymbolMapping,
    canonical,
    digest,
)
from atlas.market_data.providers import ProviderError

URL = "https://data.alpaca.markets/v2/stocks/bars"
UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL")
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = ROOT / "local/market-data/alpaca-pages.sqlite3"


def retry_delay(value, now):
    """Never shorten a server delay. Very long delays are persisted for later resume."""
    if value is None:
        return 60.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                raise ValueError
            seconds = max(0, (stamp - now).total_seconds())
        except (ValueError, TypeError, OverflowError):
            raise ProviderError("INVALID_RETRY_AFTER") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise ProviderError("INVALID_RETRY_AFTER")
    return seconds


def load_credentials():
    """Root-relative, lazy credentials; process assignments (even empty) take precedence."""
    names = ("ALPACA_API_KEY", "ALPACA_API_SECRET")
    found = {name: os.environ[name] for name in names if name in os.environ}
    if len(found) < len(names):
        path = ROOT / ".env"
        if path.is_file():
            try:
                if path.stat().st_size > 65536:
                    raise AtlasError("Alpaca credential file exceeds the safe size limit.")
                assignments = {}
                for line in path.read_text(encoding="utf-8-sig").splitlines():
                    name, separator, value = line.partition("=")
                    name = name.strip().removeprefix("export ").strip()
                    if separator and name in names and name not in found:
                        if name in assignments:
                            raise AtlasError("Duplicate Alpaca credential assignment: " + name)
                        value = value.strip()
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                            value = value[1:-1]
                        assignments[name] = value
                found.update(assignments)
            except (OSError, UnicodeError):
                raise AtlasError(
                    "Repository-root Alpaca credential file could not be read."
                ) from None
    missing = [name for name in names if not found.get(name, "").strip()]
    if missing:
        raise AtlasError("Configure nonempty Alpaca credential variables: " + ", ".join(missing))
    return tuple(found[name] for name in names)


def feed_for(provider):
    if provider not in ("alpaca-sip", "alpaca-iex"):
        raise AtlasError("Unknown Alpaca feed provenance.")
    return provider.removeprefix("alpaca-")


def instrument(symbol, start, end, *, feed="sip"):
    feed_for("alpaca-" + feed)
    if symbol not in UNIVERSE:
        raise AtlasError("Unsupported Alpaca research symbol.")
    return HistoricalInstrument(
        instrument_id="ALPACA-" + feed.upper() + "-" + symbol,
        venue=Venue.NASDAQ,
        trading_currency=Currency.USD,
        quote_unit=QuoteUnit.USD,
        instrument_timezone="America/New_York",
        exchange_timezone="America/New_York",
        mappings=(
            SymbolMapping(
                provider="alpaca-" + feed,
                symbol=symbol,
                mic=Venue.NASDAQ,
                currency_code="USD",
                effective_from=start,
                effective_to=end,
                metadata_source="USER_DECLARED_RESEARCH_UNIVERSE_NOT_DATED_REFERENCE",
            ),
        ),
        metadata_evidence=(
            "Declared Nasdaq USD research universe; historical listing identity unverified."
        ),
    )


def parameters(request, symbols, cursor=None):
    feed = feed_for(request.provider)
    if request.adjustment != Adjustment.RAW:
        raise AtlasError("Alpaca imports require distinct feed provenance and raw prices.")
    if request.assumed_delay_seconds is not None:
        raise AtlasError("Collection must preserve unknown historical publication times.")
    if request.end - request.start > timedelta(hours=8):
        raise AtlasError("Alpaca batches must cover a single regular session.")
    result = {
        "symbols": ",".join(sorted(symbols)),
        "timeframe": "1Min",
        "feed": feed,
        "adjustment": "raw",
        "sort": "asc",
        "limit": "10000",
        "currency": "USD",
        "asof": "-",
        "start": request.start.isoformat(),
        "end": (request.end - timedelta(microseconds=1)).isoformat(),
    }
    if cursor is not None:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 8192:
            raise ProviderError("INVALID_PAGINATION")
        result["page_token"] = cursor
    return result


def project(rows):
    return [
        {
            "datetime": row["t"],
            **{
                target: row[source]
                for target, source in (
                    ("open", "o"),
                    ("high", "h"),
                    ("low", "l"),
                    ("close", "c"),
                    ("volume", "v"),
                )
            },
        }
        for row in rows
    ]


def verify_evidence(request, body):
    evidence = body["alpaca_evidence"]
    symbol = request.instrument.mapping(request.provider, request.start, request.end).symbol
    symbols = evidence["parameters"]["symbols"].split(",")
    if (
        symbol not in symbols
        or set(symbols) - set(UNIVERSE)
        or evidence["parameters"] != parameters(request, symbols, evidence["cursor"])
        or evidence["source_checksum"] != digest(evidence["source_rows"])
        or body["values"] != project(evidence["source_rows"])
    ):
        raise ValueError("Alpaca projection or source policy mismatch")


class _PrivateHTTPLogs(logging.Filter):
    def filter(self, record):
        # Do not format a record that could contain auth headers, URLs or payloads.
        record.msg, record.args = "Historical HTTP event suppressed", ()
        record.exc_info, record.exc_text, record.stack_info = None, None, None
        return True


class AlpacaProvider:
    descriptor = ProviderDescriptor(
        provider_id="alpaca-sip",
        origin=Origin.HISTORICAL,
        exchanges=(Venue.NASDAQ,),
        authentication="Two environment credentials; headers only",
        coverage="Historical SIP; Basic excludes latest 15 minutes; access requires pilot",
        pagination="Symbol then timestamp; opaque next_page_token; 10000 total rows maximum",
        rate_limit="Persistent 180 attempts per 61 seconds, 0.35 second completion spacing",
        retry_policy="Three attempts/page maximum; 429/server errors only; pilot cap two",
        licensing="Internal research under account terms; redistribution rights not asserted",
        network_required=True,
        activation_status="HISTORICAL_SIP_ACCESS_NOT_ASSUMED",
        access_reference="https://docs.alpaca.markets/us/reference/stockbars",
    )

    @classmethod
    def descriptor_for(cls, feed):
        feed_for("alpaca-" + feed)
        if feed == "sip":
            return cls.descriptor
        return cls.descriptor.model_copy(
            update={
                "provider_id": "alpaca-iex",
                "coverage": "IEX exchange only; sparse observed bars, not consolidated US coverage",
                "activation_status": "BASIC_IEX_DOCUMENTED_ACCOUNT_ACCESS_REQUIRES_APPROVED_PILOT",
            }
        )

    def __init__(
        self,
        *,
        symbols=UNIVERSE,
        quota=None,
        cache=None,
        allow_network=False,
        credentials=load_credentials,
        transport=None,
        clock=lambda: datetime.now(UTC),
        pilot=None,
        attempts=3,
        feed="sip",
    ):
        self.feed = feed
        self.descriptor = type(self).descriptor_for(feed)
        if not symbols or len(set(symbols)) != len(symbols) or set(symbols) - set(UNIVERSE):
            raise AtlasError("Invalid Alpaca batch universe.")
        if transport is None and not allow_network:
            raise AtlasError("Historical network access requires explicit authorization.")
        if transport is None and quota is not None and quota.path != DEFAULT_QUOTA.resolve():
            raise AtlasError("Real Alpaca traffic requires the shared canonical quota.")
        if not 1 <= attempts <= 3:
            raise AtlasError("Invalid Alpaca attempt bound.")
        self.symbols, self.quota = tuple(sorted(symbols)), quota or AlpacaQuota()
        default_cache = (
            DEFAULT_CACHE if feed == "sip" else ROOT / "local/market-data/alpaca-iex-pages.sqlite3"
        )
        self.cache, self.credentials, self.transport = (
            Path(cache or default_cache).resolve(),
            credentials,
            transport,
        )
        self.clock, self.pilot, self.attempts = clock, pilot, attempts
        self.heartbeat = lambda: None
        self.on_event = lambda event: None
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names and names != {"pages"}:
                raise AtlasError("Foreign Alpaca page-cache schema; migration refused.")
            db.execute(
                "CREATE TABLE IF NOT EXISTS pages("
                "id TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)"
            )

    def connect(self):
        db = sqlite3.connect(self.cache, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def set_heartbeat(self, heartbeat):
        def beat():
            heartbeat()
            self.on_event({"event": "heartbeat"})

        self.heartbeat = beat

    def _request(self, params):
        key, secret = self.credentials()
        if any(not isinstance(v, str) or not v or "\n" in v or "\r" in v for v in (key, secret)):
            raise ProviderError("INVALID_LOCAL_AUTH_CONFIGURATION")
        guards = []
        for name in (
            "httpx",
            "httpcore",
            "httpcore.connection",
            "httpcore.http11",
            "httpcore.http2",
        ):
            logger, guard = logging.getLogger(name), _PrivateHTTPLogs()
            logger.addFilter(guard)
            guards.append((logger, guard))
        try:
            for attempt in range(self.attempts):
                identity = self.quota.acquire(self.pilot, self.heartbeat)
                self.on_event({"event": "http_attempt", "retry": attempt > 0})
                status, backoff, wire = 0, 0, None
                try:
                    with httpx.Client(
                        transport=self.transport,
                        trust_env=False,
                        follow_redirects=False,
                        timeout=httpx.Timeout(20, connect=10),
                    ) as client:
                        with client.stream(
                            "GET",
                            URL,
                            params=params,
                            headers={
                                "APCA-API-KEY-ID": key,
                                "APCA-API-SECRET-KEY": secret,
                                "Accept": "application/json",
                            },
                        ) as response:
                            status = response.status_code
                            if status == 429:
                                # Invalid headers stop this page; preserve a conservative cooldown.
                                backoff = 60
                                backoff = retry_delay(
                                    response.headers.get("Retry-After"), self.clock()
                                )
                            elif status >= 500:
                                backoff = 2**attempt
                            if status == 200:
                                chunks, size, deadline = [], 0, time.monotonic() + 30
                                for chunk in response.iter_bytes():
                                    size += len(chunk)
                                    if size > 8_000_000 or time.monotonic() > deadline:
                                        raise ProviderError("RESPONSE_LIMIT")
                                    chunks.append(chunk)
                                wire = b"".join(chunks)
                except httpx.HTTPError:
                    # Delivery is uncertain: do not repeat a transport failure automatically.
                    raise ProviderError("TRANSPORT_FAILURE") from None
                finally:
                    self.quota.finish(identity, status, backoff=backoff)
                if status == 200:
                    if key.encode() in wire or secret.encode() in wire:
                        raise ProviderError("SENSITIVE_RESPONSE_REJECTED")
                    return wire
                if status in (401, 403):
                    raise ProviderError("AUTHENTICATION_OR_SIP_ACCESS_DENIED", status=status)
                if backoff > 300:
                    raise ProviderError("LONG_RETRY_DELAY_RESUME_LATER", status=status)
                if (status != 429 and status < 500) or attempt + 1 == self.attempts:
                    raise ProviderError("HISTORICAL_HTTP_FAILURE", status=status)
            raise ProviderError("ATTEMPT_LIMIT")
        finally:
            for logger, guard in guards:
                logger.removeFilter(guard)

    def _batch_page(self, request, cursor):
        from atlas.research_collection import exclusive_worker

        params = parameters(request, self.symbols, cursor)
        identity = digest(params)
        with exclusive_worker(self.cache.with_suffix(".lock")):
            with self.connect() as db:
                row = db.execute(
                    "SELECT payload,checksum FROM pages WHERE id=?", (identity,)
                ).fetchone()
            if row:
                if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                    raise AtlasError("Alpaca page-cache checksum mismatch.")
                return json.loads(row[0])
            wire = self._request(params)

            def unique(pairs):
                result = {}
                for name, value in pairs:
                    if name in result:
                        raise ValueError
                    result[name] = value
                return result

            try:
                body = json.loads(
                    wire,
                    parse_float=Decimal,
                    parse_int=Decimal,
                    object_pairs_hook=unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                source = body["bars"]
                if not isinstance(source, dict) or set(source) - set(self.symbols):
                    raise ValueError
                rows, total = {}, 0
                for symbol, values in source.items():
                    if not isinstance(values, list):
                        raise ValueError
                    rows[symbol] = []
                    for value in values:
                        stamp = value["t"]
                        if not isinstance(stamp, str):
                            raise ValueError
                        parsed = datetime.fromisoformat(stamp)
                        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
                            raise ValueError
                        row = {"t": stamp}
                        for name in ("o", "h", "l", "c", "v"):
                            number = value[name]
                            if not isinstance(number, Decimal) or not number.is_finite():
                                raise ValueError
                            if name == "v" and number != number.to_integral_value():
                                raise ValueError
                            row[name] = str(number)
                        rows[symbol].append(row)
                        total += 1
                token = body.get("next_page_token")
                if total > 10000 or (
                    token is not None
                    and (
                        not isinstance(token, str) or not 1 <= len(token) <= 8192 or token == cursor
                    )
                ):
                    raise ValueError
                payload = {
                    "rows": rows,
                    "next_cursor": token,
                    "fetched_at": self.clock().isoformat(),
                    "wire_sha256": hashlib.sha256(wire).hexdigest(),
                }
            except (KeyError, ValueError, TypeError, OverflowError, UnicodeError):
                raise ProviderError("INVALID_HISTORICAL_PAYLOAD") from None
            encoded = canonical(payload)
            with self.connect() as db:
                db.execute(
                    "INSERT INTO pages VALUES(?,?,?)",
                    (identity, encoded, hashlib.sha256(encoded.encode()).hexdigest()),
                )
            self.on_event({"event": "page_committed", "bars": total})
            return payload

    def fetch_page(self, request, cursor):
        if request.provider != self.descriptor.provider_id:
            raise ProviderError("FEED_PROVENANCE_MISMATCH")
        symbol = request.instrument.mapping(request.provider, request.start, request.end).symbol
        if symbol not in self.symbols or request.end > self.clock() - timedelta(minutes=15):
            raise ProviderError("SYMBOL_OR_HISTORICAL_DELAY_GATE")
        payload = self._batch_page(request, cursor)
        rows = payload["rows"].get(symbol, [])
        return RawPage(
            body={
                "meta": {
                    "symbol": symbol,
                    "mic_code": "XNAS",
                    "currency": "USD",
                    "interval": "1min",
                    "exchange_timezone": "America/New_York",
                },
                "status": "ok",
                "values": project(rows),
                "alpaca_evidence": {
                    "parameters": parameters(request, self.symbols, cursor),
                    "cursor": cursor,
                    "source_rows": rows,
                    "source_checksum": digest(rows),
                },
            },
            fetched_at=datetime.fromisoformat(payload["fetched_at"]),
            next_cursor=payload["next_cursor"],
            wire_sha256=payload["wire_sha256"],
            normalization_order="ascending",
            original_availability="unknown",
        )
