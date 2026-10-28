"""Deterministic historical SIP tests: fake credentials and MockTransport only."""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from atlas.alpaca_collection import collect_alpaca, plan_alpaca
from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.historical_backtest import AvailabilityMode, run_historical_backtest
from atlas.market_data.alpaca import AlpacaProvider, instrument, parameters
from atlas.market_data.alpaca_limits import AlpacaQuota
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import Adjustment, DownloadRequest
from atlas.market_data.providers import ProviderError
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore
from atlas.research_history_cli import main
from tests.test_historical_backtest import configuration

DAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def credential_root(tmp_path, monkeypatch, text):
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".env").write_text(text, encoding="utf-8-sig")
    monkeypatch.setattr("atlas.market_data.alpaca.ROOT", root)
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    return root


def test_root_dotenv_credentials_independent_of_working_directory(tmp_path, monkeypatch, caplog):
    from atlas.market_data.alpaca import load_credentials

    root = credential_root(
        tmp_path,
        monkeypatch,
        "ALPACA_API_KEY=\"offline-key\"\nALPACA_API_SECRET='offline-secret'\n"
        "ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2\n",
    )
    original = (root / ".env").read_bytes()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("ALPACA_API_KEY=wrong\nALPACA_API_SECRET=wrong\n")
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("ALPACA_ENDPOINT", "unchanged-account-setting")
    assert load_credentials() == ("offline-key", "offline-secret")
    import os

    assert os.environ["ALPACA_ENDPOINT"] == "unchanged-account-setting"
    assert "ALPACA_API_KEY" not in os.environ
    assert "ALPACA_API_SECRET" not in os.environ
    assert (root / ".env").read_bytes() == original
    assert "offline-key" not in caplog.text and "offline-secret" not in caplog.text


def test_process_credentials_override_root_dotenv(tmp_path, monkeypatch):
    from atlas.market_data.alpaca import load_credentials

    credential_root(
        tmp_path, monkeypatch, "ALPACA_API_KEY=file-key\nALPACA_API_SECRET=file-secret\n"
    )
    monkeypatch.setenv("ALPACA_API_KEY", "process-key")
    assert load_credentials() == ("process-key", "file-secret")
    monkeypatch.setenv("ALPACA_API_SECRET", "process-secret")
    assert load_credentials() == ("process-key", "process-secret")


@pytest.mark.parametrize("name", ["ALPACA_API_KEY", "ALPACA_API_SECRET"])
def test_explicit_empty_process_credential_never_falls_back(tmp_path, monkeypatch, name):
    from atlas.market_data.alpaca import load_credentials

    credential_root(
        tmp_path, monkeypatch, "ALPACA_API_KEY=file-key\nALPACA_API_SECRET=file-secret\n"
    )
    monkeypatch.setenv(name, "")
    with pytest.raises(AtlasError, match=name) as error:
        load_credentials()
    assert "file-key" not in str(error.value) and "file-secret" not in str(error.value)


def test_missing_credentials_report_only_required_names(tmp_path, monkeypatch):
    from atlas.market_data.alpaca import load_credentials

    credential_root(tmp_path, monkeypatch, "# No credentials configured\n")
    with pytest.raises(AtlasError) as error:
        load_credentials()
    assert str(error.value) == (
        "Configure nonempty Alpaca credential variables: ALPACA_API_KEY, ALPACA_API_SECRET"
    )


def test_dotenv_export_and_duplicate_policy(tmp_path, monkeypatch):
    from atlas.market_data.alpaca import load_credentials

    root = credential_root(
        tmp_path,
        monkeypatch,
        "export ALPACA_API_KEY=offline-key\nALPACA_API_SECRET=offline-secret\n",
    )
    assert load_credentials() == ("offline-key", "offline-secret")
    with (root / ".env").open("a") as handle:
        handle.write("ALPACA_API_KEY=duplicate\n")
    with pytest.raises(AtlasError, match="Duplicate Alpaca credential assignment: ALPACA_API_KEY"):
        load_credentials()


class Timer:
    def __init__(self):
        self.value = NOW.timestamp()

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def setup(tmp_path, handler, *, symbols=("AAPL",), pilot=None, attempts=3):
    timer = Timer()
    quota = AlpacaQuota(tmp_path / "quota.sqlite3", clock=timer, sleep=timer.sleep)
    if pilot:
        quota.start_pilot(pilot)
    provider = AlpacaProvider(
        symbols=symbols,
        quota=quota,
        cache=tmp_path / "cache.sqlite3",
        credentials=lambda: ("FAKE-KEY", "FAKE-SECRET"),
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
        pilot=pilot,
        attempts=attempts,
    )
    calendar = HistoricalCalendar(Venue.NASDAQ, DAY, DAY)
    session = calendar.sessions()[0]
    request = DownloadRequest(
        provider="alpaca-sip",
        instrument=instrument("AAPL", session.open, session.close),
        start=session.open,
        end=session.close,
    )
    return provider, request, calendar, quota, timer


