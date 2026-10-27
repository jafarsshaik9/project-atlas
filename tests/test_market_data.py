"""Original artificial historical fixtures; every external response is mocked."""

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from atlas.config import AtlasError
from atlas.domain import Currency, QuoteUnit, Venue
from atlas.main import main
from atlas.market_data.calendars import HistoricalCalendar, RecordedCalendar
from atlas.market_data.contracts import (
    Availability,
    CorporateAction,
    DownloadRequest,
    HistoricalBar,
    RawPage,
    digest,
)
from atlas.market_data.providers import (
    FixtureProvider,
    ProviderError,
    TwelveDataProvider,
    fixture_instruments,
    safe_body,
)
from atlas.market_data.quality import assess
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore, JobOwnershipLost

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


@pytest.fixture
def historical(tmp_path):
    cal = HistoricalCalendar(Venue.LSE, date(2024, 10, 28), date(2024, 10, 28))
    session = cal.session(Venue.LSE, date(2024, 10, 28))
    request = DownloadRequest(
        provider="fixture-v1",
        instrument=fixture_instruments()[0],
        start=session.open,
        end=session.open + timedelta(minutes=10),
    )
    return (
        request,
        cal,
        FixtureProvider(cal, clock=lambda: NOW),
        HistoricalStore(tmp_path / "md.sqlite3", clock=lambda: NOW),
    )


def changed(model, **values):
    return type(model).model_validate({**model.model_dump(), **values})


def all_pages(provider, request):
    pages, cursor = [], None
    while True:
        page = provider.fetch_page(request, cursor)
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            return tuple(pages)


def test_complete_offline_roundtrip_restart_and_idempotency(historical):
    request, cal, provider, store = historical
    identifier, job, cached = download(provider, request, cal, store)
    assert not cached and store.job(job)["status"] == "COMPLETED"
    reopened = HistoricalStore(store.path, clock=lambda: NOW + timedelta(days=1))
    manifest, quality, bars = reopened.inspect(identifier)
    assert manifest["origin"] == "ARTIFICIAL_OFFLINE_FIXTURE"
    assert dict(manifest["provider_descriptor"]["generation_parameters"])["seed"] == "No randomness"
    assert manifest["execution"] == "NONE" and manifest["row_count"] == 10
    assert quality.status == "WARNING"  # No survivorship/action completeness claims.
    assert bars[0].open == Decimal("250")
    assert reopened.validate(identifier) == quality
    assert download(provider, request, cal, reopened) == (identifier, job, True)
    provider.clock = lambda: NOW + timedelta(days=1)
    assert download(provider, request, cal, reopened, refresh=True)[0] == identifier
    assert reopened.inspect(identifier)[2][0].ingested_at == NOW
    assert reopened.coverage()[1] == 1


