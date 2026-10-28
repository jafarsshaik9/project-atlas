"""Full-collection readiness with native HTTP disabled and synthetic credentials only."""

import json
import shutil
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest

from atlas import alpaca_collection_cli as cli
from atlas.alpaca_collection import plan_alpaca
from atlas.config import AtlasError
from atlas.market_data.alpaca import UNIVERSE, retry_delay
from atlas.market_data.contracts import digest
from atlas.market_data.providers import ProviderError
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore
from atlas.research_collection import exclusive_worker
from atlas.research_history_cli import main
from tests.test_alpaca import DAY, NOW, bars, setup


def prepare(tmp_path, monkeypatch, handler, *, reuse=None):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "DEFAULT_QUOTA", tmp_path / "local/quota.sqlite3")
    (tmp_path / "src/atlas").mkdir(parents=True)
    (tmp_path / "src/atlas/main.py").touch()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="project-atlas"\n')
    provider, request, calendar, quota, timer = setup(tmp_path / "local", handler, symbols=UNIVERSE)
    manifest = plan_alpaca(NOW, first=DAY, last=DAY)
    plan = {
        "schema": 1,
        "repository": str(tmp_path.resolve()),
        "manifest_id": manifest["manifest_id"],
        "store": str(tmp_path / "local/full/historical.sqlite3"),
        "state": str(tmp_path / "local/full/collection.sqlite3"),
        "cache": str(provider.cache),
        "reuse": reuse or {},
    }
    plan["run_id"] = digest(plan)
    return manifest, plan, provider, request, calendar, quota, timer


def pages(request):
    data = bars(request)
    return [
        {"bars": {}, "next_page_token": "one"},
        {"bars": {"AAPL": data[:200]}, "next_page_token": "two"},
        {"bars": {"AAPL": data[200:], "AMZN": data[:50]}, "next_page_token": "three"},
        {"bars": {"AMZN": data[50:], "GOOGL": data}, "next_page_token": "four"},
        {"bars": {"MSFT": data[:100]}, "next_page_token": "five"},
        {"bars": {"MSFT": data[100:], "NVDA": data}, "next_page_token": None},
    ]


def test_all_five_uneven_empty_pages_progress_and_restart(tmp_path, monkeypatch):
    calls, events = [], []

    def handler(req):
        index = [None, "one", "two", "three", "four", "five"].index(
            req.url.params.get("page_token")
        )
        calls.append(index)
        return httpx.Response(200, json=pages(request)[index])

    manifest, plan, provider, request, _, quota, _ = prepare(tmp_path, monkeypatch, handler)
    result = cli.run_collection(manifest, plan, provider, progress=events.append)
    assert len(result) == 5 and all(r["state"] == "ACCEPTED" for r in result)
    assert calls == list(range(6)) and quota.attempt_count() == 6
    assert events[-1]["completed_sessions"] == 1
    assert events[-1]["remaining_sessions"] == 0 and events[-1]["completed_symbols"] == 5
    assert events[-1]["bars_downloaded"] == 1950
    assert events[-1]["accepted_partitions"] == 5 and events[-1]["quarantined_partitions"] == 0
    assert events[-1]["estimated_remaining_seconds"] == 0
    again = cli.run_collection(manifest, plan, provider, progress=events.append)
    assert [r["dataset_id"] for r in result] == [r["dataset_id"] for r in again]
    assert quota.attempt_count() == 6


def test_partial_pagination_restart_does_not_redownload_committed_pages(tmp_path, monkeypatch):
    calls = []

    def handler(req):
        index = [None, "one", "two", "three", "four", "five"].index(
            req.url.params.get("page_token")
        )
        calls.append(index)
        if len(calls) == 3:
            raise httpx.ReadTimeout("synthetic transport interruption")
        return httpx.Response(200, json=pages(request)[index])

    manifest, plan, provider, request, _, quota, _ = prepare(tmp_path, monkeypatch, handler)
    with pytest.raises(ProviderError, match="TRANSPORT_FAILURE"):
        cli.run_collection(manifest, plan, provider)
    result = cli.run_collection(manifest, plan, provider)
    assert len(result) == 5 and calls == [0, 1, 2, 2, 3, 4, 5]
    assert quota.attempt_count() == 7


