"""All OANDA responses and credentials here are original offline test fixtures."""

import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import httpx
import pytest

from atlas.config import AtlasError
from atlas.forex import cli
from atlas.forex.access import Permit, Quota, approval_record, phrase, validate_approval
from atlas.forex.accounting import (
    Conversion,
    ForexPortfolio,
    FxCosts,
    FxQuote,
    broker_margin_estimate,
    lot_equivalent,
    pip_value,
)
from atlas.forex.config import OandaConfig, load_config
from atlas.forex.history import ForexStore, windows
from atlas.forex.models import OHLC, CandlePlan, FxInstrument, parse_candles
from atlas.forex.provider import OandaReader, ReadOnlyTransport
from atlas.forex.sessions import LONDON, NEW_YORK, local_instant, overlap

D = Decimal
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
CONFIG = OandaConfig("offline-test-token-not-a-credential", "001-001-1111111-001", "live")


def instrument(name="EUR_USD"):
    return FxInstrument(
        name=name,
        kind="CURRENCY",
        display_precision=3 if name == "USD_JPY" else 5,
        pip_location=-2 if name == "USD_JPY" else -4,
        units_precision=0,
        minimum_units="1",
        maximum_units="1000000",
        margin_rate="0.02",
    )


def plan(**changes):
    values = dict(
        environment="live",
        origin="ARTIFICIAL_OFFLINE_FIXTURE",
        instrument=instrument(),
        start=NOW - timedelta(minutes=4),
        end=NOW,
        as_of=NOW,
        page_minutes=2,
    )
    return CandlePlan(**(values | changes))


def payload(start=None, count=2, *, complete=True):
    start = start or NOW - timedelta(minutes=4)
    return {
        "instrument": "EUR_USD",
        "granularity": "M1",
        "candles": [
            {
                "time": (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "volume": 42,
                "complete": complete,
                "bid": {"o": "1.10001", "h": "1.10008", "l": "1.09999", "c": "1.10003"},
                "ask": {"o": "1.10011", "h": "1.10018", "l": "1.10009", "c": "1.10013"},
                "mid": {"o": "1.10006", "h": "1.10013", "l": "1.10004", "c": "1.10008"},
            }
            for i in range(count)
        ],
    }


class Clock:
    def __init__(self):
        self.now = NOW.timestamp()
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture
def connection(tmp_path):
    opened = []

    def build(handler, *, scope="accounts-instruments", cap=20, config=CONFIG, clock=None):
        clock = clock or Clock()
        quota = Quota(tmp_path / "oanda-quota.sqlite3", clock=clock, sleep=clock.sleep)
        permit = Permit("offline-fixture", config.environment, scope, cap, clock() + 86400)
        transport = ReadOnlyTransport(config, permit, quota, inner=httpx.MockTransport(handler))
        opened.append(transport)
        return OandaReader(transport), transport, quota, permit, clock

    yield build
    for transport in opened:
        transport.close()


def test_scoped_root_environment_loading_and_precedence(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "OANDA_API_TOKEN='offline-test-token-not-a-credential'\n"
        "OANDA_ACCOUNT_ID=001-001-1111111-001\nOANDA_ENVIRONMENT=live\nUNRELATED=value\n"
    )
    monkeypatch.chdir(tmp_path.parent)
    environ = {}
    settings = load_config(tmp_path, environ)
    assert settings == CONFIG and environ == {}
    assert CONFIG.token not in repr(settings) and CONFIG.account_id not in repr(settings)
    assert load_config(tmp_path, {"OANDA_ENVIRONMENT": "practice"}).environment == "practice"
    with pytest.raises(AtlasError, match="OANDA_API_TOKEN"):
        load_config(tmp_path, {"OANDA_API_TOKEN": ""})


@pytest.mark.parametrize("environment", ["", "demo", "LIVE", "https://evil.test"])
def test_environment_has_no_automatic_live_fallback(environment):
    with pytest.raises(AtlasError):
        OandaConfig(CONFIG.token, CONFIG.account_id, environment)


def test_duplicate_dotenv_assignment_rejected(tmp_path):
    (tmp_path / ".env").write_text("OANDA_API_TOKEN=a\nOANDA_API_TOKEN=b\n")
    with pytest.raises(AtlasError, match="Duplicate"):
        load_config(tmp_path, {})


@pytest.mark.parametrize(
    "method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"]
)
def test_mutation_methods_never_reach_transport(connection, method):
    calls = []
    _, transport, quota, permit, _ = connection(lambda r: calls.append(r))
    with pytest.raises(AtlasError, match="NOT_ALLOWLISTED"):
        transport.handle_request(httpx.Request(method, "https://api-fxtrade.oanda.com/v3/accounts"))
    assert calls == [] and quota.count(permit) == 0