@pytest.mark.parametrize("index", range(4))
def test_all_markets_and_explicit_units(index, tmp_path):
    instrument = fixture_instruments()[index]
    cal = HistoricalCalendar(instrument.venue, date(2024, 10, 28), date(2024, 10, 28))
    session = cal.sessions()[0]
    request = DownloadRequest(
        provider="fixture-v1",
        instrument=instrument,
        start=session.open,
        end=session.open + timedelta(minutes=3),
    )
    store = HistoricalStore(tmp_path / "md.sqlite3", clock=lambda: NOW)
    identifier, _, _ = download(FixtureProvider(cal, clock=lambda: NOW), request, cal, store)
    bar = store.inspect(identifier)[2][0]
    assert bar.unit == instrument.quote_unit and bar.currency == instrument.trading_currency
    if instrument.quote_unit == QuoteUnit.GBX:
        assert instrument.quote_unit.major(bar.open) == Decimal("2.50")
    if instrument.quote_unit == QuoteUnit.USD:
        assert bar.open == Decimal("100") and bar.currency == Currency.USD


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("missing", "MISSING_INTERVALS"),
        ("duplicate", "DUPLICATE_BARS"),
        ("invalid_ohlc", "INVALID_BAR_CONTRACT"),
        ("negative_volume", "INVALID_BAR_CONTRACT"),
        ("nonfinite", "INVALID_NUMBER_OR_MISSING_FIELD"),
        ("incomplete", "INCOMPLETE_BAR"),
        ("out_of_session", "OUT_OF_SESSION"),
        ("malformed_timestamp", "INVALID_TIMESTAMP"),
        ("missing_volume", "INVALID_NUMBER_OR_MISSING_FIELD"),
        ("mixed_adjustment", "MIXED_ADJUSTMENT"),
        ("wrong_currency", "METADATA_MISMATCH_CURRENCY"),
        ("wrong_mic", "METADATA_MISMATCH_MIC_CODE"),
    ],
)
def test_bad_data_is_preserved_and_quarantined(scenario, code, historical):
    request, cal, provider, store = historical
    request = changed(request, fixture_scenario=scenario)
    identifier, job, _ = download(provider, request, cal, store)
    manifest, report, _ = store.inspect(identifier)
    assert report.status == "FAIL" and manifest["state"] == "QUARANTINED"
    assert code in {f.code for f in report.findings}
    assert store.pages(job) and store.validate(identifier) == report
    with pytest.raises(AtlasError, match="Quarantined"):
        store.visible_bars(identifier, NOW)


def test_unknown_availability_denied_and_assumption_explicit(historical):
    request, cal, provider, store = historical
    request = changed(request, fixture_scenario="unknown_availability")
    identifier = download(provider, request, cal, store)[0]
    assert store.visible_bars(identifier, NOW) == ()
    assert store.visible_bars(identifier, NOW, allow_assumed=True) == ()
    assert all(b.source_available_at is None for b in store.inspect(identifier)[2])
    request = changed(request, assumed_delay_seconds=5)
    assumed = download(provider, request, cal, store)[0]
    assert assumed != identifier and store.visible_bars(assumed, NOW) == ()
    bars = store.inspect(assumed)[2]
    assert bars[0].availability == Availability.ASSUMED
    assert bars[0].source_available_at is None and bars[0].ingested_at == NOW
    assert not bars[0].visible_at(bars[0].end + timedelta(seconds=4), allow_assumed=True)
    assert bars[0].visible_at(bars[0].end + timedelta(seconds=5), allow_assumed=True)
    assert len(store.visible_bars(assumed, NOW, allow_assumed=True)) == 10


def test_no_lookahead_original_boundary(historical):
    request, cal, provider, store = historical
    identifier = download(provider, request, cal, store)[0]
    first = store.inspect(identifier)[2][0]
    assert store.visible_bars(identifier, first.end) == ()
    assert store.visible_bars(identifier, first.end + timedelta(seconds=1)) == ()
    assert store.visible_bars(identifier, first.end + timedelta(seconds=2)) == (first,)


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", "NaN"),
        ("close", "Infinity"),
        ("low", "0"),
        ("high", "1"),
        ("volume", "-1"),
        ("complete", "false"),
        ("start", "2024-10-28T08:00:00"),
        ("source_available_at", "2024-10-28T08:00:00Z"),
        ("unit", "USD"),
    ],
)
def test_invalid_bar_contracts(field, value, historical):
    request, cal, provider, _ = historical
    bars, _ = assess(request, all_pages(provider, request), cal)
    with pytest.raises((ValidationError, AtlasError)):
        HistoricalBar.model_validate({**bars[0].model_dump(), field: value})


