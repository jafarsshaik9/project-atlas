from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from atlas.config import AtlasError
from atlas.dashboard_security import COOKIE, DashboardAuth, DashboardSettings
from atlas.domain import Venue
from atlas.history import HistoryStore
from atlas.research import ResearchSettings
from atlas.research_history import recorded_backtest
from atlas.risk import RiskPolicy
from atlas.strategy import ORBConfig
from atlas.synthetic import generate, synthetic_costs
from atlas.web.app import create_app

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "test-only-" + "long-password"


@pytest.fixture
def web(tmp_path):
    store = HistoryStore(tmp_path / "web.sqlite3")
    DashboardAuth(store).set_password(PASSWORD)
    with TestClient(create_app(store), base_url=ORIGIN) as client:
        yield client, store


def login(client):
    response = client.post("/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return response


def seed(store, scenario="orb", venue=Venue.LSE):
    return recorded_backtest(
        generate(scenario, venue),
        ResearchSettings(),
        costs=synthetic_costs(),
        orb=ORBConfig(),
        policy=RiskPolicy(),
        store=store,
    )


@pytest.mark.parametrize(
    "path",
    [
        "portfolio",
        "equity",
        "pnl",
        "drawdown",
        "positions",
        "trades",
        "risk",
        "activity",
        "health",
        "notifications",
        "runs",
    ],
)
def test_all_private_api_views_require_authentication(web, path):
    client, _ = web
    assert client.get("/api/" + path).status_code == 401


def test_login_cookie_headers_logout_and_restart_sessions(web):
    client, store = web
    response = login(client)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "content-security-policy" in client.get("/").headers
    token = client.cookies.get(COOKIE)
    assert DashboardAuth(HistoryStore(store.path)).valid(token)
    with store.connect() as db:
        assert db.execute("SELECT token_hash FROM sessions").fetchone()[0] != token
        assert db.execute("SELECT digest FROM auth").fetchone()[0] != PASSWORD
    assert client.post("/auth/logout", headers={"Origin": ORIGIN}).status_code == 200
    assert client.get("/api/portfolio").status_code == 401


def test_unknown_origin_bad_host_and_invalid_requests_do_not_expose_password(web):
    client, _ = web
    assert (
        client.post(
            "/auth/login", json={"password": PASSWORD}, headers={"Origin": "http://evil.invalid"}
        ).status_code
        == 403
    )
    assert client.get("/login", headers={"Host": "evil.invalid"}).status_code == 400
    invalid = client.post(
        "/auth/login", json={"password": PASSWORD, "extra": "secret"}, headers={"Origin": ORIGIN}
    )
    assert (
        invalid.status_code == 422 and PASSWORD not in invalid.text and "secret" not in invalid.text
    )
    large = client.post("/auth/login", content=b"x" * 9000, headers={"Origin": ORIGIN})
    assert large.status_code == 413


def test_password_failures_are_rate_limited_across_restart(web):
    client, store = web
    for _ in range(5):
        assert (
            client.post(
                "/auth/login", json={"password": "wrong"}, headers={"Origin": ORIGIN}
            ).status_code
            == 401
        )
    assert DashboardAuth(HistoryStore(store.path)).login(PASSWORD, "testclient") is None


def test_empty_dashboard_has_na_values_and_no_invented_performance(web):
    client, _ = web
    login(client)
    response = client.get("/api/portfolio")
    assert response.status_code == 200
    assert response.json()["data"]["equity_gbp"] is None
    assert client.get("/api/equity").json()["data"]["points"] == []
    assert client.get("/api/health").json()["data"]["engine"] == "STOPPED"
    html = client.get("/")
    assert html.status_code == 200 and "SYNTHETIC" in html.text
    assert "No persisted research" in html.text


def test_selected_run_summary_history_risk_and_filters_reconcile(web):
    client, store = web
    report, run_id = seed(store)
    login(client)
    summary = client.get("/api/portfolio").json()
    assert summary["run_id"] == run_id and summary["stale"]
    assert Decimal(summary["data"]["equity_gbp"]) == report.ending_equity_gbp
    assert summary["data"]["completed_trades"] == 1
    assert summary["data"]["open_positions"] == 0
    assert client.get("/api/positions").json()["data"] == []
    trades = client.get("/api/trades", params={"limit": 1, "side": "sell"}).json()["data"]
    assert trades["total"] == 1 and trades["items"][0]["payload"]["side"] == "sell"
    event_id = trades["items"][0]["event_id"]
    assert client.get("/api/trades/" + event_id).json()["event_id"] == event_id
    assert client.get("/api/trades", params={"search": "no-match"}).json()["data"]["total"] == 0
    assert client.get("/api/trades", params={"offset": 2}).json()["data"]["items"] == []
    assert client.get("/api/trades", params={"limit": 101}).status_code == 422
    assert client.get("/api/trades", params={"sort": "sql-injection"}).status_code == 422
    risk = client.get("/api/risk").json()["data"]
    assert Decimal(risk["per_trade_budget_gbp"]) == 5
    assert Decimal(risk["concurrent_limit_gbp"]) == 10
    assert risk["remaining_budget_gbp"] is None
    assert risk["leverage"] == "None" and risk["margin_gbp"] == "0.00"
    chart = client.get("/api/equity").json()["data"]
    assert chart["available_ranges"] == ["all"]
    assert max(Decimal(p["drawdown_gbp"]) for p in chart["points"]) == report.maximum_drawdown_gbp
    for point in chart["points"]:
        assert Decimal(point["equity_gbp"]) - 1000 == Decimal(point["total_pnl_gbp"])


@pytest.mark.parametrize("venue,unit", [(Venue.LSE, "GBX"), (Venue.NASDAQ, "USD")])
def test_native_units_and_gbp_reporting_are_preserved(web, venue, unit):
    client, store = web
    _, run_id = seed(store, venue=venue)
    login(client)
    fills = client.get("/api/trades", params={"run_id": run_id}).json()["data"]["items"]
    assert all(f["payload"]["quote_unit"] == unit for f in fills)
    for fill in fills:
        p = fill["payload"]
        factor = Decimal(100) if unit == "GBX" else Decimal(1)
        assert Decimal(p["gross_gbp"]) == Decimal(p["price"]) / factor * Decimal(
            p["quantity"]
        ) * Decimal(p["fx_gbp_per_unit"])


@pytest.mark.parametrize(
    "settings",
    [
        {"host": "0.0.0.0", "lan": True, "allow_insecure_lan": True},
        {"host": "8.8.8.8", "lan": True, "allow_insecure_lan": True},
        {"host": "192.168.1.20"},
        {"host": "192.168.1.20", "lan": True},
        {"port": 80},
    ],
)
def test_unsafe_network_bindings_are_rejected(settings):
    with pytest.raises(AtlasError):
        DashboardSettings(**settings)


def test_explicit_lan_http_and_tls_modes_require_authentication(tmp_path):
    store = HistoryStore(tmp_path / "lan.sqlite3")
    settings = DashboardSettings(host="192.168.1.20", lan=True, allow_insecure_lan=True)
    with TestClient(create_app(store, settings), base_url="http://192.168.1.20:8000") as client:
        assert client.get("/api/portfolio").status_code == 401
    secure = DashboardSettings(
        host="192.168.1.20", lan=True, certificate="local/cert.pem", private_key="local/key.pem"
    )
    assert secure.secure


def test_no_dashboard_broker_or_execution_mutation_routes(web):
    client, _ = web
    login(client)
    for path in ("/api/orders", "/api/trading/enable", "/api/risk", "/api/research/run"):
        assert client.post(path, json={}, headers={"Origin": ORIGIN}).status_code in (404, 405)
    assert client.get("/local/atlas-history.sqlite3").status_code == 404


def test_interrupted_run_does_not_look_like_a_running_bot(web):
    client, store = web
    from atlas.activity import now_utc

    store.claim_run(
        "a" * 64,
        {"settings": {}, "strategy": "orb-test"},
        "owner",
        now_utc() - timedelta(minutes=1),
    )
    login(client)
    health = client.get("/api/health").json()["data"]
    assert health["engine"] == "ERROR" and health["active_heartbeat"] is None


def test_phone_layout_assets_are_local_and_no_credentials_in_html(web):
    client, _ = web
    login(client)
    html = client.get("/").text
    assert PASSWORD not in html
    assert 'name="viewport"' in html and 'id="sidebar"' in html
    assert "cdn.jsdelivr" not in html and "fonts.googleapis" not in html
    for path in ("atlas.css", "atlas.js", "vendor/chart.umd.js"):
        assert client.get("/static/" + path).status_code == 200
