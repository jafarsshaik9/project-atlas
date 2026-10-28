"""Original synthetic HTTP responses only: planning, resume, quota and process ownership."""

import json
import socket
import subprocess
import sys
from datetime import UTC, date, datetime

import httpx
import pytest

from atlas.config import AtlasError, Settings
from atlas.main import main
from atlas.market_data.massive import MassiveProvider
from atlas.market_data.massive_limits import MassiveQuota
from atlas.market_data.store import HistoricalStore
from atlas.research_collection import (
    CollectionState,
    collect,
    exclusive_worker,
    inventory,
    plan_collection,
    rolling_window,
    verify_plan,
    write_manifest,
)

AS_OF = datetime(2026, 10, 28, 12, tzinfo=UTC)
DAY = date(2026, 10, 26)


class Clock:
    def __init__(self):
        self.value = AS_OF.timestamp()

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds

    def utc(self):
        return datetime.fromtimestamp(self.value, UTC)


def harness(tmp_path, *, fail_second=False, missing=False, paginate=True):
    manifest = plan_collection(AS_OF, first=DAY, last=DAY, symbols=("AAPL",))
    path = tmp_path / "manifest.json"
    write_manifest(path, manifest)
    clock = Clock()
    quota = MassiveQuota(tmp_path / "quota.sqlite3", clock=clock, sleep=clock.sleep)
    state = CollectionState(tmp_path / "collection.sqlite3")
    store = HistoricalStore(tmp_path / "history.sqlite3", clock=clock.utc)
    calls = []
    failures = [fail_second]

    def response(request):
        calls.append((clock(), request.url.path, dict(request.url.params)))
        if request.url.path.startswith("/v3/reference"):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "results": dict(
                        ticker="AAPL",
                        locale="us",
                        market="stocks",
                        type="CS",
                        active=True,
                        currency_name="usd",
                        primary_exchange="XNAS",
                        composite_figi="BBG000FAKE01",
                    ),
                },
            )
        start = int(request.url.path.split("/")[-2])
        offset = 200 if request.url.params.get("cursor") else 0
        if offset and failures[0]:
            failures[0] = False
            raise httpx.ConnectError("synthetic interruption", request=request)
        stop = 390 if offset or not paginate else 200
        values = [
            dict(t=start + i * 60000, o=100, h=101, l=99, c=100, v=1000)
            for i in range(offset, stop)
            if not (missing and i == 10)
        ]
        body = dict(
            ticker="AAPL",
            adjusted=False,
            status="OK",
            resultsCount=len(values),
            queryCount=len(values),
            results=values,
        )
        if stop == 200:
            body["next_url"] = "https://api.massive.com" + request.url.path + "?cursor=UEFHRQ=="
        return httpx.Response(200, json=body)

    provider = MassiveProvider(
        quota,
        allow_network=True,
        access_reference="OFFLINE_FIXTURE_ONLY",
        key="fictional_massive_key_never_sent_123456",
        attempts=1,
        transport=httpx.MockTransport(response),
        clock=clock.utc,
        monotonic=clock,
    )
    return manifest, path, clock, quota, state, store, provider, calls


def test_rolling_two_year_plan_has_full_request_manifest_and_early_closes():
    result = plan_collection(AS_OF)
    assert result["first"] == "2024-10-28" and result["last"] == "2026-10-27"
    assert result["sessions_per_symbol"] == 501
    assert len(result["tasks"]) == 2505 and result["expected_rows"] == 972450
    assert result["request_count_without_retries"] == 5010
    assert {t["expected_minutes"] for t in result["tasks"]} == {210, 390}
    assert all(len(t["requests"]) == 2 for t in result["tasks"])
    assert all(t["requests"][1]["params"]["adjusted"] == "false" for t in result["tasks"])
    assert verify_plan(result) == result


def test_et_midnight_and_leap_year_eligibility():
    assert rolling_window(datetime(2026, 10, 28, 3, 59, tzinfo=UTC))[1] == date(2026, 10, 26)
    assert rolling_window(datetime(2026, 10, 28, 4, 0, tzinfo=UTC))[1] == date(2026, 10, 27)
    assert rolling_window(datetime(2024, 2, 29, 12, tzinfo=UTC))[0] == date(2022, 2, 28)


