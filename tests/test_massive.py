"""Original artificial Massive responses; no external requests or genuine credentials."""

import base64
import gc
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from threading import Barrier

import httpx
import pytest

from atlas.domain import QuoteUnit, Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import Adjustment, DownloadRequest, RawPage
from atlas.market_data.massive import MassiveProvider, identity_instrument, milliseconds
from atlas.market_data.massive_credentials import load_massive_key
from atlas.market_data.massive_limits import MassiveQuota
from atlas.market_data.massive_pilot import PILOT_ID, run_pilot
from atlas.market_data.providers import ProviderError
from atlas.market_data.quality import assess
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore

KEY = "fictional_massive_key_never_sent_123456"
DAY = date(2026, 10, 26)


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 27, 12, tzinfo=UTC).timestamp()
        self.waits = []

    def __call__(self):
        return self.value

    def utc(self):
        return datetime.fromtimestamp(self.value, UTC)

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds


def identity():
    return dict(
        ticker="AAPL",
        locale="us",
        market="stocks",
        type="CS",
        active=True,
        currency_name="usd",
        primary_exchange="XNAS",
        composite_figi="BBG000FAKE01",
        as_of=DAY.isoformat(),
    )


def aggregates(request, *, count=3):
    rows = [
        dict(
            t=milliseconds(request.start + timedelta(minutes=i)),
            o=245,
            h=247,
            l=244,
            c=246,
            v=100 + i,
        )
        for i in range(count)
    ]
    return dict(
        ticker="AAPL",
        adjusted=False,
        queryCount=len(rows),
        resultsCount=len(rows),
        status="OK",
        results=rows,
    )


@pytest.fixture
def context(tmp_path):
    clock = Clock()
    calendar = HistoricalCalendar(Venue.NASDAQ, DAY, DAY)
    start = calendar.sessions()[0].open
    request = DownloadRequest(
        provider="massive",
        instrument=identity_instrument(identity()),
        start=start,
        end=start + timedelta(minutes=3),
    )
    quota = MassiveQuota(tmp_path / "quota.sqlite3", clock=clock, sleep=clock.sleep)
    store = HistoricalStore(tmp_path / "historical.sqlite3", clock=clock.utc)
    return clock, calendar, request, quota, store


def provider(context, handler=None, **kwargs):
    clock, _, request, quota, _ = context

    def respond(call):
        assert call.url.host == "api.massive.com"
        assert call.headers["Authorization"] == "Bearer " + KEY
        assert KEY not in str(call.url)
        if "/reference/" in call.url.path:
            assert call.url.params["date"] == DAY.isoformat()
            return httpx.Response(200, json={"status": "OK", "results": identity()})
        if handler:
            return handler(call)
        return httpx.Response(200, json=aggregates(request))

    return MassiveProvider(
        quota,
        allow_network=True,
        access_reference="OFFLINE_FIXTURE",
        key=KEY,
        transport=httpx.MockTransport(respond),
        clock=clock.utc,
        **kwargs,
    )


def test_roundtrip_normalization_restart_cache_backup(context, tmp_path):
    clock, calendar, request, quota, store = context
    adapter = provider(context)
    dataset, job, cached = download(adapter, request, calendar, store)
    manifest, report, bars = store.inspect(dataset)
    assert not cached and manifest["state"] == "ACCEPTED" and report.status == "WARNING"
    assert len(bars) == 3 and bars[0].provider_timestamp == str(milliseconds(request.start))
    assert bars[0].open == Decimal("245") and bars[0].unit == QuoteUnit.USD
    assert bars[0].start == request.start and bars[-1].end == request.end
    assert all(b.source_available_at is None and b.permitted_at is None for b in bars)
    assert not store.visible_bars(dataset, clock.utc())
    restored = HistoricalStore(store.path, read_only=True)
    assert restored.inspect(dataset) == store.inspect(dataset)
    count = len(quota.audit(None))
    assert download(provider(context), request, calendar, store) == (dataset, job, True)
    assert len(quota.audit(None)) == count
    target = tmp_path / "backup.sqlite3"
    store.backup(target)
    assert HistoricalStore(target, read_only=True).inspect(dataset) == store.inspect(dataset)


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency_name", "gbp"),
        ("primary_exchange", "XLON"),
        ("ticker", "MSFT"),
        ("type", "ETF"),
        ("locale", "gb"),
        ("market", "otc"),
        ("active", False),
        ("composite_figi", None),
        ("currency_name", None),
    ],
)
def test_metadata_rejects_incorrect_identity(context, field, value):
    adapter = provider(context)
    altered = identity() | {field: value}
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "OK", "results": altered})
    )
    with pytest.raises(ProviderError, match="METADATA_IDENTITY"):
        adapter.identity("AAPL", DAY)


def test_native_decimal_precision_and_split_policy(context):
    request = context[2].model_copy(update={"adjustment": Adjustment.SPLITS})

    def response(call):
        assert call.url.params["adjusted"] == "true"
        text = json.dumps(aggregates(request)).replace('"adjusted": false', '"adjusted": true')
        text = text.replace('"o": 245', '"o": 245.12345678901234567890123456789')
        return httpx.Response(200, content=text.encode())

    page = provider(context, response).fetch_page(request)
    with localcontext() as arithmetic:
        arithmetic.prec = 6
        bars, report = assess(request, [page], context[1])
    assert bars[0].open == Decimal("245.12345678901234567890123456789")
    assert bars[0].adjustment == Adjustment.SPLITS and report.status != "FAIL"