@pytest.mark.parametrize(
    "venue,day,opened,closed,half",
    [
        (Venue.LSE, "2024-03-11", "08:00", "16:30", False),
        (Venue.NYSE, "2024-03-11", "13:30", "20:00", False),
        (Venue.NASDAQ, "2024-03-08", "14:30", "21:00", False),
        (Venue.LSE, "2024-10-28", "08:00", "16:30", False),
        (Venue.NASDAQ, "2024-10-28", "13:30", "20:00", False),
        (Venue.NYSE, "2024-11-04", "14:30", "21:00", False),
        (Venue.LSE, "2024-12-24", "08:00", "12:30", True),
        (Venue.NYSE, "2024-11-29", "14:30", "18:00", True),
    ],
)
def test_calendar_dst_and_halfdays(venue, day, opened, closed, half):
    local = date.fromisoformat(day)
    cal = HistoricalCalendar(venue, local, local)
    session = cal.session(venue, local)
    assert session.open.strftime("%H:%M") == opened
    assert session.close.strftime("%H:%M") == closed
    assert session.half_day == half
    saved = RecordedCalendar(cal.provenance())
    assert saved.session(venue, local) == session
    assert saved.expected_starts(session.open, session.close) == cal.expected_starts(
        session.open, session.close
    )


@pytest.mark.parametrize(
    "venue,day",
    [
        (Venue.LSE, "2024-12-25"),
        (Venue.LSE, "2022-09-19"),
        (Venue.NYSE, "2012-10-29"),
        (Venue.NASDAQ, "2025-01-09"),
        (Venue.NYSE, "2024-07-04"),
    ],
)
def test_holidays_and_exceptional_closures(venue, day):
    local = date.fromisoformat(day)
    assert HistoricalCalendar(venue, local, local).session(venue, local) is None


def test_calendar_fingerprint_and_coverage(historical):
    _, cal, _, _ = historical
    assert digest(cal.provenance()) == HistoricalCalendar(cal.venue, cal.start, cal.end).checksum
    changed_calendar = cal.provenance()
    changed_calendar["entries"][0]["close"] = "2024-10-28T12:30:00+00:00"
    assert digest(changed_calendar) != cal.checksum
    with pytest.raises(AtlasError):
        cal.session(cal.venue, date(2024, 10, 29))
    with pytest.raises(AtlasError):
        HistoricalCalendar(Venue.LSE, date(1999, 1, 1), date(1999, 1, 2))


def test_mapping_metadata_never_inferred(historical):
    request, _, _, _ = historical
    instrument = request.instrument
    with pytest.raises((ValueError, AtlasError)):
        changed(instrument, quote_unit=QuoteUnit.USD)
    with pytest.raises(ValueError):
        instrument.mapping("unconfigured", request.start, request.end)
    mapping = changed(instrument.mappings[0], currency_code="USD")
    explicit = changed(
        instrument, quote_unit=QuoteUnit.USD, trading_currency=Currency.USD, mappings=(mapping,)
    )
    assert explicit.venue == Venue.LSE  # LSE does not imply GBX.
    with pytest.raises(ValueError):
        changed(instrument.mappings[0], effective_to=datetime(1999, 1, 1, tzinfo=UTC))


def test_corporate_actions_version_dataset_not_just_request(historical):
    request, cal, provider, store = historical
    initial = download(provider, request, cal, store)[0]
    action = CorporateAction(
        action_id="example-split",
        instrument_id=request.instrument.instrument_id,
        kind="split",
        effective_date=date(2024, 10, 28),
        ratio=Decimal("2"),
        source="fictional",
    )
    revised = download(provider, request, cal, store, actions=(action,))[0]
    assert revised != initial
    assert store.inspect(revised)[0]["actions"][0]["event_at"] is None
    assert store.validate(revised).status == "WARNING"
    with pytest.raises(ValidationError):
        changed(action, ratio=None)
    with pytest.raises(ValidationError):
        changed(action, kind="dividend")


def test_discontinuity_is_warning_not_repaired(historical):
    request, cal, provider, store = historical
    request = changed(request, fixture_scenario="discontinuity")
    manifest, report, bars = store.inspect(download(provider, request, cal, store)[0])
    assert manifest["state"] == "ACCEPTED" and bars[-1].open == Decimal("1000")
    assert "POTENTIAL_ACTION_OR_PRICE_GAP" in {f.code for f in report.findings}


