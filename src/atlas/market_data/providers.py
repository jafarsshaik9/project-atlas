"""Offline fixtures and an explicitly gated, documented historical REST adapter."""

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from atlas.config import AtlasError
from atlas.domain import QuoteUnit, Venue
from atlas.market_data.contracts import (
    DownloadRequest,
    HistoricalInstrument,
    Origin,
    ProviderDescriptor,
    RawPage,
    SymbolMapping,
    canonical,
)


class ProviderError(AtlasError):
    def __init__(self, code, *, status=None, retry_after=None):
        self.code, self.status, self.retry_after = code, status, retry_after
        text = "Historical provider: " + code
        if retry_after is not None:
            text += f"; retry after at least {retry_after} seconds"
        super().__init__(text)


class HistoricalProvider(Protocol):
    descriptor: ProviderDescriptor

    def fetch_page(self, request: DownloadRequest, cursor: str | None) -> RawPage: ...


def fixture_instruments():
    result = []
    for venue, unit in (
        (Venue.LSE, QuoteUnit.GBX),
        (Venue.LSE, QuoteUnit.GBP),
        (Venue.NYSE, QuoteUnit.USD),
        (Venue.NASDAQ, QuoteUnit.USD),
    ):
        zone = "Europe/London" if venue == Venue.LSE else "America/New_York"
        result.append(
            HistoricalInstrument(
                instrument_id=f"FIXTURE-{venue.value}-{unit.value}-001",
                venue=venue,
                trading_currency=unit.currency,
                quote_unit=unit,
                instrument_timezone=zone,
                exchange_timezone=zone,
                metadata_evidence="Original artificial Atlas metadata; not a listing.",
                mappings=(
                    SymbolMapping(
                        provider="fixture-v1",
                        symbol=f"FAKE-{venue.value}-{unit.value}",
                        mic=venue,
                        currency_code=unit.value,
                        effective_from=datetime(2000, 1, 1, tzinfo=UTC),
                        metadata_source="Original artificial fixture specification v1",
                    ),
                ),
            )
        )
    return tuple(result)