@pytest.mark.parametrize("mutation", ["missing", "empty", "duplicate", "reverse"])
def test_empty_missing_and_duplicate_not_fabricated(context, mutation):
    request = context[2]
    body = aggregates(request)
    if mutation == "empty":
        body["results"] = []
    elif mutation == "missing":
        body["results"].pop(1)
    elif mutation == "duplicate":
        body["results"][1] = dict(body["results"][0])
    else:
        body["results"].reverse()
    body["resultsCount"] = body["queryCount"] = len(body["results"])
    adapter = provider(context, lambda _: httpx.Response(200, json=body))
    if mutation in ("duplicate", "reverse"):
        with pytest.raises(ProviderError, match="CONTRACT_REJECTED"):
            adapter.fetch_page(request)
    else:
        dataset = download(adapter, request, context[1], context[4])[0]
        manifest, report, bars = context[4].inspect(dataset)
        assert manifest["state"] == "QUARANTINED" and report.status == "FAIL"
        assert len(bars) == (0 if mutation == "empty" else 2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("o", 0),
        ("o", -1),
        ("o", "245"),
        ("o", True),
        ("h", 1),
        ("l", 999),
        ("v", -1),
        ("v", None),
        ("t", "bad"),
        ("t", True),
        ("t", 1),
        ("t", 99999999999999999999),
        ("otc", True),
    ],
)
def test_bad_ohlcv_or_timestamp_rejected(context, field, value):
    body = aggregates(context[2])
    body["results"][0][field] = value
    adapter = provider(context, lambda _: httpx.Response(200, json=body))
    with pytest.raises(ProviderError, match="CONTRACT_REJECTED"):
        adapter.fetch_page(context[2])


@pytest.mark.parametrize(
    "field,value",
    [
        ("adjusted", True),
        ("adjusted", 0),
        ("ticker", "MSFT"),
        ("resultsCount", 99),
        ("resultsCount", True),
        ("queryCount", 1),
        ("queryCount", 5001),
        ("results", None),
    ],
)
def test_bad_aggregate_evidence_rejected(context, field, value):
    body = aggregates(context[2]) | {field: value}
    with pytest.raises(ProviderError):
        provider(context, lambda _: httpx.Response(200, json=body)).fetch_page(context[2])


@pytest.mark.parametrize(
    "suffix",
    [
        "https://evil.invalid/x?cursor=test",
        "http://api.massive.com/X?cursor=test",
        "https://api.massive.com.evil.invalid/X?cursor=test",
        "https://user:pass@api.massive.com/X?cursor=test",
        "https://user%3Apass@api.massive.com/X?cursor=test",
        "https://api.massive.com:444/X?cursor=test",
        "https://api.massive.com/X?cursor=test#bad",
        "https://api.massive.com/X?cursor=test&apiKey=never-persist",
        "https://api.massive.com/X?cursor=a&cursor=b",
        "https://api.massive.com/X?cursor=a&adjusted=true",
        "https://api.massive.com/X?cursor=a&sort=desc",
        "https://api.massive.com/X?cursor=a&limit=1",
        "https://api.massive.com/other?cursor=a",
        "/X?cursor=a",
        "https://api.massive.com/X?cursor=",
    ],
)
def test_adversarial_continuations(context, suffix):
    adapter = provider(context)
    path = adapter.aggregate_path(context[2])
    body = aggregates(context[2]) | {"next_url": suffix.replace("/X?", path + "?")}
    adapter = provider(context, lambda _: httpx.Response(200, json=body))
    with pytest.raises(ProviderError, match="UNSAFE_CONTINUATION"):
        adapter.fetch_page(context[2])


def test_long_cursor_durable_resume_and_lease_heartbeat(context):
    clock, calendar, request, quota, store = context
    adapter = provider(context)
    path = adapter.aggregate_path(request)
    cursor = "C" * 2000
    calls = []
    interrupted = [True]

    def response(call):
        calls.append(str(call.url))
        body = aggregates(request)
        if call.url.params.get("cursor") is None:
            body["results"] = body["results"][:1]
            body.update(
                resultsCount=1,
                queryCount=1,
                next_url="https://api.massive.com" + path + "?cursor=" + cursor,
            )
        elif interrupted[0]:
            raise httpx.ReadTimeout("offline interruption")
        else:
            body["results"] = body["results"][1:]
            body.update(resultsCount=2, queryCount=2)
        return httpx.Response(200, json=body)

    adapter = provider(context, response, attempts=1)
    with pytest.raises(ProviderError) as failure:
        download(adapter, request, calendar, store, refresh=True)
    job = failure.value.resume_job_id
    assert len(store.pages(job)) == 1 and len(store.job(job)["cursor"]) > 100
    interrupted[0] = False
    clock.sleep(120)
    restarted = HistoricalStore(store.path, clock=clock.utc)
    renewed = provider(context, response, attempts=1)
    result = download(renewed, request, calendar, restarted, resume_job_id=job)
    assert len(restarted.inspect(result[0])[2]) == 3
    assert sum("cursor=" not in url for url in calls) == 1
    assert renewed.heartbeat is None


