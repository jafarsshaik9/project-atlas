import base64
import json
from decimal import Decimal

import httpx
import pytest

from atlas.broker import Trading212Client, _retry_delay
from atlas.config import AtlasError, Settings
from atlas.main import main

KEY = "dummy-key"
SECRET = "dummy-secret"
PAYLOAD = {
    "id": 12345,
    "currency": "GBP",
    "totalValue": 500.25,
    "cash": {"availableToTrade": 490.25, "inPies": 0, "reservedForOrders": 0},
    "investments": {
        "currentValue": 10,
        "totalCost": 9,
        "realizedProfitLoss": 0,
        "unrealizedProfitLoss": 1,
    },
}


def make_client(handler):
    waits = []
    client = Trading212Client(
        Settings(api_key=KEY, api_secret=SECRET),
        transport=httpx.MockTransport(handler),
        sleep=waits.append,
    )
    return client, waits


def test_summary_uses_only_demo_get_basic_auth():
    def handler(request):
        assert str(request.url) == "https://demo.trading212.com/api/v0/equity/account/summary"
        assert request.method == "GET"
        expected = base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        assert request.extensions["timeout"]["connect"] == 5
        return httpx.Response(200, json=PAYLOAD)

    client, _ = make_client(handler)
    summary = client.account_summary()
    assert summary.total_value == Decimal("500.25")
    assert summary.available_cash == Decimal("490.25")
    assert summary.currency == "GBP"
    assert "12345" not in repr(summary)


@pytest.mark.parametrize("status", [401, 403, 400, 302])
def test_failures_do_not_leak_payload_or_follow_redirect(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text=f"{KEY}:{SECRET}",
            headers={"Location": "https://example.com/steal"},
        )

    client, _ = make_client(handler)
    with pytest.raises(AtlasError) as error:
        client.account_summary()
    assert str(status) in str(error.value)
    assert KEY not in str(error.value)
    assert SECRET not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_transient_failures_have_bounded_retries(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "6"})

    client, waits = make_client(handler)
    with pytest.raises(AtlasError, match="after 3 attempts"):
        client.account_summary()
    assert len(calls) == 3
    assert waits.count(6) == 2


def test_transient_failure_then_success():
    responses = iter([httpx.Response(503), httpx.Response(200, json=PAYLOAD)])
    client, _ = make_client(lambda _: next(responses))
    assert client.account_summary().total_value == Decimal("500.25")


def test_network_failure_is_sanitised_and_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError(SECRET, request=request)

    client, _ = make_client(handler)
    with pytest.raises(AtlasError) as error:
        client.account_summary()
    assert SECRET not in str(error.value)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {**PAYLOAD, "currency": "dummy-secret"},
        {**PAYLOAD, "totalValue": True},
        {**PAYLOAD, "totalValue": "500"},
        {**PAYLOAD, "cash": None},
        {**PAYLOAD, "totalValue": float("inf")},
    ],
)
def test_invalid_schema(payload):
    client, _ = make_client(lambda _: httpx.Response(200, content=json.dumps(payload)))
    with pytest.raises(AtlasError, match="schema"):
        client.account_summary()


def test_invalid_json():
    client, _ = make_client(lambda _: httpx.Response(200, content=SECRET))
    with pytest.raises(AtlasError, match="invalid JSON"):
        client.account_summary()


def test_delay_headers():
    assert _retry_delay(httpx.Headers({"x-ratelimit-reset": "110"}), 100) == 10
    assert _retry_delay(httpx.Headers({"Retry-After": "Thu, 01 Jan 1970 00:01:50 GMT"}), 100) == 10
    assert _retry_delay(httpx.Headers({"Retry-After": "invalid"}), 100) == 5
    with pytest.raises(AtlasError, match="longer wait"):
        _retry_delay(httpx.Headers({"Retry-After": "300"}), 100)


def test_long_broker_wait_stops_without_early_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "300"})

    client, _ = make_client(handler)
    with pytest.raises(AtlasError, match="longer wait"):
        client.account_summary()
    assert len(calls) == 1