def test_interrupted_download_resumes_without_double_rows(historical):
    request, cal, provider, store = historical
    real = provider.fetch_page
    calls = []

    def interrupted(req, cursor=None):
        calls.append(cursor)
        if len(calls) == 2:
            raise ProviderError("NETWORK_FAILURE")
        return real(req, cursor)

    provider.fetch_page = interrupted
    with pytest.raises(ProviderError):
        download(provider, request, cal, store)
    with store.connect() as db:
        job = db.execute("SELECT * FROM md_jobs").fetchone()
        assert job["status"] == "INTERRUPTED" and job["page_count"] == 1
        assert db.execute("SELECT count(*) FROM md_datasets").fetchone()[0] == 0
    provider.fetch_page = real
    identifier = download(provider, request, cal, store)[0]
    assert len(store.inspect(identifier)[2]) == 10
    assert len(store.pages(job["job_id"])) == 3


def test_fencing_transfer_and_expired_worker_cannot_publish(historical):
    request, cal, provider, store = historical
    job, old, _ = store.begin_job(request, cal.provenance())
    with pytest.raises(AtlasError, match="active worker"):
        store.begin_job(request, cal.provenance())
    store.clock = lambda: NOW + timedelta(seconds=91)
    same, new, _ = store.begin_job(request, cal.provenance())
    assert same == job and old != new
    with pytest.raises(JobOwnershipLost):
        store.checkpoint(job, old, provider.fetch_page(request))
    store.interrupted(job, old, "STALE")
    assert store.job(job)["owner"] == new
    pages = all_pages(provider, request)
    bars, report = assess(request, pages, cal)
    with pytest.raises(JobOwnershipLost):
        store.publish(
            job,
            old,
            request,
            provider.descriptor,
            cal.provenance(),
            bars,
            report,
            pages,
            (),
            "UNKNOWN",
        )


def test_publish_transaction_rolls_back_and_resumes(historical):
    request, cal, provider, store = historical
    with store.connect(write=True) as db:
        db.execute(
            "CREATE TRIGGER test_fail BEFORE INSERT ON md_bars "
            "BEGIN SELECT RAISE(ABORT, 'failure'); END"
        )
    with pytest.raises(AtlasError):
        download(provider, request, cal, store)
    with store.connect(write=True) as db:
        assert db.execute("SELECT count(*) FROM md_datasets").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM md_bars").fetchone()[0] == 0
        db.execute("DROP TRIGGER test_fail")
    identifier = download(provider, request, cal, store)[0]
    assert len(store.inspect(identifier)[2]) == 10


@pytest.mark.parametrize("target", ["bar", "manifest", "raw"])
def test_checksums_reject_corruption(target, historical):
    request, cal, provider, store = historical
    identifier, job, _ = download(provider, request, cal, store)
    with store.connect(write=True) as db:
        if target == "bar":
            db.execute(
                "UPDATE md_bars SET content=replace(content, '250.1', '250.9') "
                "WHERE dataset_id=? AND ordinal=0",
                (identifier,),
            )
        elif target == "manifest":
            db.execute("UPDATE md_datasets SET manifest=replace(manifest,'ACCEPTED','QUARANTINED')")
        else:
            db.execute("UPDATE md_pages SET content=x'0001' WHERE job_id=? AND ordinal=0", (job,))
    with pytest.raises(AtlasError):
        store.validate(identifier)


def test_safe_backup_foreign_db_and_schema(historical, tmp_path):
    request, cal, provider, store = historical
    identifier = download(provider, request, cal, store)[0]
    backup = tmp_path / "backup.sqlite3"
    store.backup(backup)
    assert HistoricalStore(backup).inspect(identifier) == store.inspect(identifier)
    with pytest.raises(AtlasError):
        store.backup(backup)
    foreign = tmp_path / "research.sqlite3"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE events(value)")
    before = foreign.read_bytes()
    with pytest.raises(AtlasError, match="not an Atlas historical"):
        HistoricalStore(foreign)
    assert foreign.read_bytes() == before
    with store.connect(write=True) as db:
        db.execute("UPDATE md_meta SET version=999")
    with pytest.raises(AtlasError):
        HistoricalStore(store.path)