def bars(request, *, count=390, offset=0):
    return [
        {
            "t": (request.start + timedelta(minutes=i)).isoformat(),
            "o": 100,
            "h": 101,
            "l": 99,
            "c": 100,
            "v": 1000,
        }
        for i in range(offset, offset + count)
    ]


def test_headers_parameters_and_no_logging(tmp_path, caplog):
    def handler(req):
        assert req.method == "GET"
        assert req.url.host == "data.alpaca.markets"
        assert req.url.path == "/v2/stocks/bars"
        assert req.headers["APCA-API-KEY-ID"] == "FAKE-KEY"
        assert req.headers["APCA-API-SECRET-KEY"] == "FAKE-SECRET"
        assert "FAKE" not in str(req.url)
        assert dict(req.url.params) == parameters(request, ("AAPL",))
        logging.getLogger("httpcore.http11").warning("FAKE-KEY FAKE-SECRET")
        return httpx.Response(200, json={"bars": {"AAPL": bars(request)}})

    provider, request, _, quota, _ = setup(tmp_path, handler)
    with caplog.at_level(logging.DEBUG):
        page = provider.fetch_page(request, None)
    assert "FAKE-KEY" not in caplog.text and "FAKE-SECRET" not in caplog.text
    assert len(page.body["values"]) == 390
    assert page.original_availability == "unknown"
    assert quota.attempt_count() == 1
    assert parameters(request, ("AAPL",))["end"].endswith("59.999999+00:00")


def test_sparse_symbol_pages_and_durable_cache(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.params.get("page_token"))
        if len(calls) == 1:
            return httpx.Response(
                200, json={"bars": {"AAPL": bars(request)}, "next_page_token": "page2"}
            )
        return httpx.Response(200, json={"bars": {"MSFT": bars(request)}})

    provider, request, calendar, quota, _ = setup(tmp_path, handler, symbols=("AAPL", "MSFT"))
    store = HistoricalStore(tmp_path / "history.sqlite3", clock=lambda: NOW)
    plan = plan_alpaca(NOW, first=DAY, last=DAY, symbols=("AAPL", "MSFT"))
    result = collect_alpaca(plan, provider, store)
    assert len(result) == 2 and calls == [None, "page2"]
    assert quota.attempt_count() == 2
    for item in result:
        manifest, quality, data = store.inspect(item["dataset_id"])
        assert manifest["state"] == "ACCEPTED" and len(data) == 390
        assert data[0].provider == "alpaca-sip"
        assert data[0].source_available_at is None
        assert "ALPACA_METADATA_DECLARED" in {f.code for f in quality.findings}
    again = collect_alpaca(plan, provider, store)
    assert all(r["cached"] for r in again) and quota.attempt_count() == 2
    # A new provider instance also uses the durable cache without reading credentials.
    provider.credentials = lambda: pytest.fail("Cached pages must not load credentials")
    assert provider.fetch_page(request, None).next_cursor == "page2"


@pytest.mark.parametrize("status", [401, 403, 400, 302])
def test_permanent_errors_never_retry_or_echo(tmp_path, status):
    provider, request, _, quota, _ = setup(
        tmp_path, lambda _: httpx.Response(status, text="FAKE-KEY FAKE-SECRET")
    )
    with pytest.raises(ProviderError) as error:
        provider.fetch_page(request, None)
    assert "FAKE" not in str(error.value)
    assert error.value.status == status and quota.attempt_count() == 1


@pytest.mark.parametrize("status", [429, 500])
def test_persistent_backoff_and_retries(tmp_path, status):
    calls = []

    def handler(_):
        calls.append(True)
        if len(calls) == 1:
            return httpx.Response(status, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"bars": {"AAPL": bars(request)}})

    provider, request, _, quota, timer = setup(tmp_path, handler)
    provider.fetch_page(request, None)
    assert quota.attempt_count() == 2
    assert timer.value >= NOW.timestamp() + (7 if status == 429 else 1)


def test_pilot_budget_survives_restart_and_counts_retries(tmp_path):
    provider, request, _, quota, timer = setup(
        tmp_path, lambda _: httpx.Response(429), pilot="limited"
    )
    with pytest.raises(AtlasError, match="two-attempt"):
        provider.fetch_page(request, None)
    assert quota.attempt_count("limited") == 2
    reopened = AlpacaQuota(quota.path, clock=timer, sleep=timer.sleep)
    with pytest.raises(AtlasError, match="cannot be reset"):
        reopened.start_pilot("limited")
    with pytest.raises(AtlasError, match="two-attempt"):
        reopened.acquire("limited")
    assert reopened.attempt_count() == 2