class FixtureProvider:
    descriptor = ProviderDescriptor(
        provider_id="fixture-v1",
        origin=Origin.FIXTURE,
        exchanges=tuple(Venue),
        authentication="none",
        coverage="Original generated one-minute fixtures; no actual market observations.",
        pagination="Ascending rows; bounded offset cursor",
        rate_limit="No network requests",
        retry_policy="No network retries",
        licensing="Original artificial Atlas fixtures; no downloaded data",
        network_required=False,
        activation_status="AVAILABLE_OFFLINE",
        generation_parameters=(
            ("generator", "fixture-v1; original artificial Atlas observations"),
            ("seed", "No randomness"),
            ("open", "base + step * (index modulo 20)"),
            ("ohlc", "high=open+2*step; low=open-step; close=open+step"),
            ("volume", "100 + 10 * (index modulo 5)"),
            ("GBX_base_step", "250; 0.1"),
            ("GBP_base_step", "2.5; 0.001"),
            ("USD_base_step", "100; 0.01"),
            ("availability", "Fictional bar end plus 2 seconds; not actual observations"),
            ("arithmetic", "Decimal precision 64, ROUND_HALF_EVEN; isolated local context"),
        ),
    )

    def __init__(self, calendar, *, page_size=4, clock=lambda: datetime.now(UTC)):
        if not 1 <= page_size <= 5000:
            raise ValueError("Page size must be between 1 and 5000.")
        self.calendar, self.page_size, self.clock = calendar, page_size, clock

    def fetch_page(self, request, cursor=None):
        mapping = request.instrument.mapping(
            self.descriptor.provider_id, request.start, request.end
        )
        if cursor is not None and not re.fullmatch(r"\d{1,7}", cursor):
            raise ProviderError("INVALID_CURSOR")
        offset = int(cursor or 0)
        starts = sorted(self.calendar.expected_starts(request.start, request.end))
        if len(starts) > 200000:
            raise ProviderError("ROW_LIMIT")
        rows = []
        unit = request.instrument.quote_unit
        base = (
            Decimal("250")
            if unit == QuoteUnit.GBX
            else Decimal("2.5")
            if unit == QuoteUnit.GBP
            else Decimal("100")
        )
        step = (
            Decimal("0.1")
            if unit == QuoteUnit.GBX
            else Decimal("0.001")
            if unit == QuoteUnit.GBP
            else Decimal("0.01")
        )
        with localcontext(Context(prec=64, rounding=ROUND_HALF_EVEN)):
            for index, start in enumerate(starts):
                value = base + step * (index % 20)
                row = dict(
                    datetime=start.isoformat(),
                    open=str(value),
                    high=str(value + step * 2),
                    low=str(value - step),
                    close=str(value + step),
                    volume=str(100 + index % 5 * 10),
                    complete=True,
                    source_available_at=(start + timedelta(minutes=1, seconds=2)).isoformat(),
                    adjustment=request.adjustment.value,
                )
                rows.append(row)
        scenario = request.fixture_scenario
        if rows:
            if scenario == "missing":
                rows.pop(len(rows) // 2)
            elif scenario == "duplicate":
                rows.insert(1, dict(rows[0]))
            elif scenario == "invalid_ohlc":
                rows[0]["high"] = "0.01"
            elif scenario == "negative_volume":
                rows[0]["volume"] = "-1"
            elif scenario == "nonfinite":
                rows[0]["open"] = "NaN"
            elif scenario == "incomplete":
                rows[-1]["complete"] = False
            elif scenario == "unknown_availability":
                for row in rows:
                    row.pop("source_available_at")
            elif scenario == "out_of_session":
                rows[0]["datetime"] = (starts[0] - timedelta(minutes=1)).isoformat()
            elif scenario == "discontinuity":
                rows[-1].update(open="1000", high="1002", low="999", close="1001")
            elif scenario == "malformed_timestamp":
                rows[0]["datetime"] = "not-a-date"
            elif scenario == "missing_volume":
                rows[0].pop("volume")
            elif scenario == "mixed_adjustment":
                rows[-1]["adjustment"] = "SPLIT_ADJUSTED"
            elif scenario not in ("normal", "wrong_currency", "wrong_mic"):
                raise ProviderError("UNKNOWN_FIXTURE_SCENARIO")
        meta = dict(
            symbol=mapping.symbol,
            mic_code=request.instrument.venue.value,
            currency=unit.value,
            exchange_timezone=request.instrument.exchange_timezone,
            interval="1min",
        )
        if scenario == "wrong_currency":
            meta["currency"] = "USD" if unit != QuoteUnit.USD else "GBP"
        if scenario == "wrong_mic":
            meta["mic_code"] = "UNKNOWN"
        body = {"meta": meta, "values": rows[offset : offset + self.page_size], "status": "ok"}
        return RawPage(
            body=body,
            fetched_at=self.clock(),
            next_cursor=str(offset + self.page_size)
            if offset + self.page_size < len(rows)
            else None,
            wire_sha256=hashlib.sha256(canonical(body).encode()).hexdigest(),
            normalization_order="ascending",
            original_availability="fixture_known",
        )


class _Redact(logging.Filter):
    def __init__(self, key):
        super().__init__()
        self.key = key

    def filter(self, record):
        message = record.getMessage().replace(self.key, "[REDACTED]")
        message = re.sub(r"(?i)(apikey[= :]+)[^\s&'\"]+", r"\1[REDACTED]", message)
        record.msg, record.args = message, ()
        return True


def safe_body(body, key):
    """No raw headers, unknown account fields or echoed keys enter the dataset store."""
    if not isinstance(body, dict):
        return {"invalid_shape": True}
    meta = body.get("meta")
    allowed_meta = (
        {
            k: v
            for k, v in meta.items()
            if k
            in (
                "symbol",
                "mic_code",
                "currency",
                "exchange_timezone",
                "interval",
                "exchange",
                "type",
            )
        }
        if isinstance(meta, dict)
        else None
    )
    values = body.get("values")
    allowed_rows = []
    if isinstance(values, list):
        for row in values[:5001]:
            allowed_rows.append(
                {
                    k: v
                    for k, v in row.items()
                    if k in ("datetime", "open", "high", "low", "close", "volume")
                }
                if isinstance(row, dict)
                else {"invalid_shape": True}
            )
    payload = {
        "meta": allowed_meta,
        "values": allowed_rows if isinstance(values, list) else None,
        "status": body.get("status"),
    }
    # Exact known-key redaction is a documented security transformation, not price correction.
    return json.loads(canonical(payload).replace(key, "[REDACTED]"))


class TwelveDataProvider:
    descriptor = ProviderDescriptor(
        provider_id="twelve-data",
        origin=Origin.HISTORICAL,
        exchanges=(Venue.NYSE, Venue.NASDAQ),
        authentication="Local ATLAS_TWELVEDATA_API_KEY; Authorization header; no key in URL",
        coverage="US minute history requires account verification; LSE excluded.",
        pagination="Descending time_series windows; next end is before earliest returned bar",
        rate_limit="8-second spacing; 800 attempts/day in Atlas store; other clients share quotas",
        retry_policy="3 attempts/page; bounded backoff; permanent auth errors not retried",
        licensing="User-approved account/internal-use rights required; Atlas accepts no licences",
        network_required=True,
        activation_status="BLOCKED_UNTIL_ACCESS_AND_LICENCE_CONFIRMED",
    )

    def __init__(
        self,
        key,
        *,
        allow_network=False,
        access_confirmed=False,
        license_reference="",
        page_size=5000,
        transport=None,
        clock=lambda: datetime.now(UTC),
        sleep=time.sleep,
        monotonic=time.monotonic,
        before_request=None,
        on_backoff=None,
    ):
        if (
            not allow_network
            or not access_confirmed
            or not license_reference.strip()
            or license_reference.startswith("replace_")
            or not key
            or key.startswith("replace_")
        ):
            raise ProviderError("EXTERNAL_ACCESS_NOT_APPROVED")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,200}", key) or not 1 <= page_size <= 5000:
            raise ProviderError("INVALID_LOCAL_PROVIDER_CONFIGURATION")
        self._key = key
        self.descriptor = self.descriptor.model_copy(
            update={
                "access_reference": license_reference,
                "activation_status": "USER_CONFIGURED_ENTITLEMENT_NOT_YET_VERIFIED",
            }
        )
        self.page_size, self.transport, self.clock = page_size, transport, clock
        self.sleep, self.monotonic, self.before_request, self.on_backoff = (
            sleep,
            monotonic,
            before_request,
            on_backoff,
        )
        self._last_request = None
        for name in (
            "httpx",
            "httpcore",
            "httpcore.http11",
            "httpcore.http2",
            "httpcore.connection",
            "httpcore.proxy",
        ):
            logging.getLogger(name).addFilter(_Redact(key))

    def fetch_page(self, request, cursor=None):
        mapping = request.instrument.mapping(
            self.descriptor.provider_id, request.start, request.end
        )
        if request.instrument.venue not in self.descriptor.exchanges:
            raise ProviderError("ONE_MINUTE_EXCHANGE_COVERAGE_NOT_VERIFIED")
        upper = datetime.fromisoformat(cursor) if cursor else request.end
        if (
            upper.tzinfo is None
            or upper.utcoffset() != timedelta(0)
            or not request.start < upper <= request.end
        ):
            raise ProviderError("INVALID_CURSOR")
        params = dict(
            symbol=mapping.symbol,
            mic_code=mapping.mic.value,
            interval="1min",
            timezone="UTC",
            start_date=request.start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=(upper - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
            outputsize=self.page_size,
            adjust="none",
            format="JSON",
        )
        if request.adjustment.value != "UNADJUSTED":
            raise ProviderError("EXTERNAL_ADJUSTMENT_POLICY_UNSUPPORTED")
        for attempt in range(3):
            if self.before_request:
                self.before_request()
            if self._last_request is not None:
                self.sleep(max(0, 8 - (self.monotonic() - self._last_request)))
            self._last_request = self.monotonic()
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(10, connect=5),
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                ) as client:
                    response = client.get(
                        "https://api.twelvedata.com/time_series",
                        params=params,
                        headers={"Authorization": "apikey " + self._key},
                    )
            except httpx.RequestError:
                if attempt == 2:
                    raise ProviderError("NETWORK_FAILURE") from None
                self.sleep(2**attempt)
                continue
            if response.status_code in (401, 403):
                raise ProviderError("AUTH_OR_ENTITLEMENT_REJECTED", status=response.status_code)
            try:
                body = response.json()
            except ValueError:
                if response.status_code < 500:
                    raise ProviderError("INVALID_JSON") from None
                body = {}
            code = body.get("code") if isinstance(body, dict) else None
            if response.status_code == 429 or code == 429:
                raw = response.headers.get("Retry-After", "60")
                try:
                    delay = (
                        int(raw)
                        if raw.isdigit()
                        else int((parsedate_to_datetime(raw) - self.clock()).total_seconds()) + 1
                    )
                except (ValueError, TypeError, OverflowError):
                    delay = 60
                delay = max(1, delay)
                if self.on_backoff:
                    self.on_backoff(delay)
                if delay > 30 or attempt == 2:
                    raise ProviderError("RATE_LIMIT", status=429, retry_after=delay)
                self.sleep(delay)
                continue
            if response.status_code >= 500:
                if attempt == 2:
                    raise ProviderError("PROVIDER_UNAVAILABLE", status=response.status_code)
                self.sleep(2**attempt)
                continue
            if code in (401, 403):
                raise ProviderError("AUTH_OR_ENTITLEMENT_REJECTED", status=code)
            if (
                response.status_code != 200
                or isinstance(body, dict)
                and body.get("status") == "error"
            ):
                raise ProviderError("REQUEST_REJECTED", status=response.status_code)
            if len(response.content) > 8_000_000 or (
                isinstance(body, dict)
                and isinstance(body.get("values"), list)
                and len(body["values"]) > self.page_size
            ):
                raise ProviderError("OVERSIZED_PAGE")
            sanitized = safe_body(body, self._key)
            values = sanitized.get("values")
            next_cursor = None
            if isinstance(values, list) and len(values) == self.page_size:
                try:
                    earliest = min(
                        datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                        for v in values
                    )
                except (KeyError, TypeError, ValueError):
                    raise ProviderError("MALFORMED_PAGINATION_TIMESTAMP") from None
                if not request.start <= earliest < upper:
                    raise ProviderError("PAGINATION_NOT_PROGRESSING")
                if earliest > request.start:
                    next_cursor = earliest.isoformat()
            return RawPage(
                body=sanitized,
                fetched_at=self.clock(),
                next_cursor=next_cursor,
                wire_sha256=hashlib.sha256(response.content).hexdigest(),
                http_status=response.status_code,
                normalization_order="descending",
                original_availability="unknown",
            )
        raise AssertionError("Unreachable bounded provider retry state")
