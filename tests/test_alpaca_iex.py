"""IEX preparation only: mock HTTP, disposable confirmations, no real account requests."""

import json
from datetime import timedelta

import httpx
import pytest

from atlas import alpaca_collection_cli as cli
from atlas import alpaca_iex_approval as approval
from atlas.alpaca_collection import plan_alpaca
from atlas.config import AtlasError
from atlas.historical_backtest import run_historical_backtest
from atlas.market_data.alpaca import UNIVERSE, AlpacaProvider, instrument
from atlas.market_data.alpaca_limits import AlpacaQuota
from atlas.market_data.contracts import DownloadRequest, digest
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore
from atlas.research_history_cli import main
from tests.test_alpaca import DAY, NOW, Timer, bars
from tests.test_historical_backtest import configuration


def setup_iex(tmp_path, monkeypatch, handler, *, mode="full"):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "DEFAULT_QUOTA", tmp_path / "local/quota.sqlite3")
    (tmp_path / "src/atlas").mkdir(parents=True)
    (tmp_path / "src/atlas/main.py").touch()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="project-atlas"\n')
    symbols = ("AAPL",) if mode == "pilot" else UNIVERSE
    manifest = plan_alpaca(NOW, first=DAY, last=DAY, symbols=symbols, feed="iex")
    base = tmp_path / f"local/market-data/alpaca-iex/{mode}"
    plan = {
        "schema": 2,
        "feed": "iex",
        "mode": mode,
        "repository": str(tmp_path.resolve()),
        "manifest_id": manifest["manifest_id"],
        "store": str(base / "historical.sqlite3"),
        "state": str(base / "collection.sqlite3"),
        "cache": str(base / "pages.sqlite3"),
        "reuse": {},
    }
    plan["run_id"] = digest(plan)
    timer = Timer()
    quota = AlpacaQuota(cli.DEFAULT_QUOTA, clock=timer, sleep=timer.sleep)
    provider = AlpacaProvider(
        feed="iex",
        symbols=symbols,
        quota=quota,
        cache=plan["cache"],
        credentials=lambda: ("FAKE-KEY", "FAKE-SECRET"),
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    task = manifest["tasks"][0]
    req = cli.partition_request(task, "AAPL")
    evidence = tmp_path / "local/research/two-year/alpaca-personal-use-evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        json.dumps(
            {
                "provider": "alpaca",
                "personal_use_permission": {
                    "status": "SUPPORT_CONFIRMED_PERSONAL_NONCOMMERCIAL_ONLY"
                },
                "historical_sip_entitlement": {"status": "UNCONFIRMED_FOR_FULL_COLLECTION"},
                "execution_approval": {"status": "NOT_GRANTED"},
            }
        )
    )
    return manifest, plan, provider, req, quota


def test_iex_parameters_and_sparse_data_are_explicit(tmp_path, monkeypatch):
    def handler(request):
        assert request.url.params["feed"] == "iex"
        assert (
            request.url.params["timeframe"] == "1Min" and request.url.params["adjustment"] == "raw"
        )
        assert request.url.host == "data.alpaca.markets"
        return httpx.Response(200, json={"bars": {"AAPL": [bars(req)[i] for i in (0, 2, 4)]}})

    manifest, plan, provider, req, quota = setup_iex(tmp_path, monkeypatch, handler, mode="pilot")
    store = HistoricalStore(plan["store"], clock=lambda: NOW)
    from atlas.market_data.calendars import RecordedCalendar

    identity = download(provider, req, RecordedCalendar(manifest["tasks"][0]["calendar"]), store)[0]
    proof, quality, data = store.inspect(identity)
    assert proof["state"] == "ACCEPTED" and len(data) == 3
    findings = {f.code: f for f in quality.findings}
    assert findings["MISSING_INTERVALS"].severity == "WARNING"
    assert findings["MISSING_INTERVALS"].count == 387
    assert "IEX_LIMITED_COVERAGE" in findings
    assert all(b.provider == "alpaca-iex" and b.source_available_at is None for b in data)
    report = run_historical_backtest(
        store,
        identity,
        configuration(RecordedCalendar(manifest["tasks"][0]["calendar"]).sessions()[0]),
    )
    assert report.selected_rows == 3 and report.derived_strategy_bars == 0
    assert report.result.simulated_trades == 0 and not report.point_in_time_verified
    assert quota.attempt_count() == 1