@pytest.mark.parametrize(
    "path",
    [
        "/v3/accounts/001-001-1111111-001/orders",
        "/v3/accounts/001-001-1111111-001/orders/7/cancel",
        "/v3/accounts/001-001-1111111-001/positions/EUR_USD/close",
        "/v3/accounts/001-001-1111111-001/summary",
        "/v3/accounts/001-001-1111111-001/transactions",
        "/v3/accounts/001-001-1111111-001",
        "/v3/accounts/999-999-9999999-999/instruments",
        "/v3/%61ccounts",
        "/v3/accounts?extra=true",
        "/v3/accounts/",
    ],
)
def test_forbidden_get_routes_rejected_before_transmission(connection, path):
    calls = []
    _, transport, quota, permit, _ = connection(lambda r: calls.append(r))
    with pytest.raises(AtlasError):
        transport.handle_request(httpx.Request("GET", "https://api-fxtrade.oanda.com" + path))
    assert not calls and quota.count(permit) == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/v3/accounts",
        "http://api-fxtrade.oanda.com/v3/accounts",
        "https://api-fxpractice.oanda.com/v3/accounts",
        "https://api-fxtrade.oanda.com:444/v3/accounts",
        "https://api-fxtrade.oanda.com.evil.test/v3/accounts",
        "https://user@api-fxtrade.oanda.com/v3/accounts",
        "https://api-fxtrade.oanda.com/v3/accounts#fragment",
    ],
)
def test_host_scheme_port_userinfo_fragment_boundaries(connection, url):
    calls = []
    _, transport, quota, permit, _ = connection(lambda r: calls.append(r))
    with pytest.raises(AtlasError):
        transport.handle_request(httpx.Request("GET", url))
    assert not calls and quota.count(permit) == 0


def test_redirect_refused_no_second_request(connection):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.test"})

    reader, _, quota, permit, _ = connection(handler)
    with pytest.raises(AtlasError, match="REDIRECT"):
        reader.accounts()
    assert calls == ["api-fxtrade.oanda.com"] and quota.count(permit) == 1