def test_requests_are_spaced_at_least_five_seconds():
    clock = [0.0]
    request_times = []
    attempts = iter([503, 503, 200])

    def sleep(seconds):
        clock[0] += seconds

    def handler(request):
        request_times.append(clock[0])
        return httpx.Response(next(attempts), json=PAYLOAD)

    client = Trading212Client(
        Settings(api_key=KEY, api_secret=SECRET),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    client.account_summary()
    assert request_times == [0, 5, 10]


def test_live_rejected():
    with pytest.raises(AtlasError, match="must be demo"):
        Settings(environment="live")


@pytest.mark.parametrize("key,secret", [("", ""), ("replace_with_key", "secret"), ("a:b", "s")])
def test_invalid_credentials(key, secret):
    with pytest.raises(AtlasError):
        Trading212Client(Settings(api_key=key, api_secret=secret))


def test_settings_repr_hides_secrets():
    settings = Settings(api_key=KEY, api_secret=SECRET)
    assert KEY not in repr(settings)
    assert SECRET not in repr(settings)


@pytest.mark.parametrize(
    "key,secret,message",
    [
        ("key:secret", SECRET, "API Key contains a colon"),
        ("dummy-key\n", SECRET, "API Key contains whitespace"),
        (KEY, "dummy-secret\t", "API Secret contains whitespace"),
        (KEY, "dummy-secret\u200b", "API Secret contains a hidden or non-ASCII"),
        ('"dummy-key"', SECRET, "API Key appears to include quotes"),
        (KEY, "'dummy-secret'", "API Secret appears to include quotes"),
        ("\x16dummy-key", SECRET, "API Key contains a Ctrl\\+V control character"),
        (KEY, "dummy-secret\x16", "API Secret contains a Ctrl\\+V control character"),
    ],
)
def test_credential_errors_identify_field_without_exposing_values(key, secret, message):
    with pytest.raises(AtlasError, match=message) as error:
        Settings(api_key=key, api_secret=secret).require_credentials()
    assert key not in str(error.value)
    assert secret not in str(error.value)


def test_colon_in_secret_is_valid_for_basic_auth():
    Settings(api_key=KEY, api_secret="dummy:secret").require_credentials()


def test_health_is_offline(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "demo")
    monkeypatch.setenv("TRADING212_API_KEY", KEY)
    monkeypatch.setenv("TRADING212_API_SECRET", SECRET)

    def forbidden(*args, **kwargs):
        pytest.fail("health contacted broker")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert main(["health"]) == 0
    output = capsys.readouterr().out
    assert "Broker not contacted" in output
    assert KEY not in output and SECRET not in output


def test_broker_check_missing_credentials(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "demo")
    monkeypatch.delenv("TRADING212_API_KEY", raising=False)
    monkeypatch.delenv("TRADING212_API_SECRET", raising=False)
    assert main(["broker-check"]) == 1
    assert "secure prompt" in capsys.readouterr().err


def test_cli_live_environment_fails_before_network(monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_ENV", "live")

    def forbidden(*args, **kwargs):
        pytest.fail("Live configuration reached the network")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert main(["broker-check"]) == 1
    assert "must be demo" in capsys.readouterr().err


def test_cli_success_prints_only_minimal_summary(monkeypatch, capsys):
    monkeypatch.delenv("ATLAS_ENV", raising=False)
    monkeypatch.setenv("TRADING212_API_KEY", KEY)
    monkeypatch.setenv("TRADING212_API_SECRET", SECRET)

    def send(client, request, **kwargs):
        assert request.url.host == "demo.trading212.com"
        return httpx.Response(200, json=PAYLOAD, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    assert main(["broker-check"]) == 0
    output = capsys.readouterr().out
    assert output == (
        "Demo connection OK; currency: GBP\nTotal value: 500.25; available cash: 490.25\n"
    )
    assert KEY not in output and SECRET not in output and "12345" not in output