@pytest.mark.parametrize("fault", ["empty", "duplicate", "invalid"])
def test_iex_still_quarantines_bad_or_empty_data(tmp_path, monkeypatch, fault):
    def handler(_):
        data = bars(req)
        if fault == "empty":
            data = []
        elif fault == "duplicate":
            data.insert(2, data[2])
        else:
            data[0]["h"] = 1
        return httpx.Response(200, json={"bars": {"AAPL": data}})

    manifest, plan, provider, req, _ = setup_iex(tmp_path, monkeypatch, handler, mode="pilot")
    from atlas.market_data.calendars import RecordedCalendar

    store = HistoricalStore(plan["store"], clock=lambda: NOW)
    identity = download(provider, req, RecordedCalendar(manifest["tasks"][0]["calendar"]), store)[0]
    assert store.inspect(identity)[0]["state"] == "QUARANTINED"


def test_iex_five_symbol_sparse_pages_restart(tmp_path, monkeypatch):
    calls = []
    symbols = sorted(UNIVERSE)

    def handler(request):
        index = int(request.url.params.get("page_token", "0"))
        calls.append(index)
        return httpx.Response(
            200,
            json={
                "bars": {symbols[index]: bars(req, count=10)},
                "next_page_token": str(index + 1) if index < 4 else None,
            },
        )

    manifest, plan, provider, req, quota = setup_iex(tmp_path, monkeypatch, handler)
    result = cli.run_collection(manifest, plan, provider)
    assert len(result) == 5 and all(r["state"] == "ACCEPTED" for r in result)
    assert calls == [0, 1, 2, 3, 4]
    assert cli.run_collection(manifest, plan, provider) == result
    assert quota.attempt_count() == 5


def test_sip_artifacts_rebuild_identically_and_are_not_iex(tmp_path, monkeypatch):
    manifest, plan, provider, req, quota = setup_iex(
        tmp_path, monkeypatch, lambda _: pytest.fail("HTTP")
    )
    sip = plan_alpaca(NOW, first=DAY, last=DAY)
    assert (
        sip["provider"] == "alpaca-sip"
        and sip["tasks"][0]["requests"][0]["parameters"]["feed"] == "sip"
    )
    assert sip == plan_alpaca(NOW, first=DAY, last=DAY, feed="sip")
    assert sip["manifest_id"] != manifest["manifest_id"]
    sip_req = DownloadRequest(
        provider="alpaca-sip",
        instrument=instrument("AAPL", req.start, req.end),
        start=req.start,
        end=req.end,
    )
    with pytest.raises(AtlasError, match="FEED_PROVENANCE_MISMATCH"):
        provider.fetch_page(sip_req, None)
    assert quota.attempt_count() == 0


def test_approval_does_not_inherit_sip_gate_or_claim_observed_access(tmp_path, monkeypatch):
    manifest, plan, _, _, quota = setup_iex(
        tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"), mode="pilot"
    )
    record = approval.make_record(
        manifest,
        plan,
        account_plan="basic",
        now=NOW,
        confirmation=f"APPROVE IEX PILOT {plan['run_id']}",
    )
    assert (
        record["personal_use_permission"]["status"]
        == "SUPPORT_CONFIRMED_PERSONAL_NONCOMMERCIAL_ONLY"
    )
    assert record["entitlement"]["observed_access"]["status"] == "NOT_YET_TESTED"
    assert record["entitlement"]["account_plan"] == "BASIC_USER_DECLARED"
    assert record["maximum_http_attempts"] == 2 and record["feed"] == "iex"
    path = tmp_path / "local/approval.json"
    path.write_text(json.dumps(record))
    cli.check_permission(path, manifest, plan, NOW)
    assert quota.attempt_count() == 0


@pytest.mark.parametrize("fault", ["sip", "scope", "expired", "unapproved"])
def test_iex_rejects_wrong_or_stale_approval(tmp_path, monkeypatch, fault):
    manifest, plan, *_ = setup_iex(
        tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"), mode="pilot"
    )
    record = approval.make_record(
        manifest,
        plan,
        account_plan="basic",
        now=NOW,
        confirmation=f"APPROVE IEX PILOT {plan['run_id']}",
    )
    if fault == "sip":
        record["feed"] = "sip"
    elif fault == "scope":
        record["scope"] = "full"
    elif fault == "expired":
        record["execution_approval"]["approved_at"] = (NOW - timedelta(days=31)).isoformat()
    else:
        record["execution_approval"]["status"] = "NOT_GRANTED"
    path = tmp_path / "local/approval.json"
    path.write_text(json.dumps(record))
    with pytest.raises(AtlasError):
        cli.check_permission(path, manifest, plan, NOW)