def test_account_discovery_scrubs_ids_financial_fields_and_debug_logs(connection, caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        assert request.headers["Authorization"] == "Bearer " + CONFIG.token
        logging.getLogger("httpcore.http11").debug("request %s %s", CONFIG.token, CONFIG.account_id)
        return httpx.Response(
            200,
            json={
                "accounts": [{"id": CONFIG.account_id, "balance": "987654", "tags": ["PRIVATE"]}]
            },
        )

    reader, _, _, _, _ = connection(handler)
    result = reader.accounts()
    text = json.dumps(result) + caplog.text
    assert (
        result["configured_account_accessible"]
        and result["account_currency"] == "UNKNOWN_NOT_REQUESTED"
    )
    assert all(
        secret not in text for secret in (CONFIG.token, CONFIG.account_id, "987654", "PRIVATE")
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_error_response_never_exposes_sensitive_payload(connection, status):
    reader, _, quota, permit, _ = connection(
        lambda r: httpx.Response(status, text=CONFIG.token + CONFIG.account_id)
    )
    with pytest.raises(AtlasError) as caught:
        reader.accounts()
    assert CONFIG.token not in str(caught.value) and CONFIG.account_id not in str(caught.value)
    assert quota.count(permit) == 1


@pytest.mark.parametrize("header", ["2", "Wed, 28 Oct 2026 12:00:02 GMT"])
def test_retry_after_numeric_and_http_date(connection, header):
    calls = []

    def handler(request):
        calls.append(1)
        return (
            httpx.Response(429, headers={"Retry-After": header})
            if len(calls) == 1
            else httpx.Response(200, json={"accounts": []})
        )

    reader, _, quota, permit, clock = connection(handler)
    assert reader.accounts()["accounts"] == []
    assert quota.count(permit) == 2 and sum(clock.waits) >= 2


def test_long_backoff_survives_restart(connection, tmp_path):
    reader, _, _, permit, clock = connection(
        lambda r: httpx.Response(429, headers={"Retry-After": "3600"})
    )
    with pytest.raises(AtlasError, match="COOLDOWN"):
        reader.accounts()
    restarted = Quota(tmp_path / "oanda-quota.sqlite3", clock=clock, sleep=clock.sleep)
    with pytest.raises(AtlasError, match="COOLDOWN"):
        restarted.acquire(permit)
    assert restarted.count(permit) == 1


def test_timeout_retries_are_bounded_and_redacted(connection):
    def handler(request):
        raise httpx.ReadTimeout(CONFIG.token + CONFIG.account_id)

    reader, _, quota, permit, _ = connection(handler)
    with pytest.raises(AtlasError, match="TRANSPORT_FAILURE") as caught:
        reader.accounts()
    assert quota.count(permit) == 3 and CONFIG.token not in str(caught.value)


def test_pilot_cap_includes_retries_and_persists(connection, tmp_path):
    reader, _, quota, permit, clock = connection(
        lambda r: httpx.Response(503, headers={"Retry-After": "0"}), cap=2
    )
    with pytest.raises(AtlasError, match="CAP_EXHAUSTED"):
        reader.accounts()
    assert quota.count(permit) == 2
    with pytest.raises(AtlasError, match="CAP_EXHAUSTED"):
        Quota(tmp_path / "oanda-quota.sqlite3", clock=clock).acquire(permit)


def test_uncertain_request_blocks_worker_takeover(connection, tmp_path):
    _, _, quota, permit, clock = connection(lambda r: httpx.Response(200))
    quota.acquire(permit)
    with pytest.raises(AtlasError, match="UNCERTAIN"):
        Quota(tmp_path / "oanda-quota.sqlite3", clock=clock).acquire(permit)


def test_approval_is_scope_environment_account_bound_and_expiring():
    record = approval_record(
        CONFIG, "accounts-instruments", phrase("live", "accounts-instruments"), now=100
    )
    permit = validate_approval(CONFIG, record, now=101)
    assert permit.maximum_attempts == 2
    assert CONFIG.token not in json.dumps(record) and CONFIG.account_id not in json.dumps(record)
    for changed in (record | {"scope": "candles"}, record | {"maximum_attempts": 100}):
        with pytest.raises(AtlasError):
            validate_approval(CONFIG, changed, now=101)
    with pytest.raises(AtlasError):
        validate_approval(OandaConfig(CONFIG.token, "002-002-2222222-002", "live"), record, now=101)
    with pytest.raises(AtlasError):
        validate_approval(CONFIG, record, now=86501)
    with pytest.raises(AtlasError):
        approval_record(CONFIG, "accounts", "yes", now=100)


def test_cli_default_no_network_or_credential_export(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: CONFIG)
    monkeypatch.setattr(
        cli, "ReadOnlyTransport", lambda *a, **k: pytest.fail("Network construction")
    )
    assert cli.main(["discover"]) == 0
    assert cli.main(["discover", "--execute"]) == 1
    text = capsys.readouterr().out
    assert CONFIG.token not in text and CONFIG.account_id not in text


def test_instrument_discovery_is_narrow_and_retains_precision(connection):
    raw = {
        "name": "USD_JPY",
        "type": "CURRENCY",
        "displayPrecision": 3,
        "pipLocation": -2,
        "tradeUnitsPrecision": 0,
        "minimumTradeSize": "1",
        "maximumOrderUnits": "99999",
        "marginRate": "0.04",
        "maximumPositionSize": "0",
    }

    def handler(request):
        assert request.url.params["instruments"] == "EUR_USD,GBP_USD,USD_JPY"
        return httpx.Response(200, json={"instruments": [raw]})

    reader, _, _, _, _ = connection(handler)
    result = reader.instruments()[0]
    assert result.pip == D(".01") and result.base == "USD" and result.quote == "JPY"
    assert result.margin_rate == D(".04") and "maximumPositionSize" in result.provider_metadata


@pytest.mark.parametrize("components", ["MBA", "BA", "M", "B", "A"])
def test_candle_precision_components_source_time_and_unknown_availability(components):
    p = plan(components=components)
    candles = parse_candles(payload(), p, p.start, p.start + timedelta(minutes=2))
    assert candles[0].source_time.endswith(".000000000Z")
    assert candles[0].activity_count == 42 and candles[0].source_available_at is None
    if "B" in components:
        assert candles[0].bid.o == D("1.10001")
    else:
        assert candles[0].bid is None


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "reversed",
        "outside",
        "missing_component",
        "negative",
        "nan",
        "ohlc",
        "symbol",
        "volume",
        "timestamp",
    ],
)
def test_invalid_candle_contracts_fail_closed(defect):
    p, body = plan(), payload()
    if defect == "duplicate":
        body["candles"][1] = body["candles"][0]
    if defect == "reversed":
        body["candles"].reverse()
    if defect == "outside":
        body["candles"][0]["time"] = "2020-01-01T00:00:00Z"
    if defect == "missing_component":
        del body["candles"][0]["bid"]
    if defect == "negative":
        body["candles"][0]["bid"]["o"] = "-1"
    if defect == "nan":
        body["candles"][0]["bid"]["o"] = "NaN"
    if defect == "ohlc":
        body["candles"][0]["bid"]["h"] = "0.5"
    if defect == "symbol":
        body["instrument"] = "GBP_USD"
    if defect == "volume":
        body["candles"][0]["volume"] = -1
    if defect == "timestamp":
        body["candles"][0]["time"] = "2026-10-28T11:56:00.000000001Z"
    with pytest.raises(AtlasError, match="CONTRACT_INVALID"):
        parse_candles(body, p, p.start, p.end)