@pytest.mark.parametrize(
    "status,expected", [(401, 1), (403, 1), (302, 1), (400, 1), (429, 3), (500, 3)]
)
def test_status_retry_attempts_and_redaction(context, status, expected):
    adapter = provider(context)
    calls = []

    def response(call):
        calls.append(call)
        return httpx.Response(
            status,
            content=(KEY + " must never escape").encode(),
            headers={"Retry-After": "70", "Location": "https://evil.invalid"},
        )

    adapter.transport = httpx.MockTransport(response)
    with pytest.raises(ProviderError) as error:
        adapter.identity("AAPL", DAY)
    assert len(calls) == expected and KEY not in str(error.value)
    assert all(c.url.host == "api.massive.com" for c in calls)
    if status == 429:
        assert sum(context[0].waits) >= 140


@pytest.mark.parametrize(
    "content", [b"not json", b"[]", b'{"status":"OK","results":NaN}', b'{"status":"ERROR"}']
)
def test_malformed_json_and_status_fail_closed(context, content):
    adapter = provider(context)
    adapter.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    with pytest.raises(ProviderError):
        adapter.identity("AAPL", DAY)


def test_network_retry_counts_and_logging_redaction(context, caplog):
    adapter = provider(context)

    def failing(call):
        logging.getLogger("httpcore.http11").warning("echo %s", KEY)
        raise httpx.ConnectTimeout(KEY, request=call)

    adapter.transport = httpx.MockTransport(failing)
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as error:
        adapter.identity("AAPL", DAY)
    assert KEY not in str(error.value) + caplog.text
    assert "REDACTED" in caplog.text
    with context[3].transaction() as db:
        assert db.execute("SELECT count(*) FROM massive_attempts").fetchone()[0] == 3


def test_rolling_window_spacing_restart_and_no_bursts(context):
    clock, _, _, quota, _ = context
    starts = []
    for index in range(13):
        if index == 5:
            clock.sleep(300)
        current = MassiveQuota(quota.path, clock=clock, sleep=clock.sleep)
        token = current.acquire("ticker_metadata" if index % 2 else "minute_aggregates")
        starts.append(clock())
        clock.sleep(index % 3)
        current.finish(token, status=200, rows=1)
    assert all(sum(start - 60 < value <= start for value in starts) <= 4 for start in starts)
    assert all(b - a >= 16 for a, b in zip(starts, starts[1:], strict=False))


def test_concurrent_quota_one_owner_and_uncertain_restart(context):
    clock, _, _, quota, _ = context
    barrier = Barrier(4)

    def reserve(index):
        local = MassiveQuota(quota.path, clock=clock, sleep=clock.sleep)
        barrier.wait(timeout=10)
        return local.reserve("minute_aggregates")[0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(reserve, range(4)))
    assert sum(t is not None for t in tokens) == 1
    clock.sleep(300)
    restarted = MassiveQuota(quota.path, clock=clock, sleep=clock.sleep)
    assert restarted.reserve("ticker_metadata")[0] is None
    with pytest.raises(ProviderError, match="UNCERTAIN_OWNER"):
        restarted.acquire("ticker_metadata", max_wait=2)
    restarted.finish(next(t for t in tokens if t), status=None)
    before = clock()
    token = restarted.acquire("ticker_metadata")
    assert clock() - before >= 16
    restarted.finish(token)


def test_long_dispatch_pause_remains_charged_and_clock_rollback(context):
    clock, _, _, quota, _ = context
    token = quota.acquire("ticker_metadata")
    clock.sleep(1000)
    assert quota.reserve("minute_aggregates")[0] is None
    quota.finish(token)
    clock.value -= 1
    with pytest.raises(ProviderError, match="CLOCK_MOVED_BACKWARDS"):
        quota.reserve("ticker_metadata")


def test_pilot_hard_cap_persists(context):
    clock, _, _, quota, _ = context
    quota.start_pilot("offline-pilot")
    for _ in range(4):
        token = quota.acquire("ticker_metadata", pilot="offline-pilot")
        quota.finish(token, status=200)
    restarted = MassiveQuota(quota.path, clock=clock, sleep=clock.sleep)
    with pytest.raises(ProviderError, match="BUDGET_EXHAUSTED"):
        restarted.acquire("minute_aggregates", pilot="offline-pilot")
    with pytest.raises(ProviderError, match="ALREADY_STARTED"):
        restarted.start_pilot("offline-pilot")
    assert len(restarted.audit("offline-pilot")) == 4


def test_lease_renewal_during_wait_exceeds_original_lease(context):
    clock, calendar, request, quota, store = context
    token = quota.acquire("ticker_metadata")
    quota.finish(token, retry_after=120)
    result = download(provider(context), request, calendar, store)
    assert store.job(result[1])["status"] == "COMPLETED"
    assert sum(clock.waits) >= 120


@pytest.mark.parametrize(
    "value", ["", "replace_with_key", "short", "has spaces in token", "x" * 300]
)
def test_missing_or_invalid_credentials_safe(tmp_path, monkeypatch, value):
    monkeypatch.setenv("MASSIVE_API_KEY", value)
    with pytest.raises(ProviderError) as error:
        load_massive_key(authorized=True, path=tmp_path / "not-opened")
    assert "MASSIVE_CREDENTIAL" in str(error.value)