@pytest.mark.parametrize("fault", ["missing_symbol", "partial", "duplicate", "repeated_token"])
def test_invalid_multi_symbol_sessions_stop_or_quarantine(tmp_path, monkeypatch, fault):
    calls = []

    def handler(req):
        calls.append(req.url.params.get("page_token"))
        body = {"bars": {s: bars(request) for s in UNIVERSE}}
        if fault == "missing_symbol":
            del body["bars"]["AMZN"]
        elif fault == "partial":
            body["bars"]["AMZN"].pop(10)
        elif fault == "duplicate":
            body["bars"]["AAPL"].insert(10, body["bars"]["AAPL"][10])
        else:
            body["next_page_token"] = "same"
        return httpx.Response(200, json=body)

    manifest, plan, provider, request, _, quota, _ = prepare(tmp_path, monkeypatch, handler)
    with pytest.raises(AtlasError):
        cli.run_collection(manifest, plan, provider)
    if fault == "repeated_token":
        assert calls == [None, "same"]
    else:
        state = cli.CollectionState(plan["state"]).results()
        assert any(r["status"] == "QUARANTINED" for r in state)
        with pytest.raises(AtlasError, match="quarantined"):
            cli.run_collection(manifest, plan, provider)
        assert quota.attempt_count() == 1


def test_reuse_accepted_alpaca_partition_read_only(tmp_path, monkeypatch):
    def handler(_):
        return httpx.Response(200, json={"bars": {s: bars(request) for s in UNIVERSE}})

    manifest, plan, provider, request, calendar, quota, _ = prepare(tmp_path, monkeypatch, handler)
    source = HistoricalStore(tmp_path / "local/source.sqlite3", clock=lambda: NOW)
    identity = download(provider, request, calendar, source)[0]
    before = (tmp_path / "local/source.sqlite3").read_bytes()
    plan["reuse"] = cli.reuse_inventory([source.path], manifest)
    plan.pop("run_id")
    plan["run_id"] = digest(plan)
    result = cli.run_collection(manifest, plan, provider)
    assert result[0]["state"] == "REUSED" and result[0]["dataset_id"] == identity
    assert result[0]["store"] == str(source.path)
    assert (tmp_path / "local/source.sqlite3").read_bytes() == before
    assert quota.attempt_count() == 1  # Batch pages also survived reuse planning.


@pytest.mark.parametrize("header", ["7", "http-date", "past-date"])
def test_retry_after_numeric_and_http_date(tmp_path, header):
    calls = []
    value = (
        format_datetime(NOW + timedelta(seconds=7), usegmt=True)
        if header == "http-date"
        else header
    )
    if header == "past-date":
        value = format_datetime(NOW - timedelta(seconds=10), usegmt=True)

    def handler(_):
        calls.append(True)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": value})
        return httpx.Response(200, json={"bars": {"AAPL": bars(request)}})

    provider, request, _, quota, timer = setup(tmp_path, handler)
    provider.fetch_page(request, None)
    assert quota.attempt_count() == 2
    if header != "past-date":
        assert timer() - NOW.timestamp() >= 7


@pytest.mark.parametrize("header", ["90000", "long-http-date"])
def test_long_delay_persisted_and_does_not_sleep_or_retry(tmp_path, header):
    value = (
        format_datetime(NOW + timedelta(seconds=90000), usegmt=True)
        if header == "long-http-date"
        else header
    )
    provider, request, _, quota, timer = setup(
        tmp_path, lambda _: httpx.Response(429, headers={"Retry-After": value})
    )
    with pytest.raises(ProviderError, match="LONG_RETRY"):
        provider.fetch_page(request, None)
    assert quota.attempt_count() == 1
    assert timer() == NOW.timestamp()
    with quota.connect() as db:
        assert db.execute("SELECT blocked FROM state").fetchone()[0] >= NOW.timestamp() + 90000
    with pytest.raises(AtlasError, match="later resume"):
        quota.acquire()


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "invalid-date"])
def test_invalid_retry_after_is_fail_closed(value):
    with pytest.raises(ProviderError, match="INVALID_RETRY_AFTER"):
        retry_delay(value, NOW)