class FixtureReader:
    def __init__(self, *, missing=False, incomplete=False):
        self.calls = []
        self.missing, self.incomplete = missing, incomplete

    def candles(self, p, start, end):
        self.calls.append((start, end))
        body = payload(
            start, int((end - start).total_seconds() // 60), complete=not self.incomplete
        )
        if self.missing:
            body["candles"] = body["candles"][:1]
        return parse_candles(body, p, start, end)


def test_candle_provider_pagination_and_immutable_reuse(connection, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["price"] == "MBA"
        assert "count" not in request.url.params and request.url.params["smooth"] == "false"
        return httpx.Response(200, json=payload(datetime.fromisoformat(request.url.params["from"])))

    reader, _, quota, permit, _ = connection(handler, scope="candles")
    store = ForexStore(tmp_path / "forex.sqlite3")
    identity = store.collect(plan(), reader)
    manifest, bars = store.inspect(identity)
    assert len(bars) == 4 and manifest["plan"]["provider"] == "oanda-v20"
    assert manifest["plan"]["environment"] == "live" and manifest["unobserved_minutes"] == 0
    assert store.collect(plan(), reader) == identity and quota.count(permit) == 2
    assert calls[1].url.params["from"] == calls[0].url.params["to"]


def test_missing_candles_preserved_without_interpolation(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")
    identity = store.collect(plan(), FixtureReader(missing=True))
    manifest, bars = store.inspect(identity)
    assert len(bars) == 2 and manifest["unobserved_minutes"] == 2
    assert "SCHEDULE_AND_GAPS_UNVERIFIED" in manifest["warnings"]


def test_incomplete_partition_is_quarantined_and_not_accepted(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")
    reader = FixtureReader(incomplete=True)
    with pytest.raises(AtlasError, match="QUARANTINED"):
        store.collect(plan(), reader)
    before = len(reader.calls)
    with pytest.raises(AtlasError, match="QUARANTINED"):
        store.collect(plan(), reader)
    assert len(reader.calls) == before


def test_crash_before_commit_repeats_get_not_dataset(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")
    reader = FixtureReader()

    def crash():
        raise RuntimeError("offline simulated crash")

    with pytest.raises(RuntimeError):
        store.collect(plan(), reader, checkpoint_hook=crash)
    identity = store.collect(plan(), reader)
    assert len(reader.calls) == 3 and len(store.inspect(identity)[1]) == 4
    second = ForexStore(tmp_path / "other.sqlite3")
    assert second.collect(plan(), FixtureReader()) == identity


def test_resume_after_committed_page_skips_accepted_page(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")
    reader = FixtureReader()
    original = reader.candles

    def interrupted(p, start, end):
        if start > p.start:
            raise AtlasError("offline interruption")
        return original(p, start, end)

    reader.candles = interrupted
    with pytest.raises(AtlasError):
        store.collect(plan(), reader)
    resumed = FixtureReader()
    identity = store.collect(plan(), resumed)
    assert len(resumed.calls) == 1 and len(store.inspect(identity)[1]) == 4


def test_dataset_corruption_and_foreign_store_rejected(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")
    identity = store.collect(plan(), FixtureReader())
    with store.connect() as db:
        db.execute("UPDATE fx_pages SET content='[]' WHERE ordinal=0")
    with pytest.raises(AtlasError, match="INTEGRITY"):
        store.inspect(identity)
    with pytest.raises(AtlasError, match="Foreign"):
        Quota(tmp_path / "fx.sqlite3")


def test_bounded_windows_5000_minutes_max():
    p = plan(start=NOW - timedelta(minutes=10001), page_minutes=5000)
    assert [int((b - a).total_seconds() / 60) for a, b in windows(p)] == [5000, 5000, 1]


def conversion(now=NOW, currency="USD", value=".8"):
    return Conversion(currency, D(value), now, now, "ASSUMED")


def quote(now=NOW, bid="1.10", ask="1.1002"):
    return FxQuote("EUR_USD", D(bid), D(ask), now, now)


@pytest.mark.parametrize("side", ["long", "short"])
def test_long_short_accounting_bid_ask_costs_and_reconciliation(side):
    ledger = ForexPortfolio(FxCosts(D(".00001"), D(".0001")))
    stop, target = (D("1.09"), D("1.12")) if side == "long" else (D("1.11"), D("1.08"))
    p = ledger.open(
        "fixture-entry", instrument(), side, D(100), quote(), conversion(), stop, target, NOW
    )
    assert ledger.available_cash < ledger.cash < D(1000)
    assert p.entry == (D("1.10021") if side == "long" else D("1.09999"))
    later = NOW + timedelta(minutes=1)
    net = ledger.close("EUR_USD", quote(later, "1.105", "1.1052"), conversion(later), later)
    assert (net > 0) == (side == "long")
    assert not ledger.positions and ledger.cash == D(1000) + net
    with pytest.raises(AtlasError):
        ledger.open(
            "fixture-entry",
            instrument(),
            side,
            D(100),
            quote(later),
            conversion(later),
            stop,
            target,
            later,
        )


def test_no_leverage_from_broker_margin_rate():
    ledger = ForexPortfolio(FxCosts(D(0), D(0)))
    assert broker_margin_estimate(instrument(), D(1000), D("1.1"), conversion(), NOW) == D("17.6")
    with pytest.raises(AtlasError, match="CASH_OR_RISK"):
        ledger.open(
            "too-big",
            instrument(),
            "long",
            D(2000),
            quote(),
            conversion(),
            D("1.1001"),
            D("1.12"),
            NOW,
        )
    units = ledger.size(instrument(), quote(), conversion(), D("1.09"), NOW)
    assert 0 < units * quote().ask * D(".8") <= 1000


def test_forex_pips_spread_lots_and_conversion():
    assert pip_value(instrument(), D(10000), conversion(), NOW) == D(".8")
    assert pip_value(
        instrument("USD_JPY"), D(10000), conversion(currency="JPY", value=".005"), NOW
    ) == D(".5")
    assert lot_equivalent(D(10000), units_per_lot=D(100000)) == D(".1")
    assert quote().spread == D(".0002")
    with pytest.raises(AtlasError):
        conversion(NOW - timedelta(minutes=6)).value("USD", NOW)
    with pytest.raises(AtlasError):
        conversion().value("JPY", NOW)


def test_midpoint_is_not_an_execution_quote():
    ledger = ForexPortfolio(FxCosts(D(0), D(0)))
    with pytest.raises(AtlasError, match="EXECUTABLE_QUOTE"):
        ledger.open(
            "mid",
            instrument(),
            "long",
            D(1),
            OHLC(o="1", h="1", l="1", c="1"),
            conversion(),
            D(".9"),
            D("1.1"),
            NOW,
        )


def test_overnight_financing_cannot_be_assumed_zero():
    ledger = ForexPortfolio(FxCosts(D(0), D(0)))
    ledger.open(
        "long", instrument(), "long", D(100), quote(), conversion(), D("1.09"), D("1.12"), NOW
    )
    later = NOW + timedelta(days=1)
    with pytest.raises(AtlasError, match="FINANCING_UNSUPPORTED"):
        ledger.close("EUR_USD", quote(later), conversion(later), later)
    net = ledger.close("EUR_USD", quote(later), conversion(later), later, financing_gbp=D(".1"))
    assert net == D("-.116")


def test_sessions_dst_overlap_weekends_and_unknown_hours():
    wide = [(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))]
    london = LONDON.window(date(2026, 3, 16), confirmed_windows=wide)
    ny = NEW_YORK.window(date(2026, 3, 16), confirmed_windows=wide)
    assert london[0].hour == 8 and ny[0].hour == 12
    assert overlap(london, ny) == (ny[0], london[1])
    assert LONDON.window(date(2026, 7, 1), confirmed_windows=wide)[0].hour == 7
    assert LONDON.window(date(2026, 10, 31)) is None
    with pytest.raises(AtlasError, match="unverified"):
        LONDON.window(date(2026, 10, 28))
    for day in (date(2026, 3, 29), date(2026, 10, 25)):
        with pytest.raises(AtlasError):
            local_instant(day, time(1, 30), "Europe/London")


def test_rolling_quota_is_shared_and_conservative(tmp_path):
    clock = Clock()
    first = Quota(tmp_path / "quota.sqlite3", clock=clock, sleep=clock.sleep)
    second = Quota(tmp_path / "quota.sqlite3", clock=clock, sleep=clock.sleep)
    permit = Permit("rolling-fixture", "live", "accounts", 70, clock() + 86400)
    starts = []
    for i in range(61):
        quota = first if i % 2 else second
        attempt = quota.acquire(permit)
        starts.append(clock())
        quota.finish(attempt, 200)
    assert starts[-1] - starts[0] >= 61
    assert all(b - a >= 1 for a, b in zip(starts, starts[1:], strict=False))
    assert second.count(permit) == 61


def test_request_body_and_preexisting_credentials_rejected(connection):
    calls = []
    _, transport, _, _, _ = connection(lambda r: calls.append(r))
    url = "https://api-fxtrade.oanda.com/v3/accounts"
    for request in (
        httpx.Request("GET", url, content=b"body"),
        httpx.Request("GET", url, headers={"Authorization": "unexpected"}),
    ):
        with pytest.raises(AtlasError):
            transport.handle_request(request)
    assert not calls


@pytest.mark.parametrize("injection", ["host", "override", "sni"])
def test_routing_header_and_extension_injection_rejected(connection, injection):
    calls = []
    _, transport, quota, permit, _ = connection(lambda r: calls.append(r))
    request = httpx.Request("GET", "https://api-fxtrade.oanda.com/v3/accounts")
    if injection == "host":
        request.headers["Host"] = "evil.test"
    elif injection == "override":
        request.headers["X-HTTP-Method-Override"] = "POST"
    else:
        request.extensions["sni_hostname"] = "evil.test"
    with pytest.raises(AtlasError):
        transport.handle_request(request)
    assert not calls and quota.count(permit) == 0


def test_practice_is_explicit_and_never_falls_back(connection):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(401)

    config = OandaConfig(CONFIG.token, CONFIG.account_id, "practice")
    reader, _, _, _, _ = connection(handler, config=config)
    with pytest.raises(AtlasError):
        reader.accounts()
    assert hosts == ["api-fxpractice.oanda.com"]


def test_candle_scope_cannot_discover_accounts_and_vice_versa(connection):
    calls = []
    reader, _, _, _, _ = connection(lambda r: calls.append(r), scope="candles")
    with pytest.raises(AtlasError):
        reader.accounts()
    discovery, _, _, _, _ = connection(lambda r: calls.append(r))
    with pytest.raises(AtlasError):
        discovery.candles(plan(), plan().start, plan().end)
    assert not calls


def test_mock_candles_cannot_claim_external_origin(connection):
    reader, _, quota, permit, _ = connection(
        lambda r: pytest.fail("Network called"), scope="candles"
    )
    p = plan(origin="EXTERNAL_HISTORICAL_MARKET_DATA")
    with pytest.raises(AtlasError, match="ORIGIN_MISMATCH"):
        reader.candles(p, p.start, p.end)
    assert quota.count(permit) == 0


def test_invalid_candle_response_quarantines_job(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")

    class InvalidReader:
        def candles(self, *args):
            raise AtlasError("OANDA_CANDLE_CONTRACT_INVALID")

    with pytest.raises(AtlasError, match="QUARANTINED"):
        store.collect(plan(), InvalidReader())
    with pytest.raises(AtlasError, match="QUARANTINED"):
        store.collect(plan(), FixtureReader())


def test_empty_response_page_advances_bounded_window_and_reports_gap(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")

    class EmptyFirst(FixtureReader):
        def candles(self, p, start, end):
            return () if start == p.start else super().candles(p, start, end)

    identity = store.collect(plan(), EmptyFirst())
    manifest, bars = store.inspect(identity)
    assert manifest["unobserved_minutes"] == 2 and len(bars) == 2


def test_store_single_worker_lock_prevents_parallel_page_fetch(tmp_path):
    store = ForexStore(tmp_path / "fx.sqlite3")
    other = ForexStore(tmp_path / "fx.sqlite3")
    reader = FixtureReader()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(Exception, match="locked"):
            other.collect(plan(), reader)
    assert reader.calls == []


def test_non_forex_metadata_supported_but_product_accounting_not_invented():
    metal = FxInstrument(
        name="XAU_USD",
        kind="METAL",
        display_precision=3,
        pip_location=-2,
        units_precision=2,
        minimum_units=".01",
    )
    assert metal.base is None and metal.quote is None
    with pytest.raises(AtlasError, match="unsupported"):
        pip_value(metal, D(1), conversion(), NOW)
    with pytest.raises(ValueError):
        FxInstrument(
            **(instrument().model_dump() | {"provider_metadata": '{"accountID":"private"}'})
        )


def test_prices_do_not_round_or_accept_float_corruption():
    with pytest.raises(ValueError):
        OHLC(o=1.1, h="1.2", l="1.0", c="1.1")
    assert OHLC(o="1.100005", h="1.100005", l="1.100005", c="1.100005").o == D("1.100005")


def test_single_position_funding_restriction_and_future_fx():
    ledger = ForexPortfolio(FxCosts(D(0), D(0)))
    ledger.open(
        "one", instrument(), "long", D(100), quote(), conversion(), D("1.09"), D("1.12"), NOW
    )
    with pytest.raises(AtlasError, match="SINGLE_POSITION"):
        ledger.open(
            "two", instrument(), "long", D(1), quote(), conversion(), D("1.09"), D("1.12"), NOW
        )
    with pytest.raises(AtlasError):
        conversion(NOW + timedelta(seconds=1)).value("USD", NOW)


def test_cli_approval_and_execution_only_mocked(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: CONFIG)
    monkeypatch.setattr("builtins.input", lambda prompt: phrase("live", "accounts-instruments"))
    approval = tmp_path / "local/approval.json"
    assert cli.main(["approve", "--scope", "accounts-instruments", "--output", str(approval)]) == 0
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"accounts": [{"id": CONFIG.account_id}]}
            if len(calls) == 1
            else {"instruments": []},
        )

    original = ReadOnlyTransport
    monkeypatch.setattr(
        cli,
        "ReadOnlyTransport",
        lambda c, p, q: original(c, p, q, inner=httpx.MockTransport(handler)),
    )
    output = tmp_path / "local/evidence.json"
    command = ["discover", "--execute", "--approval", str(approval), "--output", str(output)]
    assert cli.main(command) == 0
    assert len(calls) == 2 and json.loads(output.read_text())["authenticated_attempts"] == 2
    assert cli.main(command) == 1 and len(calls) == 2
    text = capsys.readouterr().out + output.read_text() + approval.read_text()
    assert CONFIG.token not in text and CONFIG.account_id not in text