def test_key_file_selective_loading_no_export_or_modification(tmp_path, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    path = tmp_path / "credentials.fixture"
    content = 'OTHER_LOCAL_SETTING=fictional_other_secret\nMASSIVE_API_KEY="' + KEY + '"\n'
    path.write_text(content)
    assert load_massive_key(authorized=True, path=path) == KEY
    assert path.read_text() == content
    with pytest.raises(ProviderError, match="NOT_AUTHORISED"):
        load_massive_key(path=path)
    path.write_text(content + "MASSIVE_API_KEY=" + KEY)
    with pytest.raises(ProviderError, match="AMBIGUOUS"):
        load_massive_key(authorized=True, path=path)


def test_evidence_rechecked_at_quality_boundary(context):
    page = provider(context).fetch_page(context[2])
    changed = page.model_dump()
    changed["body"]["aggregate_evidence"]["adjusted"] = True
    bars, report = assess(context[2], [RawPage.model_validate(changed)], context[1])
    assert not bars and report.status == "FAIL"
    assert "MASSIVE_PROVENANCE_MISMATCH" in {f.code for f in report.findings}


def test_quota_connections_close_without_gc(context, tmp_path):
    enabled = gc.isenabled()
    gc.disable()
    try:
        token = context[3].acquire("ticker_metadata")
        context[3].finish(token)
        context[3].path.rename(tmp_path / "quota-renamed.sqlite3")
        (tmp_path / "quota-renamed.sqlite3").unlink()
    finally:
        if enabled:
            gc.enable()


@pytest.mark.parametrize("adjustment,delay", [(Adjustment.TOTAL_RETURN, None), (Adjustment.RAW, 0)])
def test_unsupported_policy_no_network(context, adjustment, delay):
    request = context[2].model_copy(
        update={"adjustment": adjustment, "assumed_delay_seconds": delay}
    )
    with pytest.raises(ProviderError, match="REQUEST_POLICY"):
        provider(context).fetch_page(request)


def test_zero_volume_is_not_invented(context):
    body = aggregates(context[2])
    body["results"][0]["v"] = 0
    page = provider(context, lambda _: httpx.Response(200, json=body)).fetch_page(context[2])
    assert assess(context[2], [page], context[1])[0][0].volume == Decimal("0")


def pilot_factory(context, mode="success", calls=None):
    request = context[2].model_copy(update={"end": context[1].sessions()[0].close})

    def respond(call):
        if calls is not None:
            calls.append(str(call.url))
        if "/reference/" in call.url.path:
            if mode == "auth":
                return httpx.Response(401, content=KEY.encode())
            return httpx.Response(200, json={"status": "OK", "results": identity()})
        if mode == "entitlement":
            return httpx.Response(403, content=KEY.encode())
        if mode == "rate":
            return httpx.Response(429, headers={"Retry-After": "120"}, content=KEY.encode())
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://evil.invalid"})
        body = aggregates(request, count=390)
        if mode == "missing":
            body["results"].pop(20)
            body.update(resultsCount=389, queryCount=389)
        if mode == "unsafe":
            body["next_url"] = "https://evil.invalid?apiKey=" + KEY
        if mode == "many_pages":
            index = int(call.url.params.get("cursor", "0"))
            body["results"] = body["results"][index : index + 1]
            body.update(
                resultsCount=1,
                queryCount=1,
                next_url="https://api.massive.com" + call.url.path + "?cursor=" + str(index + 1),
            )
        return httpx.Response(200, json=body)

    def factory(quota, **kwargs):
        return MassiveProvider(quota, key=KEY, transport=httpx.MockTransport(respond), **kwargs)

    return factory


def test_pilot_success_and_fresh_process_integrity(context, tmp_path):
    calls = []
    report = run_pilot(
        authorized=True,
        root=tmp_path / "pilot",
        quota=context[3],
        clock=context[0].utc,
        provider_factory=pilot_factory(context, calls=calls),
    )
    assert report["status"] == "COMPLETED" and report["restart_verified"]
    assert report["authenticated_attempt_count"] == len(calls) == 2
    assert report["metadata_available"] and report["authentication_succeeded"]
    assert report["complete_session"] and report["stored_bars"] == 390
    assert report["normalization_passed"] and report["adjustment_verified"]
    assert report["historical_availability"] == "UNKNOWN"
    assert KEY not in json.dumps(report)
    assert all(KEY not in call for call in calls)
    assert not (tmp_path / "history.sqlite3").exists()
    with pytest.raises(ProviderError, match="ALREADY_STARTED"):
        run_pilot(
            authorized=True,
            root=tmp_path / "pilot",
            quota=context[3],
            clock=context[0].utc,
            provider_factory=pilot_factory(context, calls=calls),
        )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "mode,count,state",
    [
        ("auth", 1, "NOT_PUBLISHED"),
        ("entitlement", 2, "NOT_PUBLISHED"),
        ("rate", 2, "NOT_PUBLISHED"),
        ("redirect", 2, "NOT_PUBLISHED"),
        ("unsafe", 2, "NOT_PUBLISHED"),
        ("missing", 2, "QUARANTINED"),
        ("many_pages", 4, "NOT_PUBLISHED"),
    ],
)
def test_pilot_stops_no_retries_or_budget_reset(context, tmp_path, mode, count, state):
    calls = []
    report = run_pilot(
        authorized=True,
        root=tmp_path / "pilot",
        quota=context[3],
        clock=context[0].utc,
        provider_factory=pilot_factory(context, mode, calls),
    )
    assert report["status"] != "COMPLETED" and report["dataset_state"] == state
    assert report["authenticated_attempt_count"] == len(calls) == count
    assert report["rate_limit_responses"] == int(mode == "rate")
    assert KEY not in json.dumps(report)
    with context[3].transaction() as db:
        assert (
            db.execute("SELECT status FROM massive_pilots WHERE id=?", (PILOT_ID,)).fetchone()[0]
            == "STOPPED"
        )