def test_cli_dry_run_and_execution_gate_do_not_load_credentials(tmp_path, monkeypatch, capsys):
    manifest, plan, provider, request, _, quota, _ = prepare(
        tmp_path, monkeypatch, lambda _: pytest.fail("Network is forbidden")
    )
    manifest_path = tmp_path / "local/manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    plan_path = tmp_path / "local/run.json"
    monkeypatch.setattr(
        cli, "AlpacaProvider", lambda **kw: pytest.fail("Provider constructed before approval")
    )
    args = ["collect", "--manifest", str(manifest_path), "--run-plan", str(plan_path)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["account_permissions"] == "UNVERIFIED"
    assert main([*args, "--execute"]) == 1
    assert "ACCOUNT_PERMISSIONS_UNVERIFIED" in capsys.readouterr().err
    assert quota.attempt_count() == 0
    assert not (tmp_path / "local/market-data/alpaca-full/collection.sqlite3").exists()


def permission(manifest, plan):
    return {
        "schema": 1,
        "repository": str(cli.ROOT.resolve()),
        "manifest_id": manifest["manifest_id"],
        "run_id": plan["run_id"],
        "feed": "sip",
        "adjustment": "raw",
        "account_permissions": "USER_CONFIRMED_CHECKED",
        "confirmation": cli.CONFIRMATION,
        "confirmed_at": NOW.isoformat(),
        "evidence_reference": "Synthetic test confirmation only",
    }


@pytest.mark.parametrize("fault", ["unverified", "wrong_run", "stale", "unknown_field"])
def test_permission_record_requires_separate_bound_confirmation(tmp_path, monkeypatch, fault):
    manifest, plan, *_ = prepare(tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"))
    record = permission(manifest, plan)
    if fault == "unverified":
        record["account_permissions"] = "UNVERIFIED"
    elif fault == "wrong_run":
        record["run_id"] = "0" * 64
    elif fault == "stale":
        record["confirmed_at"] = (NOW - timedelta(days=31)).isoformat()
    else:
        record["bypass"] = True
    path = tmp_path / "local/permission.json"
    path.write_text(json.dumps(record))
    with pytest.raises(AtlasError):
        cli.check_permission(path, manifest, plan, NOW)


def test_permission_record_valid_only_when_explicit_and_bound(tmp_path, monkeypatch):
    manifest, plan, *_ = prepare(tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"))
    path = tmp_path / "local/permission.json"
    path.write_text(json.dumps(permission(manifest, plan)))
    cli.check_permission(path, manifest, plan, NOW)


def test_disk_lock_manifest_and_provider_gates(tmp_path, monkeypatch):
    manifest, plan, provider, *_ = prepare(tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"))
    monkeypatch.setattr(cli.shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(100, 99, 1))
    with pytest.raises(AtlasError, match="Insufficient"):
        cli.preflight(manifest, plan, NOW)
    with exclusive_worker(tmp_path / "local/market-data/alpaca-collection.lock"):
        with pytest.raises(AtlasError, match="worker"):
            cli.run_collection(manifest, plan, provider)
    changed = dict(manifest, expected_minute_bars=1)
    with pytest.raises(AtlasError, match="manifest"):
        cli.run_collection(changed, plan, provider)


def test_native_transport_cannot_bypass_permission_gate(tmp_path, monkeypatch):
    manifest, plan, provider, *_ = prepare(tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"))
    provider.transport = None
    with pytest.raises(AtlasError, match="ACCOUNT_PERMISSIONS_UNVERIFIED"):
        cli.run_collection(manifest, plan, provider)


def test_full_progress_counts_automatic_retries(tmp_path, monkeypatch):
    calls, events = [], []

    def handler(_):
        calls.append(True)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"bars": {s: bars(request) for s in UNIVERSE}})

    manifest, plan, provider, request, _, quota, _ = prepare(tmp_path, monkeypatch, handler)
    cli.run_collection(manifest, plan, provider, progress=events.append)
    assert events[-1]["retries"] == 1
    assert events[-1]["api_requests_consumed"] == quota.attempt_count() == 2


def test_crash_after_dataset_commit_before_coordinator_checkpoint(tmp_path, monkeypatch):
    def handler(_):
        return httpx.Response(200, json={"bars": {s: bars(request) for s in UNIVERSE}})

    manifest, plan, provider, request, _, quota, _ = prepare(tmp_path, monkeypatch, handler)
    original = cli.CollectionState.checkpoint
    crashed = []

    def checkpoint(self, task, **values):
        if "job_id" in values and not crashed:
            crashed.append(True)
            raise OSError("Synthetic crash after historical publication")
        return original(self, task, **values)

    monkeypatch.setattr(cli.CollectionState, "checkpoint", checkpoint)
    with pytest.raises(OSError):
        cli.run_collection(manifest, plan, provider)
    result = cli.run_collection(manifest, plan, provider)
    assert len(result) == 5 and quota.attempt_count() == 1
    with HistoricalStore(plan["store"], read_only=True).connect() as db:
        assert db.execute("SELECT count(*) FROM md_datasets").fetchone()[0] == 5


def test_non_adjacent_pagination_cycle_is_rejected(tmp_path, monkeypatch):
    calls = []

    def handler(req):
        token = req.url.params.get("page_token")
        calls.append(token)
        return httpx.Response(
            200, json={"bars": {}, "next_page_token": "two" if token == "one" else "one"}
        )

    manifest, plan, provider, _, _, quota, _ = prepare(tmp_path, monkeypatch, handler)
    with pytest.raises(ProviderError, match="PAGINATION_LOOP"):
        cli.run_collection(manifest, plan, provider)
    assert calls == [None, "one", "two"] and quota.attempt_count() == 3


@pytest.mark.parametrize("count", [10000, 10001])
def test_total_native_response_limit_across_symbols(tmp_path, count):
    provider, request, _, _, _ = setup(
        tmp_path,
        lambda _: httpx.Response(
            200,
            json={
                "bars": {
                    "AAPL": [bars(request)[0]] * (count // 2),
                    "MSFT": [bars(request)[0]] * (count - count // 2),
                }
            },
        ),
        symbols=UNIVERSE,
    )
    if count > 10000:
        with pytest.raises(ProviderError, match="INVALID_HISTORICAL_PAYLOAD"):
            provider.fetch_page(request, None)
    else:
        assert len(provider.fetch_page(request, None).body["values"]) == 5000


def test_foreign_cache_and_foreign_coordinator_fail_closed(tmp_path, monkeypatch):
    manifest, plan, provider, *_ = prepare(tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"))
    state = cli.CollectionState(plan["state"])
    with state.connect() as db:
        db.execute(
            "INSERT INTO collection_tasks(id,definition,status) VALUES('run-other','{}','RUNNING')"
        )
    with pytest.raises(AtlasError, match="another frozen"):
        cli.run_collection(manifest, plan, provider)


def test_reuse_rejects_massive_provider(tmp_path, monkeypatch):
    from tests.test_historical_backtest import build_dataset

    manifest, *_ = prepare(tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"))
    # An otherwise accepted fixture is also ineligible: only Alpaca is reusable.
    store, _, _ = build_dataset(tmp_path / "local")
    with pytest.raises(AtlasError, match="provider mixing"):
        cli.reuse_inventory([store.path], manifest)


@pytest.mark.parametrize("status", [401, 403])
def test_full_collection_stops_on_authentication_or_entitlement(tmp_path, monkeypatch, status):
    manifest, plan, provider, _, _, quota, _ = prepare(
        tmp_path, monkeypatch, lambda _: httpx.Response(status)
    )
    with pytest.raises(ProviderError, match="AUTHENTICATION_OR_SIP_ACCESS_DENIED"):
        cli.run_collection(manifest, plan, provider)
    assert quota.attempt_count() == 1
    assert any(r["status"] == "INTERRUPTED" for r in cli.CollectionState(plan["state"]).results())


def test_cache_cannot_be_a_foreign_database(tmp_path):
    import sqlite3

    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(AtlasError, match="Foreign Alpaca page-cache"):
        setup(tmp_path, lambda _: pytest.fail("HTTP"))