def test_persistent_rate_budget(historical):
    _, _, _, store = historical
    assert store.reserve_request("twelve-data", daily_limit=2) == 0
    assert store.reserve_request("twelve-data", daily_limit=2) == 8
    store.clock = lambda: NOW + timedelta(seconds=8)
    assert store.reserve_request("twelve-data", daily_limit=2) == 0
    with pytest.raises(ProviderError, match="DAILY_BUDGET"):
        store.reserve_request("twelve-data", daily_limit=2)
    store.clock = lambda: NOW + timedelta(days=1)
    assert store.reserve_request("twelve-data", daily_limit=2) == 0
    store.defer_requests("twelve-data", 100)
    assert store.reserve_request("twelve-data") == 100


def external_request(historical):
    request = historical[0]
    instrument = fixture_instruments()[3]
    mapping = changed(instrument.mappings[0], provider="twelve-data", symbol="FAKE")
    instrument = changed(instrument, mappings=(mapping,))
    return changed(
        request,
        provider="twelve-data",
        instrument=instrument,
        start=datetime(2024, 10, 28, 13, 30, tzinfo=UTC),
        end=datetime(2024, 10, 28, 13, 33, tzinfo=UTC),
    )


def mock_body(request):
    starts = [request.start + timedelta(minutes=i) for i in reversed(range(3))]
    return {
        "status": "ok",
        "meta": {
            "symbol": "FAKE",
            "mic_code": "XNAS",
            "currency": "USD",
            "exchange_timezone": "America/New_York",
            "interval": "1min",
        },
        "values": [
            {
                "datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                "open": "100.123456789",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "123",
            }
            for t in starts
        ],
    }


def external(handler, **options):
    return TwelveDataProvider(
        "fictional_test_key",
        allow_network=True,
        access_confirmed=True,
        license_reference="MOCKED_TEST_ONLY",
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
        sleep=lambda _: None,
        **options,
    )


def test_real_adapter_contract_mocked_no_auth_key_in_url(historical):
    request = external_request(historical)
    seen = []

    def handler(req):
        seen.append(req)
        assert req.url.host == "api.twelvedata.com"
        assert "fictional_test_key" not in str(req.url)
        assert req.headers["Authorization"] == "apikey fictional_test_key"
        assert req.url.params["adjust"] == "none" and req.url.params["timezone"] == "UTC"
        return httpx.Response(200, json=mock_body(request))

    provider = external(handler)
    page = provider.fetch_page(request)
    cal = HistoricalCalendar(Venue.NASDAQ, date(2024, 10, 28), date(2024, 10, 28))
    bars, report = assess(request, (page,), cal)
    assert len(seen) == 1 and len(bars) == 3 and report.status == "WARNING"
    assert bars[0].open == Decimal("100.123456789")
    assert all(b.availability == Availability.UNKNOWN for b in bars)
    assert "fictional_test_key" not in page.model_dump_json()


@pytest.mark.parametrize(
    "status,calls,code",
    [
        (401, 1, "AUTH_OR_ENTITLEMENT_REJECTED"),
        (403, 1, "AUTH_OR_ENTITLEMENT_REJECTED"),
        (500, 3, "PROVIDER_UNAVAILABLE"),
        (429, 1, "RATE_LIMIT"),
        (400, 1, "REQUEST_REJECTED"),
        (302, 1, "REQUEST_REJECTED"),
    ],
)
def test_bounded_provider_errors(status, calls, code, historical):
    attempts = []

    def handler(req):
        attempts.append(req)
        return httpx.Response(status, json={"status": "error", "message": "fictional_test_key"})

    with pytest.raises(ProviderError, match=code) as caught:
        external(handler).fetch_page(external_request(historical))
    assert len(attempts) == calls and "fictional_test_key" not in str(caught.value)