@pytest.mark.parametrize(
    "first,last", [(date(2024, 10, 27), DAY), (DAY, date(2026, 10, 28)), (DAY, date(2026, 10, 25))]
)
def test_plan_rejects_ineligible_or_reversed_dates(first, last):
    with pytest.raises(AtlasError):
        plan_collection(AS_OF, first=first, last=last)


def test_manifest_is_immutable_and_detects_request_and_window_tampering(tmp_path):
    result = plan_collection(AS_OF, first=DAY, last=DAY, symbols=("AAPL",))
    path = tmp_path / "manifest.json"
    write_manifest(path, result)
    with pytest.raises(FileExistsError):
        write_manifest(path, result)
    from atlas.market_data.contracts import digest

    result["tasks"][0]["start"] = "2026-10-26T13:29:00+00:00"
    result["manifest_id"] = digest({k: v for k, v in result.items() if k != "manifest_id"})
    with pytest.raises(AtlasError, match="task/calendar"):
        verify_plan(result)


def test_real_adapter_collection_paginates_and_deduplicates(tmp_path):
    _, path, _, _, state, store, provider, calls = harness(tmp_path)
    result = collect(path, provider, store, state)
    assert len(calls) == 3 and result[0]["status"] == "ACCEPTED"
    identity = result[0]["dataset_id"]
    assert len(store.inspect(identity)[2]) == 390
    again = collect(path, provider, store, state)
    assert again[0]["dataset_id"] == identity and len(calls) == 3
    assert store.inspect(identity)[0]["action_coverage"] == "UNKNOWN"


def test_interrupt_resume_retains_pages_without_refetch(tmp_path):
    _, path, clock, quota, state, store, provider, calls = harness(tmp_path, fail_second=True)
    with pytest.raises(AtlasError):
        collect(path, provider, store, state)
    row = state.results()[0]
    assert row["status"] == "INTERRUPTED" and store.job(row["job_id"])["page_count"] == 1
    reopened = CollectionState(state.path)
    collect(path, provider, store, reopened)
    assert len(calls) == 4
    assert calls[-1][2]["cursor"] == "UEFHRQ=="
    assert reopened.results()[0]["status"] == "ACCEPTED"
    times = [r[0] for r in calls]
    assert all(sum(t - 60 < s <= t for s in times) <= 4 for t in times)
    assert clock() == quota.clock()


def test_adapter_retries_share_the_same_four_attempt_window(tmp_path):
    _, path, _, _, state, store, provider, calls = harness(tmp_path, fail_second=True)
    provider.attempts = 3
    collect(path, provider, store, state)
    times = [row[0] for row in calls]
    assert len(times) == 4 and state.results()[0]["status"] == "ACCEPTED"
    assert all(sum(t - 60 < s <= t for s in times) <= 4 for t in times)


def test_missing_bars_quarantined_without_filling_gaps(tmp_path):
    _, path, _, _, state, store, provider, _ = harness(tmp_path, missing=True, paginate=False)
    row = collect(path, provider, store, state)[0]
    assert row["status"] == "QUARANTINED"
    manifest, quality, bars = store.inspect(row["dataset_id"])
    assert len(bars) == 389 and manifest["state"] == "QUARANTINED"
    assert any(f.code == "MISSING_INTERVALS" for f in quality.findings)


def test_inventory_reuses_exact_accepted_session(tmp_path):
    _, path, _, _, state, store, provider, calls = harness(tmp_path, paginate=False)
    collect(path, provider, store, state)
    found, jobs = inventory([store.path])
    assert len(found) == 1 and jobs[0]["status"] == "COMPLETED"
    plan = plan_collection(AS_OF, first=DAY, last=DAY, symbols=("AAPL",), stores=[store.path])
    assert plan["request_count_without_retries"] == 0
    next_path = tmp_path / "reused.json"
    write_manifest(next_path, plan)
    other = CollectionState(tmp_path / "other.sqlite3")
    collect(next_path, provider, store, other)
    assert other.results()[0]["status"] == "REUSED" and len(calls) == 2


