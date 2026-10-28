"""Closed GET-only OANDA capability. No SDK, broker methods, redirects or proxy env."""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from atlas.config import AtlasError
from atlas.forex.config import SYMBOLS
from atlas.forex.models import FxInstrument, parse_candles
from atlas.market_data.alpaca import retry_delay
from atlas.market_data.contracts import canonical


class Redactor(logging.Filter):
    def __init__(self, config):
        super().__init__()
        self.secrets = (config.token, config.account_id)

    def filter(self, record):
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "[REDACTED]")
        message = re.sub(r"\b\d{3}-\d{3}-\d+-\d{3}\b", "[REDACTED_ACCOUNT]", message)
        record.msg, record.args = message, ()
        record.exc_info, record.exc_text, record.stack_info = None, None, None
        return True


class ReadOnlyTransport(httpx.BaseTransport):
    def __init__(self, config, permit, quota, *, inner=None):
        if config.environment != permit.environment or permit.scope not in (
            "accounts",
            "accounts-instruments",
            "candles",
        ):
            raise AtlasError("OANDA_SCOPE_OR_ENVIRONMENT_MISMATCH")
        self._config, self._permit, self._quota = config, permit, quota
        self._inner = (
            inner if inner is not None else httpx.HTTPTransport(retries=0, trust_env=False)
        )
        self.redactor = Redactor(config)
        self.loggers = [
            logging.getLogger(n)
            for n in (
                "httpx",
                "httpcore.connection",
                "httpcore.http11",
                "httpcore.http2",
                "httpcore.proxy",
            )
        ]
        for logger in self.loggers:
            logger.addFilter(self.redactor)

    def validate(self, request):
        url = request.url
        if (
            request.method != "GET"
            or request.content
            or url.scheme != "https"
            or url.host != self._config.host
            or url.port not in (None, 443)
            or url.userinfo
            or url.fragment
            or b"%" in url.raw_path.split(b"?")[0]
            or "authorization" in request.headers
            or "proxy-authorization" in request.headers
            or request.headers.get("host") != self._config.host
            or set(request.headers) - {"host"}
            or request.extensions
        ):
            raise AtlasError("OANDA_REQUEST_NOT_ALLOWLISTED")
        pairs = list(url.params.multi_items())
        params = dict(pairs)
        if len(pairs) != len(params):
            raise AtlasError("OANDA_QUERY_NOT_ALLOWLISTED")
        if (
            self._permit.allowed_requests is not None
            and (url.path, params.get("from", ""), params.get("to", ""), params.get("price", ""))
            not in self._permit.allowed_requests
        ):
            raise AtlasError("OANDA_REQUEST_OUTSIDE_APPROVED_PLAN")
        if (
            url.path == "/v3/accounts"
            and self._permit.scope in ("accounts", "accounts-instruments")
            and not params
        ):
            return
        if (
            url.path == f"/v3/accounts/{self._config.account_id}/instruments"
            and self._permit.scope == "accounts-instruments"
        ):
            if set(params) == {"instruments"} and params["instruments"] == ",".join(SYMBOLS):
                return
        if self._permit.scope == "candles" and url.path in {
            f"/v3/instruments/{s}/candles" for s in SYMBOLS
        }:
            try:
                start, end = (
                    datetime.fromisoformat(params["from"]),
                    datetime.fromisoformat(params["to"]),
                )
                if (
                    set(params) == {"from", "to", "price", "granularity", "smooth", "includeFirst"}
                    and params["granularity"] == "M1"
                    and params["price"] in ("MBA", "BA", "M", "B", "A")
                    and params["smooth"] == "false"
                    and params["includeFirst"] == "true"
                    and start.utcoffset() == end.utcoffset() == timedelta(0)
                    and not any(t.second or t.microsecond for t in (start, end))
                    and timedelta(0) < end - start <= timedelta(minutes=5000)
                ):
                    return
            except (KeyError, ValueError, TypeError):
                pass
        raise AtlasError("OANDA_PATH_OR_QUERY_NOT_ALLOWLISTED")

    def handle_request(self, request):
        self.validate(request)  # Before token attachment, attempt reservation or network.
        identity = self._quota.acquire(self._permit)
        request.headers["Authorization"] = "Bearer " + self._config.token
        request.headers["Accept-Datetime-Format"] = "RFC3339"
        request.extensions["timeout"] = {k: 10.0 for k in ("connect", "read", "write", "pool")}
        status, backoff, response = 0, 0, None
        try:
            response = self._inner.handle_request(request)
            status = response.status_code
            if 300 <= status < 400:
                raise AtlasError("OANDA_REDIRECT_REFUSED")
            if status == 429 or status >= 500:
                backoff = 60.0
                backoff = retry_delay(
                    response.headers.get("Retry-After"),
                    datetime.fromtimestamp(self._quota.clock(), tz=UTC),
                )
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 8_000_000:
                    raise AtlasError("OANDA_RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            # Never expose response cookies, headers, raw error text or credentialed request.
            return httpx.Response(status, content=b"".join(chunks))
        except AtlasError:
            raise
        except Exception:
            raise AtlasError("OANDA_TRANSPORT_FAILURE") from None
        finally:
            request.headers.pop("Authorization", None)
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass  # Never surface a credentialed upstream cleanup exception.
            self._quota.finish(identity, status, backoff)

    def close(self):
        try:
            self._inner.close()
        except Exception:
            raise AtlasError("OANDA_TRANSPORT_CLOSE_FAILED") from None
        finally:
            for logger in self.loggers:
                logger.removeFilter(self.redactor)


class OandaReader:
    """Only three fixed GET operations; discovery never returns raw account payloads."""

    def __init__(self, transport):
        self._transport = transport

    def _get(self, path, params=None):
        for attempt in range(3):
            request = httpx.Request(
                "GET", f"https://{self._transport._config.host}{path}", params=params
            )
            try:
                response = self._transport.handle_request(request)
            except AtlasError as exc:
                if str(exc) == "OANDA_TRANSPORT_FAILURE" and attempt < 2:
                    continue
                raise
            if response.status_code == 200:
                try:
                    value = json.loads(response.content)
                    if not isinstance(value, dict):
                        raise ValueError
                    return value
                except (ValueError, TypeError):
                    raise AtlasError("OANDA_INVALID_JSON") from None
            if response.status_code in (401, 403):
                raise AtlasError(f"OANDA_ACCESS_DENIED_HTTP_{response.status_code}")
            if response.status_code != 429 and response.status_code < 500:
                raise AtlasError(f"OANDA_HTTP_{response.status_code}")
            if attempt == 2:
                raise AtlasError("OANDA_RETRY_BUDGET_EXHAUSTED")
        raise AtlasError("OANDA_REQUEST_FAILED")

    def accounts(self):
        body = self._get("/v3/accounts")
        try:
            rows = body["accounts"]
            if not isinstance(rows, list) or any(not isinstance(r.get("id"), str) for r in rows):
                raise ValueError
            ids = [r["id"] for r in rows]
            if len(ids) != len(set(ids)):
                raise ValueError
            return {
                "environment": self._transport._config.environment,
                "accounts": [
                    {
                        "label": f"account-{i + 1}",
                        "configured": r == self._transport._config.account_id,
                    }
                    for i, r in enumerate(ids)
                ],
                "configured_account_accessible": self._transport._config.account_id in ids,
                "account_currency": "UNKNOWN_NOT_REQUESTED",
                "mt4_mt5_spreadbet_capabilities": "UNKNOWN",
                "broker_execution": "UNSUPPORTED",
            }
        except (KeyError, ValueError, TypeError, AttributeError):
            raise AtlasError("OANDA_ACCOUNT_RESPONSE_INVALID") from None

    def instruments(self):
        body = self._get(
            f"/v3/accounts/{self._transport._config.account_id}/instruments",
            {"instruments": ",".join(SYMBOLS)},
        )
        try:
            result = []
            for raw in body["instruments"]:
                if raw["name"] not in SYMBOLS or raw["type"] != "CURRENCY":
                    raise ValueError
                # Preserve documented numeric restrictions without aliases/account fields.
                extras = {}
                for key in (
                    "maximumPositionSize",
                    "minimumTrailingStopDistance",
                    "maximumTrailingStopDistance",
                    "minimumGuaranteedStopLossDistance",
                    "guaranteedStopLossOrderExecutionPremium",
                ):
                    if key in raw:
                        value = Decimal(raw[key])
                        if not value.is_finite() or value < 0:
                            raise ValueError
                        extras[key] = str(value)
                result.append(
                    FxInstrument(
                        name=raw["name"],
                        kind=raw["type"],
                        display_precision=raw["displayPrecision"],
                        pip_location=raw["pipLocation"],
                        units_precision=raw["tradeUnitsPrecision"],
                        minimum_units=raw["minimumTradeSize"],
                        maximum_units=raw.get("maximumOrderUnits"),
                        margin_rate=raw.get("marginRate"),
                        provider_metadata=canonical(extras),
                    )
                )
            if len({i.name for i in result}) != len(result):
                raise ValueError
            return tuple(result)
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise AtlasError("OANDA_INSTRUMENT_RESPONSE_INVALID") from None

    def candles(self, plan, start, end):
        if plan.environment != self._transport._config.environment:
            raise AtlasError("OANDA_DATA_ENVIRONMENT_MISMATCH")
        fixture = isinstance(self._transport._inner, httpx.MockTransport)
        if fixture != (plan.origin == "ARTIFICIAL_OFFLINE_FIXTURE"):
            raise AtlasError("OANDA_DATA_ORIGIN_MISMATCH")
        body = self._get(
            f"/v3/instruments/{plan.instrument.name}/candles",
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "granularity": "M1",
                "price": plan.components,
                "smooth": "false",
                "includeFirst": "true",
            },
        )
        return parse_candles(body, plan, start, end)