def test_retry_after_honoured_and_persistable(historical):
    delays = []
    provider = external(
        lambda _: httpx.Response(429, json={}, headers={"Retry-After": "172800"}),
        on_backoff=delays.append,
    )
    with pytest.raises(ProviderError) as caught:
        provider.fetch_page(external_request(historical))
    assert delays == [172800] and caught.value.retry_after == 172800


def test_network_retry_and_invalid_json(historical):
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ConnectError("fictional_test_key", request=req)

    with pytest.raises(ProviderError, match="NETWORK_FAILURE"):
        external(handler).fetch_page(external_request(historical))
    assert len(calls) == 3
    with pytest.raises(ProviderError, match="INVALID_JSON"):
        external(lambda _: httpx.Response(200, text="invalid fictional_test_key")).fetch_page(
            external_request(historical)
        )


def test_external_pagination_descending_no_repeated_boundary(historical):
    request = external_request(historical)
    ends = []

    def handler(req):
        ends.append(req.url.params["end_date"])
        body = mock_body(request)
        body["values"] = body["values"][:2] if len(ends) == 1 else body["values"][2:]
        return httpx.Response(200, json=body)

    pages = all_pages(external(handler, page_size=2), request)
    assert len(pages) == 2 and ends == ["2024-10-28 13:32:00", "2024-10-28 13:30:00"]


def test_external_access_and_uk_minute_fail_closed(historical):
    with pytest.raises(ProviderError, match="NOT_APPROVED"):
        TwelveDataProvider("fictional_test_key")
    request = historical[0]
    mapping = changed(request.instrument.mappings[0], provider="twelve-data")
    request = changed(
        request, provider="twelve-data", instrument=changed(request.instrument, mappings=(mapping,))
    )
    with pytest.raises(ProviderError, match="COVERAGE_NOT_VERIFIED"):
        external(lambda _: pytest.fail("No UK request permitted")).fetch_page(request)


def test_raw_response_secrets_removed_or_rejected(historical):
    body = mock_body(external_request(historical))
    body["token"] = "fictional_test_key"
    body["meta"]["account_id"] = "private"
    cleaned = safe_body(body, "fictional_test_key")
    assert "token" not in cleaned and "account_id" not in cleaned["meta"]
    with pytest.raises(ValidationError):
        RawPage(
            body={"api_key": "private"},
            fetched_at=NOW,
            normalization_order="ascending",
            original_availability="unknown",
            wire_sha256="0" * 64,
        )


def test_cli_offline_commands_and_isolation(tmp_path, capsys, monkeypatch):
    db = tmp_path / "historical.sqlite3"
    history = tmp_path / "history.sqlite3"
    monkeypatch.setenv("ATLAS_HISTORY_DB", str(history))
    assert main(["market-data", "providers"]) == 0
    assert main(["market-data", "instruments"]) == 0
    assert not history.exists()
    assert main(["market-data", "download", "--db", str(db)]) == 0
    store = HistoricalStore(db)
    identifier = store.coverage()[0][0]["dataset_id"]
    for command in ("validate", "inspect"):
        assert main(["market-data", command, identifier, "--db", str(db)]) == 0
    assert main(["market-data", "coverage", "--db", str(db)]) == 0
    assert main(["market-data", "coverage", "--limit", "1000", "--db", str(db)]) == 1
    assert not history.exists()
    with store.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert all(t.startswith("md_") for t in tables)
    assert "EXECUTION: NONE" in capsys.readouterr().out


def test_external_cli_requires_approval_before_reading_key(tmp_path, historical, monkeypatch):
    config = tmp_path / "request.json"
    config.write_text(json.dumps({"request": external_request(historical).model_dump(mode="json")}))
    db = tmp_path / "new.sqlite3"
    monkeypatch.setenv("ATLAS_TWELVEDATA_API_KEY", "fictional_never_sent")
    assert (
        main(
            [
                "market-data",
                "download",
                "--provider",
                "twelve-data",
                "--config",
                str(config),
                "--allow-network",
                "--db",
                str(db),
            ]
        )
        == 1
    )
    assert not db.exists()