def test_full_iex_approval_requires_observed_iex_pilot(tmp_path, monkeypatch):
    manifest, plan, *_ = setup_iex(tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"))
    with pytest.raises(AtlasError, match="IEX_ACCOUNT_ACCESS_UNVERIFIED"):
        approval.make_record(
            manifest,
            plan,
            account_plan="basic",
            now=NOW,
            confirmation=f"APPROVE IEX FULL {plan['run_id']}",
        )


def test_pilot_approval_cli_is_interactive_and_does_not_call_provider(
    tmp_path, monkeypatch, capsys
):
    manifest, plan, _, _, quota = setup_iex(
        tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"), mode="pilot"
    )
    mp = tmp_path / "local/manifest.json"
    mp.write_text(json.dumps(manifest))
    rp = tmp_path / "local/run.json"
    rp.write_text(json.dumps(plan))
    out = tmp_path / "local/pilot-approval.json"
    args = [
        "--manifest",
        str(mp),
        "--run-plan",
        str(rp),
        "--record",
        str(out),
        "--account-plan",
        "basic",
    ]
    monkeypatch.setattr(approval.sys.stdin, "isatty", lambda: False)
    assert approval.main(args) == 1 and not out.exists()
    monkeypatch.setattr(approval.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: f"APPROVE IEX PILOT {plan['run_id']}")
    assert approval.main(args) == 0 and out.exists()
    assert quota.attempt_count() == 0
    assert json.loads(out.read_text())["scope"] == "pilot"
    capsys.readouterr()


def test_cli_dry_run_iex_cannot_use_sip_manifest_or_activate_http(tmp_path, monkeypatch, capsys):
    manifest, plan, _, _, quota = setup_iex(
        tmp_path, monkeypatch, lambda _: pytest.fail("HTTP"), mode="pilot"
    )
    mp = tmp_path / "local/manifest.json"
    mp.write_text(json.dumps(manifest))
    rp = tmp_path / "local/run.json"
    rp.write_text(json.dumps(plan))
    args = ["collect", "--feed", "iex", "--pilot", "--manifest", str(mp), "--run-plan", str(rp)]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["feed"] == "iex" and result["network_enabled"] is False
    assert result["actual_iex_access"] == "NOT_YET_TESTED"
    assert main([*args, "--execute"]) == 1
    assert "IEX_EXECUTION_NOT_APPROVED" in capsys.readouterr().err
    sip = plan_alpaca(NOW, first=DAY, last=DAY)
    mp.write_text(json.dumps(sip))
    assert main(["collect", "--feed", "iex", "--manifest", str(mp), "--run-plan", str(rp)]) == 1
    assert "conflicts" in capsys.readouterr().err
    assert quota.attempt_count() == 0


def test_iex_pilot_budget_survives_retries(tmp_path, monkeypatch):
    manifest, plan, provider, req, quota = setup_iex(
        tmp_path,
        monkeypatch,
        lambda _: httpx.Response(429, headers={"Retry-After": "0"}),
        mode="pilot",
    )
    provider.pilot = "iex-pilot-" + plan["run_id"]
    quota.start_pilot(provider.pilot)
    with pytest.raises(AtlasError, match="two-attempt"):
        cli.run_collection(manifest, plan, provider)
    assert quota.attempt_count() == 2


def test_observed_iex_pilot_unlocks_only_a_separate_full_approval(tmp_path, monkeypatch):
    def handler(_):
        return httpx.Response(200, json={"bars": {"AAPL": bars(req, count=10)}})

    manifest, plan, provider, req, quota = setup_iex(tmp_path, monkeypatch, handler, mode="pilot")
    pilot_id = "iex-pilot-" + plan["run_id"]
    quota.start_pilot(pilot_id)
    provider.pilot = pilot_id
    result = cli.run_collection(manifest, plan, provider)
    approval.record_pilot_access(manifest, plan, result, quota, pilot_id)
    assert approval.observed_pilot()["status"] == "OBSERVED_SINGLE_SESSION_ONLY"
    full = plan_alpaca(NOW, first=DAY, last=DAY, feed="iex")
    base = tmp_path / "local/market-data/alpaca-iex/full"
    fp = {
        **plan,
        "mode": "full",
        "manifest_id": full["manifest_id"],
        "store": str(base / "historical.sqlite3"),
        "state": str(base / "collection.sqlite3"),
        "cache": str(base / "pages.sqlite3"),
        "reuse": {},
    }
    fp.pop("run_id")
    fp["run_id"] = digest(fp)
    record = approval.make_record(
        full, fp, account_plan="basic", now=NOW, confirmation=f"APPROVE IEX FULL {fp['run_id']}"
    )
    assert record["scope"] == "full"
    assert record["entitlement"]["observed_access"]["status"] == "OBSERVED_SINGLE_SESSION_ONLY"
    assert quota.attempt_count() == 1