def test_quota_shared_rolling_limit_and_uncertain_crash(tmp_path):
    timer = Timer()
    quota = AlpacaQuota(tmp_path / "quota.sqlite3", clock=timer, sleep=timer.sleep)
    for _ in range(180):
        quota.finish(quota.acquire(), 200)
    quota.finish(quota.acquire(), 200)
    with quota.connect() as db:
        stamps = [r[0] for r in db.execute("SELECT started FROM attempts ORDER BY started")]
    assert stamps[180] - stamps[0] >= 61
    pending = quota.acquire()
    reopened = AlpacaQuota(quota.path, clock=timer, sleep=timer.sleep)
    with pytest.raises(AtlasError, match="uncertain"):
        reopened.acquire()
    reopened.finish(pending, 200)
    timer.value -= 1
    with pytest.raises(AtlasError, match="clock regression"):
        reopened.acquire()


def test_crash_after_batch_commit_before_symbol_checkpoint(tmp_path, monkeypatch):
    provider, request, calendar, quota, _ = setup(
        tmp_path, lambda _: httpx.Response(200, json={"bars": {"AAPL": bars(request)}})
    )
    store = HistoricalStore(tmp_path / "history.sqlite3", clock=lambda: NOW)
    original = store.checkpoint
    monkeypatch.setattr(store, "checkpoint", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(AtlasError, match="raw checkpoints retained"):
        download(provider, request, calendar, store)
    monkeypatch.setattr(store, "checkpoint", original)
    identity = download(provider, request, calendar, store)[0]
    assert len(store.inspect(identity)[2]) == 390 and quota.attempt_count() == 1
    assert download(provider, request, calendar, store)[0] == identity


@pytest.mark.parametrize("fault", ["missing", "duplicate", "outside", "bad_ohlc"])
def test_existing_quality_quarantines_without_repair(tmp_path, fault):
    def handler(_):
        rows = bars(request)
        if fault == "missing":
            rows.pop(30)
        elif fault == "duplicate":
            rows.insert(30, rows[30])
        elif fault == "outside":
            rows[0]["t"] = (request.start - timedelta(minutes=1)).isoformat()
        else:
            rows[0]["h"] = 1
        return httpx.Response(200, json={"bars": {"AAPL": rows}})

    provider, request, calendar, _, _ = setup(tmp_path, handler)
    store = HistoricalStore(tmp_path / "history.sqlite3", clock=lambda: NOW)
    identity = download(provider, request, calendar, store)[0]
    assert store.inspect(identity)[0]["state"] == "QUARANTINED"


def test_raw_prices_unknown_actions_and_backtest_compatibility(tmp_path):
    provider, request, calendar, _, _ = setup(
        tmp_path, lambda _: httpx.Response(200, json={"bars": {"AAPL": bars(request)}})
    )
    store = HistoricalStore(tmp_path / "history.sqlite3", clock=lambda: NOW)
    identity = download(provider, request, calendar, store)[0]
    manifest, quality, data = store.inspect(identity)
    assert manifest["action_coverage"] == "UNKNOWN"
    assert all(bar.adjustment == Adjustment.RAW for bar in data)
    session = calendar.sessions()[0]
    strict = run_historical_backtest(
        store, identity, configuration(session, mode=AvailabilityMode.VERIFIED, verified_fx=True)
    )
    assert strict.selected_rows == 0 and not strict.point_in_time_verified
    assumed = run_historical_backtest(store, identity, configuration(session))
    assert assumed.selected_rows == 390 and not assumed.point_in_time_verified
    changed = request.model_copy(update={"adjustment": Adjustment.SPLITS})
    with pytest.raises(AtlasError, match="raw"):
        provider.fetch_page(changed, None)


def test_provenance_tampering_and_cache_corruption_fail_closed(tmp_path):
    from atlas.market_data.quality import assess

    provider, request, calendar, _, _ = setup(
        tmp_path, lambda _: httpx.Response(200, json={"bars": {"AAPL": bars(request)}})
    )
    page = provider.fetch_page(request, None)
    page.body["values"][0]["open"] = "80"
    _, report = assess(request, [page], calendar)
    assert "ALPACA_PROVENANCE_MISMATCH" in {f.code for f in report.findings}
    with provider.connect() as db:
        db.execute("UPDATE pages SET payload='{}'")
    with pytest.raises(AtlasError, match="checksum"):
        provider.fetch_page(request, None)


def test_calendar_manifest_and_default_offline_cli(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        "atlas.market_data.alpaca.load_credentials", lambda: pytest.fail("credentials")
    )
    output = tmp_path / "plan.json"
    assert (
        main(
            [
                "plan",
                "--as-of",
                NOW.isoformat(),
                "--first",
                str(DAY),
                "--last",
                str(DAY),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    plan = json.loads(output.read_text())
    assert plan["provider"] == "alpaca-iex"
    assert plan["request_count_without_retries"] == 1
    assert plan["expected_minute_bars"] == 1950
    assert len(plan["tasks"][0]["checkpoints"]) == 5
    holiday = plan_alpaca(NOW, first=date(2026, 7, 3), last=date(2026, 7, 3))
    assert not holiday["tasks"]
    with pytest.raises(AtlasError):
        plan_alpaca(NOW, first=DAY, last=DAY, stores=["massive.sqlite3"])
    capsys.readouterr()


def test_no_network_gate_and_historical_freshness(tmp_path):
    with pytest.raises(AtlasError, match="authorization"):
        AlpacaProvider(cache=tmp_path / "none.sqlite3")
    provider, request, _, quota, _ = setup(tmp_path, lambda _: pytest.fail("network"))
    provider.clock = lambda: request.end
    with pytest.raises(ProviderError, match="DELAY_GATE"):
        provider.fetch_page(request, None)
    assert quota.attempt_count() == 0


def test_interrupted_pagination_resume_only_fetches_missing_page(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.params.get("page_token"))
        if calls[-1] is None:
            return httpx.Response(
                200,
                json={
                    "bars": {"AAPL": bars(request, count=200)},
                    "next_page_token": "continuation",
                },
            )
        if len(calls) == 2:
            raise httpx.ReadTimeout("FAKE-SECRET")
        return httpx.Response(200, json={"bars": {"AAPL": bars(request, offset=200, count=190)}})

    provider, request, calendar, quota, _ = setup(tmp_path, handler)
    store = HistoricalStore(tmp_path / "history.sqlite3", clock=lambda: NOW)
    with pytest.raises(ProviderError, match="TRANSPORT_FAILURE") as error:
        download(provider, request, calendar, store)
    assert "FAKE" not in str(error.value)
    identity = download(provider, request, calendar, store)[0]
    assert calls == [None, "continuation", "continuation"]
    assert quota.attempt_count() == 3 and len(store.inspect(identity)[2]) == 390


@pytest.mark.parametrize("fault", ["secret", "unexpected_symbol", "fractional_volume", "loop"])
def test_invalid_native_response_is_not_persisted(tmp_path, fault):
    def handler(_):
        body = {"bars": {"AAPL": bars(request)}}
        if fault == "secret":
            body["echo"] = "FAKE-SECRET"
        elif fault == "unexpected_symbol":
            body["bars"]["UNKNOWN"] = []
        elif fault == "fractional_volume":
            body["bars"]["AAPL"][0]["v"] = 1.5
        else:
            body["next_page_token"] = "again"
        return httpx.Response(200, json=body)

    provider, request, _, quota, _ = setup(tmp_path, handler)
    with pytest.raises(ProviderError):
        provider.fetch_page(request, "again" if fault == "loop" else None)
    assert quota.attempt_count() == 1
    with provider.connect() as db:
        assert db.execute("SELECT count(*) FROM pages").fetchone()[0] == 0


@pytest.mark.parametrize("fault", ["pending", "deleted_state", "rewound_clock"])
def test_quota_corruption_is_not_silently_reset(tmp_path, fault):
    quota = AlpacaQuota(tmp_path / "quota.sqlite3")
    quota.finish(quota.acquire(), 200)
    with quota.connect() as db:
        if fault == "pending":
            db.execute("UPDATE state SET pending='missing'")
        elif fault == "deleted_state":
            db.execute("DELETE FROM state")
        else:
            db.execute("UPDATE state SET last=0")
    with pytest.raises(AtlasError, match="inconsistent"):
        AlpacaQuota(quota.path)


def test_provider_mixing_rejected_before_backtesting(monkeypatch):
    from types import SimpleNamespace

    from atlas.multi_session_research import ordered_catalog

    def header(ref):
        return (
            {},
            SimpleNamespace(provider=ref, instrument=SimpleNamespace(instrument_id=ref)),
            SimpleNamespace(local_date=DAY),
        )

    monkeypatch.setattr("atlas.multi_session_research.header", header)
    with pytest.raises(AtlasError, match="mix historical"):
        ordered_catalog(["massive", "alpaca-sip"])