def test_future_request_does_not_create_job(historical):
    request, cal, provider, store = historical
    store.clock = lambda: request.start
    with pytest.raises(AtlasError, match="future"):
        download(provider, request, cal, store)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM md_jobs").fetchone()[0] == 0


def test_namespace_cannot_dispatch_execution():
    import inspect

    from atlas.market_data import cli, service

    text = inspect.getsource(service) + inspect.getsource(cli)
    assert "atlas.broker" not in text and "atlas.notifications" not in text
    assert "atlas.research_history" not in text


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("missing_meta", "MALFORMED_RESPONSE"),
        ("missing_values", "MALFORMED_RESPONSE"),
        ("wrong_symbol", "METADATA_MISMATCH_SYMBOL"),
        ("wrong_timezone", "METADATA_MISMATCH_EXCHANGE_TIMEZONE"),
        ("numeric_float", "INVALID_NUMBER_OR_MISSING_FIELD"),
        ("negative_price", "INVALID_BAR_CONTRACT"),
        ("bad_available", "INVALID_BAR_CONTRACT"),
        ("naive_available", "INVALID_BAR_CONTRACT"),
        ("source_order", "SOURCE_ORDER_VIOLATION"),
        ("outside_request", "OUTSIDE_REQUEST"),
    ],
)
def test_malformed_raw_payloads_fail_closed(mutation, expected, historical):
    request, cal, provider, _ = historical
    page = provider.fetch_page(request)
    body = json.loads(json.dumps(page.body))
    if mutation == "missing_meta":
        body.pop("meta")
    elif mutation == "missing_values":
        body["values"] = None
    elif mutation == "wrong_symbol":
        body["meta"]["symbol"] = "WRONG"
    elif mutation == "wrong_timezone":
        body["meta"]["exchange_timezone"] = "UTC"
    elif mutation == "numeric_float":
        body["values"][0]["open"] = 250.1
    elif mutation == "negative_price":
        body["values"][0]["low"] = "-1"
    elif mutation == "bad_available":
        body["values"][0]["source_available_at"] = "not-a-date"
    elif mutation == "naive_available":
        body["values"][0]["source_available_at"] = "2024-10-28T08:01:02"
    elif mutation == "source_order":
        body["values"].reverse()
    else:
        body["values"][0]["datetime"] = request.end.isoformat()
        body["values"][0]["source_available_at"] = (
            request.end + timedelta(minutes=1, seconds=2)
        ).isoformat()
    _, report = assess(request, (changed(page, body=body),), cal)
    assert report.status == "FAIL" and expected in {f.code for f in report.findings}


def test_fixture_page_boundary_dedup_and_unknown_scenario(historical):
    request, cal, provider, _ = historical
    pages = all_pages(provider, request)
    assert [len(p.body["values"]) for p in pages] == [4, 4, 2]
    stamps = [r["datetime"] for p in pages for r in p.body["values"]]
    assert len(stamps) == len(set(stamps)) == 10
    assert assess(request, pages, cal) == assess(request, pages, cal)
    with pytest.raises(ProviderError):
        provider.fetch_page(changed(request, fixture_scenario="not_a_scenario"))
    with pytest.raises(ProviderError):
        provider.fetch_page(request, "untrusted-cursor")


def test_pagination_loop_not_published(historical):
    request, cal, provider, store = historical
    page = changed(provider.fetch_page(request), next_cursor="4")
    provider.fetch_page = lambda *_: page
    with pytest.raises(ProviderError, match="PAGINATION_LOOP"):
        download(provider, request, cal, store)
    assert store.coverage()[1] == 0