def test_pilot_requires_explicit_opt_in_before_reading_key_or_database(tmp_path):
    with pytest.raises(ProviderError, match="EXPLICIT_NETWORK"):
        run_pilot(root=tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_pilot_cli_defaults_no_network(capsys):
    from atlas.main import main

    assert main(["market-data", "massive-pilot", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def test_persisted_cursor_cannot_put_current_key_in_request(context):
    adapter = provider(context)
    path = adapter.aggregate_path(context[2])
    adapter._key = None
    adapter._loader = lambda **kwargs: KEY
    # Loading occurs at the header boundary, after an untrusted persisted cursor is parsed.
    with pytest.raises(ProviderError, match="SECRET_IN_REQUEST"):
        adapter._request(path, {"cursor": KEY}, "minute_aggregates")
    assert not context[3].audit(None)


def test_wall_clock_changes_do_not_release_real_quota(tmp_path, monkeypatch):
    quota = MassiveQuota(tmp_path / "monotonic.sqlite3")
    token = quota.acquire("ticker_metadata")
    quota.finish(token)
    monkeypatch.setattr(time, "time", lambda: 10**15)
    assert quota.reserve("minute_aggregates")[0] is None


@pytest.mark.parametrize(
    "header", ["9999999999999999999999999", "90000", "Wed, 28 Oct 2026 12:00:00 GMT"]
)
def test_unbounded_retry_after_fails_closed_persistently(context, header):
    adapter = provider(context)
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(429, headers={"Retry-After": header})
    )
    with pytest.raises(ProviderError):
        adapter.identity("AAPL", DAY)
    assert len(context[3].audit(None)) == 1
    with pytest.raises(ProviderError, match="UNBOUNDED_RETRY"):
        context[3].reserve("ticker_metadata")


def test_cross_process_quota_serializes_attempt_authority(context):
    program = """import sys
from atlas.market_data.massive_limits import MassiveQuota
quota = MassiveQuota(sys.argv[1], clock=lambda: 1000)
print("READY", flush=True)
sys.stdin.readline()
token, delay = quota.reserve("ticker_metadata")
print("OWNER" if token else "WAIT", flush=True)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-B", "-c", program, str(context[3].path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        for _ in range(2)
    ]
    try:
        assert all(p.stdout.readline().strip() == "READY" for p in processes)
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        outcomes = [p.communicate(timeout=20) for p in processes]
        assert sorted(out.strip() for out, err in outcomes) == ["OWNER", "WAIT"]
        assert all(
            not err and p.returncode == 0 for p, (_, err) in zip(processes, outcomes, strict=True)
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_no_research_history_or_notification_side_effects(context, tmp_path, monkeypatch):
    marker = tmp_path / "protected-history.bin"
    marker.write_bytes(b"unchanged research history")
    monkeypatch.setenv("ATLAS_HISTORY_DB", str(marker))
    download(provider(context), context[2], context[1], context[4])
    assert marker.read_bytes() == b"unchanged research history"


@pytest.mark.parametrize("mode", ["cycle", "overlap", "backwards"])
def test_pagination_cycles_and_cross_page_inconsistency(context, mode):
    request = context[2]
    path = MassiveProvider.aggregate_path(request)

    def response(call):
        body = aggregates(request)
        later = call.url.params.get("cursor") is not None
        if not later:
            body["results"] = body["results"][:1] if mode != "backwards" else body["results"][1:]
            body["next_url"] = "https://api.massive.com" + path + "?cursor=NEXT"
        elif mode == "cycle":
            body["next_url"] = "https://api.massive.com" + path + "?cursor=NEXT"
        elif mode == "backwards":
            body["results"] = body["results"][:1]
        body["resultsCount"] = body["queryCount"] = len(body["results"])
        return httpx.Response(200, json=body)

    if mode == "cycle":
        with pytest.raises(ProviderError, match="PAGINATION_LOOP"):
            download(provider(context, response), request, context[1], context[4])
        assert context[4].coverage()[1] == 0
    else:
        dataset = download(provider(context, response), request, context[1], context[4])[0]
        manifest, quality, _ = context[4].inspect(dataset)
        assert manifest["state"] == "QUARANTINED"
        assert "SOURCE_PAGE_ORDER_VIOLATION" in {f.code for f in quality.findings}


@pytest.mark.parametrize("header", ["invalid", "0", "-1", "Tue, 27 Oct 2026 12:01:00 GMT"])
def test_retry_after_variants_persist_without_pilot_retries(context, header):
    quota = context[3]
    quota.start_pilot("test-rate")
    adapter = provider(context, attempts=1, pilot="test-rate")
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(429, headers={"Retry-After": header})
    )
    with pytest.raises(ProviderError, match="RATE_LIMIT"):
        adapter.identity("AAPL", DAY)
    assert len(quota.audit("test-rate")) == 1
    assert quota.reserve("minute_aggregates")[0] is None


def test_full_base_limit_without_continuation_is_rejected(context):
    body = aggregates(context[2]) | {"queryCount": 5000}
    body["results"] = body["results"][:1]
    body["resultsCount"] = 1
    with pytest.raises(ProviderError, match="TRUNCATED"):
        provider(context, lambda _: httpx.Response(200, json=body)).fetch_page(context[2])


@pytest.mark.parametrize(
    "body",
    [
        b'{"status":"OK","results":{"ticker":"AAPL"}}',
        b'{"status":"OK","results":null}',
        b'{"status":"OK"}',
    ],
)
def test_partial_metadata_is_not_accepted(context, body):
    adapter = provider(context)
    adapter.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    with pytest.raises(ProviderError):
        adapter.identity("AAPL", DAY)


def test_raw_checkpoints_and_report_do_not_contain_key(context):
    dataset = download(provider(context), context[2], context[1], context[4])[0]
    manifest, report, _ = context[4].inspect(dataset)
    for page in context[4].pages(manifest["source_job_id"]):
        assert KEY not in page.model_dump_json()
    assert KEY not in json.dumps(manifest) + report.model_dump_json()


@pytest.mark.parametrize("day,opened", [(date(2026, 3, 9), 13), (date(2026, 11, 2), 14)])
def test_massive_epoch_normalization_across_us_dst(day, opened):
    calendar = HistoricalCalendar(Venue.NASDAQ, day, day)
    session = calendar.sessions()[0]
    assert session.open.hour == opened
    evidence = identity() | {"as_of": day.isoformat()}
    request = DownloadRequest(
        provider="massive",
        instrument=identity_instrument(evidence),
        start=session.open,
        end=session.open + timedelta(minutes=1),
    )
    body = {
        "status": "ok",
        "meta": {
            "symbol": "AAPL",
            "mic_code": "XNAS",
            "currency": "USD",
            "exchange_timezone": "America/New_York",
            "interval": "1min",
            "massive_identity": evidence,
        },
        "values": [
            {
                "datetime": str(milliseconds(session.open)),
                "open": "1",
                "high": "2",
                "low": "1",
                "close": "2",
                "volume": "0",
            }
        ],
        "aggregate_evidence": {
            "ticker": "AAPL",
            "adjusted": False,
            "status": "OK",
            "resultsCount": 1,
            "queryCount": 1,
        },
    }
    page = RawPage(
        body=body,
        fetched_at=session.close + timedelta(days=1),
        wire_sha256="0" * 64,
        normalization_order="ascending",
        original_availability="unknown",
    )
    bars, report = assess(request, [page], calendar)
    assert report.status != "FAIL" and bars[0].start == session.open


def test_unapproved_massive_configuration_refuses_before_credentials(tmp_path, monkeypatch, capsys):
    import atlas.market_data.massive as credentials
    from atlas.main import main

    def forbidden(**kwargs):
        pytest.fail("Unapproved command read credentials")

    monkeypatch.setattr(credentials, "load_massive_key", forbidden)
    target = tmp_path / "must-not-create" / "history.sqlite3"
    assert (
        main(
            [
                "market-data",
                "download",
                "--provider",
                "massive",
                "--config",
                "examples/market-data.massive.unapproved.json",
                "--db",
                str(target),
            ]
        )
        == 1
    )
    assert not target.parent.exists()
    assert "not approved" in capsys.readouterr().err


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_availability", "fixture_known"),
        ("normalization_order", "descending"),
    ],
)
def test_forged_source_flags_cannot_invent_massive_availability(context, field, value):
    page = provider(context).fetch_page(context[2]).model_dump()
    page[field] = value
    for row in page["body"]["values"]:
        row["source_available_at"] = context[0].utc().isoformat()
    bars, quality = assess(context[2], [RawPage.model_validate(page)], context[1])
    assert not bars and quality.status == "FAIL"
    assert "MASSIVE_PROVENANCE_MISMATCH" in {f.code for f in quality.findings}


def test_reference_query_count_bound_rechecked_at_read_boundary(context):
    page = provider(context).fetch_page(context[2]).model_dump()
    page["body"]["aggregate_evidence"]["queryCount"] = 5001
    bars, quality = assess(context[2], [RawPage.model_validate(page)], context[1])
    assert not bars and quality.status == "FAIL"


def test_server_retry_after_applies_to_global_quota(context):
    calls = []
    adapter = provider(context)

    def response(call):
        calls.append(context[0]())
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "70"})
        return httpx.Response(200, json={"status": "OK", "results": identity()})

    adapter.transport = httpx.MockTransport(response)
    adapter.identity("AAPL", DAY)
    assert calls[1] - calls[0] >= 70 and len(context[3].audit(None)) == 2


@pytest.mark.parametrize("kind", ["raw", "json", "nested", "foreign-field"])
def test_encoded_credential_continuations_are_not_persistable(context, kind):
    payload = (
        KEY
        if kind == "raw"
        else json.dumps(
            {"apiKey": KEY if kind != "foreign-field" else "different_fictional_private_value"}
        )
    ).encode()
    for _ in range(5 if kind == "nested" else 1):
        payload = base64.urlsafe_b64encode(payload)
    adapter = provider(context)
    path = adapter.aggregate_path(context[2])
    with pytest.raises(ProviderError, match="UNSAFE_CONTINUATION"):
        adapter.continuation(
            "https://api.massive.com" + path + "?cursor=" + payload.decode(), path, "false"
        )
    assert not context[3].audit(None)


def test_encoded_cursor_rechecked_after_lazy_key_load(context):
    adapter = provider(context)
    adapter._key = None
    adapter._loader = lambda **kwargs: KEY
    cursor = base64.urlsafe_b64encode(KEY.encode()).decode()
    with pytest.raises(ProviderError, match="SECRET_IN_REQUEST"):
        adapter._request(
            adapter.aggregate_path(context[2]), {"cursor": cursor}, "minute_aggregates"
        )
    assert not context[3].audit(None)


@pytest.mark.parametrize("encoding", ["hex", "base64", "base64-hex", "hex-base64"])
def test_private_cursor_encodings_fail_closed(context, encoding):
    payload = json.dumps({"apiKey": KEY}).encode()
    if encoding == "hex":
        payload = payload.hex().encode()
    elif encoding == "base64":
        payload = base64.urlsafe_b64encode(payload)
    elif encoding == "base64-hex":
        payload = base64.urlsafe_b64encode(payload.hex().encode())
    else:
        payload = base64.urlsafe_b64encode(payload).hex().encode()
    adapter = provider(context)
    path = adapter.aggregate_path(context[2])
    with pytest.raises(ProviderError, match="UNSAFE_CONTINUATION"):
        adapter.continuation(
            "https://api.massive.com" + path + "?cursor=" + payload.decode(), path, "false"
        )


def test_public_pagination_rejects_encoded_secret_before_checkpoint_or_log(context):
    payload = json.dumps({"apiKey": KEY}).encode().hex()
    calls = []

    def response(call):
        calls.append(str(call.url))
        body = aggregates(context[2])
        body["next_url"] = (
            "https://api.massive.com"
            + MassiveProvider.aggregate_path(context[2])
            + "?cursor="
            + payload
        )
        return httpx.Response(200, json=body)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("httpx")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with pytest.raises(ProviderError, match="UNSAFE_CONTINUATION"):
            download(provider(context, response), context[2], context[1], context[4])
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    with closing(sqlite3.connect(context[4].path)) as db:
        assert db.execute("SELECT count(*) FROM md_pages").fetchone()[0] == 0
    captured = stream.getvalue()
    assert len(calls) == 1 and all(payload not in call and KEY not in call for call in calls)
    assert payload not in captured and KEY not in captured


def test_transport_logs_redact_all_query_values_and_error_body_credentials(context):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("httpx")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    adapter = provider(context, attempts=1)
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(500, content=("apiKey=" + KEY).encode())
    )
    try:
        with pytest.raises(ProviderError, match="SERVER_FAILURE"):
            adapter._request("/probe", {"date": "fictional-sensitive-query"}, "ticker_metadata")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    captured = stream.getvalue()
    assert KEY not in captured and "fictional-sensitive-query" not in captured
    assert "[REDACTED_QUERY]" in captured


@pytest.mark.parametrize(
    "body",
    [
        b'{"status":"OK","adjusted":true,"adjusted":false,"results":[]}',
        b'{"status":"OK","status":"OK","results":[]}',
        b'{"status":"OK","results":{"ticker":"AAPL","ticker":"AAPL"}}',
        b'{"status":"OK","results":[{"o":1,"o":1}]}',
        b'{"status":"OK","next_url":"one","next_url":"two","results":[]}',
        b'{"status":"OK","results":{"nested":{"x":1,"x":1}}}',
    ],
)
def test_duplicate_json_keys_at_any_depth_are_rejected(context, body):
    adapter = provider(context, attempts=1)
    adapter.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    with pytest.raises(ProviderError, match="INVALID_JSON"):
        adapter._request("/probe", {}, "ticker_metadata")


def test_unique_json_preserves_high_precision_decimal(context):
    value = "245.12345678901234567890123456789"
    body = ('{"status":"OK","results":{"price":' + value + "}}").encode()
    adapter = provider(context, attempts=1)
    adapter.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    parsed, _ = adapter._request("/probe", {}, "ticker_metadata")
    assert parsed["results"]["price"] == Decimal(value)


class DeadlineStream(httpx.SyncByteStream):
    def __init__(self, chunks, advance):
        self.chunks, self.advance = chunks, advance
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.advance()
            yield chunk

    def close(self):
        self.closed = True


def test_raw_stream_progress_enforces_total_deadline(context):
    elapsed = [0]
    read_timeouts = []
    streams = []
    raw = b'{"status":"OK","results":{}}'

    response_obj = httpx.Response(200, stream=DeadlineStream([], lambda: None))

    def handler(call):
        def advance():
            read_timeouts.append(call.extensions["timeout"]["read"])
            elapsed[0] += 10

        stream = DeadlineStream([bytes([byte]) for byte in raw], advance)
        streams.append(stream)
        response_obj.stream = stream
        return response_obj

    adapter = provider(context, attempts=1, monotonic=lambda: elapsed[0])
    adapter.transport = httpx.MockTransport(handler)
    with pytest.raises(ProviderError, match="NETWORK_FAILURE"):
        adapter._request("/probe", {}, "ticker_metadata")
    assert elapsed[0] == 30 and streams[0].closed
    assert read_timeouts == [15, 15, 10]


def test_slow_response_headers_enforce_total_deadline(context):
    elapsed = [0]

    def response(_):
        elapsed[0] = 31
        return httpx.Response(200, content=b'{"status":"OK","results":{}}')

    adapter = provider(context, attempts=1, monotonic=lambda: elapsed[0])
    adapter.transport = httpx.MockTransport(response)
    with pytest.raises(ProviderError, match="NETWORK_FAILURE"):
        adapter._request("/probe", {}, "ticker_metadata")


def test_compressed_response_refused_without_decompression(context):
    touched = []
    stream = DeadlineStream([b"not-a-real-compressed-bomb"], lambda: touched.append(True))
    adapter = provider(context, attempts=1)
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream)
    )
    with pytest.raises(ProviderError, match="COMPRESSED_RESPONSE_REFUSED"):
        adapter._request("/probe", {}, "ticker_metadata")
    assert not touched and stream.closed


def test_stream_cancellation_closes_resource_and_charges_attempt(context):
    class Cancelled(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            raise KeyboardInterrupt
            yield b""  # pragma: no cover

        def close(self):
            self.closed = True

    stream = Cancelled()
    adapter = provider(context, attempts=1)
    adapter.transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    with pytest.raises(KeyboardInterrupt):
        adapter._request("/probe", {}, "ticker_metadata")
    assert stream.closed and len(context[3].audit(None)) == 1


def test_raw_stream_heartbeats_preserve_job_lease(context):
    raw = b'{"status":"OK","results":{}}'
    stream = DeadlineStream([raw[:10], raw[10:]], lambda: None)
    beats = []
    adapter = provider(context, attempts=1)
    adapter.set_heartbeat(lambda: beats.append(True))
    adapter.transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    adapter._request("/probe", {}, "ticker_metadata")
    assert len(beats) >= 3 and stream.closed


@pytest.mark.parametrize(
    "retry_after,server_date,client_offset,expected",
    [
        ("70", None, 0, 70),
        ("Tue, 27 Oct 2026 12:01:30 GMT", "Tue, 27 Oct 2026 12:00:00 GMT", 0, 91),
        ("Tue, 27 Oct 2026 12:01:30 GMT", "Tue, 27 Oct 2026 12:00:00 GMT", 300, None),
        ("Tue, 27 Oct 2026 12:01:30 GMT", "Tue, 27 Oct 2026 12:00:00 GMT", -300, None),
        ("Tue, 27 Oct 2026 12:01:00 GMT", None, 0, 61),
        ("Tue, 27 Oct 2026 12:01:30 GMT", "malformed", 0, None),
        ("malformed", "Tue, 27 Oct 2026 12:00:00 GMT", 0, 60),
    ],
)
def test_retry_after_policy_handles_clock_skew(
    context, retry_after, server_date, client_offset, expected
):
    base = datetime(2026, 10, 27, 12, tzinfo=UTC)
    adapter = provider(context)
    adapter.clock = lambda: base + timedelta(seconds=client_offset)
    headers = {"Retry-After": retry_after}
    if server_date is not None:
        headers["Date"] = server_date
    assert adapter._retry_after(httpx.Headers(headers)) == expected


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMIT"), (503, "SERVER_FAILURE")])
def test_http_date_clock_skew_persists_indefinite_review_block(context, status, code):
    adapter = provider(context, attempts=1)
    adapter.clock = lambda: datetime(2026, 10, 27, 12, 5, tzinfo=UTC)
    adapter.transport = httpx.MockTransport(
        lambda _: httpx.Response(
            status,
            headers={
                "Date": "Tue, 27 Oct 2026 12:00:00 GMT",
                "Retry-After": "Tue, 27 Oct 2026 12:01:30 GMT",
            },
        )
    )
    with pytest.raises(ProviderError, match=code):
        adapter._request("/probe", {}, "ticker_metadata")
    with pytest.raises(ProviderError, match="UNBOUNDED_RETRY"):
        MassiveQuota(context[3].path, clock=context[0], sleep=context[0].sleep).reserve(
            "minute_aggregates"
        )


def test_new_and_existing_quota_schema_validate(tmp_path):
    path = tmp_path / "quota.sqlite3"
    first = MassiveQuota(path, clock=lambda: 0, sleep=lambda _: None)
    second = MassiveQuota(path, clock=lambda: 0, sleep=lambda _: None)
    assert first.reserve("ticker_metadata")[0]
    assert second.reserve("minute_aggregates") == (None, 1.0)


@pytest.mark.parametrize("damage", ["missing-table", "missing-column", "missing-index", "state"])
def test_existing_damaged_quota_never_repairs_itself(tmp_path, damage):
    path = tmp_path / "quota.sqlite3"
    MassiveQuota(path, clock=lambda: 0, sleep=lambda _: None)
    with closing(sqlite3.connect(path)) as db:
        if damage == "missing-table":
            db.execute("DROP TABLE massive_attempts")
        elif damage == "missing-column":
            db.execute("ALTER TABLE massive_attempts RENAME TO damaged_attempts")
            db.execute("CREATE TABLE massive_attempts(token TEXT PRIMARY KEY)")
        elif damage == "missing-index":
            db.execute("DROP INDEX massive_singleflight")
        else:
            db.execute("UPDATE massive_state SET version=2")
        db.commit()
    with pytest.raises(ProviderError, match="MASSIVE_QUOTA_.*REVIEW_REQUIRED"):
        MassiveQuota(path, clock=lambda: 49, sleep=lambda _: None)


def test_interrupted_empty_quota_file_requires_administrative_recovery(tmp_path):
    path = tmp_path / "quota.sqlite3"
    path.touch()
    with pytest.raises(ProviderError, match="HISTORY_MISSING_REVIEW_REQUIRED"):
        MassiveQuota(path, clock=lambda: 0, sleep=lambda _: None)
    assert path.exists() and path.stat().st_size == 0


def test_restart_after_four_attempts_cannot_issue_fifth(tmp_path):
    clock = Clock()
    path = tmp_path / "quota.sqlite3"
    quota = MassiveQuota(path, clock=clock, sleep=clock.sleep)
    for _ in range(4):
        token = quota.acquire("ticker_metadata")
        quota.finish(token)
    clock.value -= 15
    with pytest.raises(ProviderError, match="CLOCK_MOVED_BACKWARDS"):
        MassiveQuota(path, clock=clock, sleep=clock.sleep).reserve("minute_aggregates")