def test_shared_persistent_limiter_counts_every_attempt_and_uncertain_owner(tmp_path):
    clock = Clock()
    path = tmp_path / "quota.sqlite3"
    a = MassiveQuota(path, clock=clock, sleep=clock.sleep)
    b = MassiveQuota(path, clock=clock, sleep=clock.sleep)
    times = []
    for index in range(12):
        token = (a if index % 2 else b).acquire("minute_aggregates")
        times.append(clock())
        a.finish(token, status=500 if index % 3 == 0 else 200)
    assert all(sum(t - 60 < s <= t for s in times) <= 4 for t in times)
    clock.sleep(61)
    token, _ = a.reserve("ticker_metadata")
    assert token and b.reserve("minute_aggregates")[0] is None
    clock.sleep(10000)
    assert b.reserve("minute_aggregates")[0] is None


def test_only_one_collection_worker_across_processes_and_death_releases_lock(tmp_path):
    path = tmp_path / "worker.lock"
    script = """
import sys
from atlas.research_collection import exclusive_worker
with exclusive_worker(sys.argv[1]): print('OWNED',flush=True)
"""
    with exclusive_worker(path):
        child = subprocess.run(
            [sys.executable, "-c", script, str(path)], capture_output=True, timeout=20
        )
        assert child.returncode != 0 and b"Another collection worker" in child.stderr
    child = subprocess.run(
        [sys.executable, "-c", script, str(path)], capture_output=True, timeout=20
    )
    assert child.returncode == 0
    crash_script = script.replace("print('OWNED',flush=True)", "__import__('os')._exit(73)")
    child = subprocess.run(
        [sys.executable, "-c", crash_script, str(path)], capture_output=True, timeout=20
    )
    assert child.returncode == 73
    with exclusive_worker(path):
        pass


def test_crash_after_immutable_publication_before_collection_checkpoint_is_idempotent(
    tmp_path, monkeypatch
):
    _, path, _, _, state, store, provider, calls = harness(tmp_path, paginate=False)
    original = state.checkpoint

    def crash(task_id, **values):
        if values.get("status") == "ACCEPTED":
            raise RuntimeError("process died after publication")
        return original(task_id, **values)

    with monkeypatch.context() as m:
        m.setattr(state, "checkpoint", crash)
        with pytest.raises(RuntimeError):
            collect(path, provider, store, state)
    assert len(calls) == 2
    collect(path, provider, store, state)
    assert len(calls) == 2 and state.results()[0]["status"] == "ACCEPTED"


def test_resume_provider_configuration_drift_cannot_create_a_second_import(tmp_path):
    _, path, _, _, state, store, provider, calls = harness(tmp_path, paginate=False)
    collect(path, provider, store, state)
    provider.descriptor = provider.descriptor.model_copy(
        update={"access_reference": "DIFFERENT_APPROVAL"}
    )
    with pytest.raises(AtlasError, match="policy drift"):
        collect(path, provider, store, state)
    assert len(calls) == 2


def test_no_authorization_cannot_load_credentials_or_send_http(tmp_path):
    _, path, _, _, state, store, provider, _ = harness(tmp_path)
    provider.transport = None
    provider._key = None
    provider._loader = lambda **kw: pytest.fail("Credential access")
    with pytest.raises(AtlasError, match="not authorized"):
        collect(path, provider, store, state)


def test_dry_run_cli_has_no_network_or_credentials(tmp_path, monkeypatch, capsys):
    def forbidden(*a, **k):
        pytest.fail("Network or credential access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Settings, "from_environment", forbidden)
    assert (
        main(
            [
                "research-history",
                "plan",
                "--provider",
                "massive",
                "--as-of",
                AS_OF.isoformat(),
                "--first",
                str(DAY),
                "--last",
                str(DAY),
                "--output",
                str(tmp_path / "plan.json"),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["request_count_without_retries"] == 10