def test_json_auth_and_short_rate_limit_retry(historical):
    request = external_request(historical)
    attempts = []

    def handler(req):
        attempts.append(req)
        if len(attempts) == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "1"})
        return httpx.Response(200, json=mock_body(request))

    assert external(handler).fetch_page(request).http_status == 200
    assert len(attempts) == 2
    with pytest.raises(ProviderError, match="AUTH_OR_ENTITLEMENT_REJECTED"):
        external(lambda _: httpx.Response(200, json={"status": "error", "code": 403})).fetch_page(
            request
        )


def test_retry_after_http_date(historical):
    from email.utils import format_datetime

    after = format_datetime(NOW + timedelta(seconds=100), usegmt=True)
    with pytest.raises(ProviderError) as caught:
        external(lambda _: httpx.Response(429, json={}, headers={"Retry-After": after})).fetch_page(
            external_request(historical)
        )
    assert caught.value.retry_after >= 100


def test_external_metadata_mismatch_quarantines(historical):
    request = external_request(historical)
    body = mock_body(request)
    body["meta"]["currency"] = "GBX"
    cal = HistoricalCalendar(Venue.NASDAQ, date(2024, 10, 28), date(2024, 10, 28))
    _, report = assess(
        request, (external(lambda _: httpx.Response(200, json=body)).fetch_page(request),), cal
    )
    assert report.status == "FAIL" and report.valid_bars == 0


def test_ingest_no_portfolio_file_changes(historical, tmp_path, monkeypatch):
    request, cal, provider, store = historical
    marker = tmp_path / "existing-research.sqlite3"
    marker.write_bytes(b"untouched-existing-history")
    monkeypatch.setenv("ATLAS_HISTORY_DB", str(marker))
    download(provider, request, cal, store)
    assert marker.read_bytes() == b"untouched-existing-history"


def test_empty_holiday_window_is_explicit_warning(tmp_path):
    cal = HistoricalCalendar(Venue.LSE, date(2024, 12, 25), date(2024, 12, 25))
    start = datetime(2024, 12, 25, 8, tzinfo=UTC)
    request = DownloadRequest(
        provider="fixture-v1",
        instrument=fixture_instruments()[0],
        start=start,
        end=start + timedelta(minutes=1),
    )
    store = HistoricalStore(tmp_path / "md.sqlite3", clock=lambda: NOW)
    manifest, report, bars = store.inspect(
        download(FixtureProvider(cal, clock=lambda: NOW), request, cal, store)[0]
    )
    assert bars == () and manifest["row_count"] == 0
    assert "NO_SESSION_BARS_EXPECTED" in {f.code for f in report.findings}


def test_original_fixture_specification_matches_generator(historical):
    from pathlib import Path

    spec = json.loads(
        (
            Path(__file__).parent / "fixtures" / "market_data" / "fixture_specification.json"
        ).read_text()
    )
    request, cal, provider, _ = historical
    assert spec["origin"] == "ARTIFICIAL_OFFLINE_FIXTURE"
    assert provider.fetch_page(request).body["values"][0] == spec["first_xlon_gbx_row"]
    assert (
        assess(request, all_pages(provider, request), cal)[1].valid_bars == spec["default_minutes"]
    )


def test_calendar_scope_mismatch_prevents_job(historical):
    request, cal, provider, store = historical
    wrong = HistoricalCalendar(Venue.NYSE, cal.start, cal.end)
    with pytest.raises(AtlasError, match="calendar does not match"):
        download(provider, request, wrong, store)
    outside = changed(
        request, start=request.start + timedelta(days=1), end=request.end + timedelta(days=1)
    )
    with pytest.raises(AtlasError, match="outside materialized"):
        download(provider, outside, cal, store)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM md_jobs").fetchone()[0] == 0


def test_action_coverage_declaration_is_validated(historical):
    request, cal, provider, store = historical
    with pytest.raises(AtlasError, match="coverage declaration"):
        download(provider, request, cal, store, action_coverage="invented")
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM md_jobs").fetchone()[0] == 0
