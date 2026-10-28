"""Explicit gaps never become accepted data or permission to execute."""

import json
from datetime import timedelta

import httpx
import pytest

from atlas import alpaca_collection_cli as cli
from atlas.alpaca_collection import plan_alpaca
from atlas.alpaca_exclusions import load_exclusion
from atlas.config import AtlasError
from atlas.market_data.alpaca import AlpacaProvider
from atlas.market_data.calendars import RecordedCalendar
from atlas.market_data.contracts import digest
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore
from tests.test_alpaca import DAY, NOW, bars
from tests.test_alpaca_iex import setup_iex


def prepare(tmp_path, monkeypatch, *, later_empty=False):
    gap_day = str(DAY - timedelta(days=1))

    def handler(request):
        day = request.url.params["start"][:10]
        task = next(t for t in manifest["tasks"] if t["date"] == day)
        req = cli.partition_request(task, "AAPL")
        return httpx.Response(
            200,
            json={
                "bars": {}
                if day == gap_day or (later_empty and day == str(DAY))
                else {s: bars(req, count=5) for s in task["symbols"]}
            },
        )

    _, plan, provider, _, quota = setup_iex(tmp_path, monkeypatch, handler)
    manifest = plan_alpaca(NOW, first=DAY - timedelta(days=4), last=DAY, feed="iex")
    plan["manifest_id"] = manifest["manifest_id"]
    plan.pop("run_id")
    plan["run_id"] = digest(plan)
    with pytest.raises(AtlasError, match="NO_VALID_BARS"):
        cli.run_collection(manifest, plan, provider)
    evidence_store = HistoricalStore(tmp_path / "local/evidence.sqlite3")
    evidence_provider = AlpacaProvider(
        feed="iex",
        symbols=manifest["symbols"],
        quota=quota,
        cache=tmp_path / "local/evidence-pages.sqlite3",
        credentials=lambda: ("FAKE-KEY", "FAKE-SECRET"),
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    task = next(t for t in manifest["tasks"] if t["date"] == gap_day)
    refs = {}
    for symbol in task["symbols"]:
        identity, _, _ = download(
            evidence_provider,
            cli.partition_request(task, symbol),
            RecordedCalendar(task["calendar"]),
            evidence_store,
        )
        proof, _, _ = evidence_store.inspect(identity)
        refs[symbol] = {
            "store": str(evidence_store.path),
            "dataset_id": identity,
            "checksum": digest(proof),
        }
    record = {
        "schema": 1,
        "repository": str(tmp_path.resolve()),
        "manifest_id": manifest["manifest_id"],
        "run_id": plan["run_id"],
        "feed": "iex",
        "date": gap_day,
        "evidence": refs,
        "approval": "USER_APPROVED_SESSION_EXCLUSION_ONLY",
        "execution_authorized": False,
        "reason": "REPEATED_EMPTY_PROVIDER_RESPONSE",
        "approved_at": NOW.isoformat(),
        "approval_reference": "synthetic explicit confirmation",
    }
    path = tmp_path / "local/exclusion.json"
    write_record(path, record)
    return manifest, plan, provider, quota, path


def write_record(path, record):
    record.pop("record_id", None)
    record["record_id"] = digest(record)
    path.write_text(json.dumps(record))


def test_exclusion_preserves_data_skips_only_reviewed_date_and_resumes(tmp_path, monkeypatch):
    manifest, plan, provider, quota, path = prepare(tmp_path, monkeypatch)
    original = cli.CollectionState(plan["state"]).results()
    accepted = {r["id"]: r for r in original if r["status"] == "ACCEPTED"}
    quarantined = next(r for r in original if r["status"] == "QUARANTINED")
    attempts = quota.attempt_count()
    events = []
    results = cli.run_collection(
        manifest, plan, provider, exclusion_record=path, progress=events.append
    )
    assert quota.attempt_count() == attempts + 1  # Only the following session is fetched.
    assert len(results) == 15
    assert sum(r["state"] == "EXCLUDED" for r in results) == 5
    assert events[-1]["completed_sessions"] == 2 and events[-1]["excluded_sessions"] == 1
    assert events[-1]["remaining_sessions"] == 0 and not events[-1]["coverage_complete"]
    rows = {r["id"]: r for r in cli.CollectionState(plan["state"]).results()}
    assert all(rows[k] == r for k, r in accepted.items())
    assert rows[quarantined["id"]]["status"] == "QUARANTINED"
    assert rows[quarantined["id"]]["dataset_id"] == quarantined["dataset_id"]
    assert rows["run-" + plan["run_id"]]["status"] == "COMPLETED_WITH_GAPS"
    with pytest.raises(AtlasError, match="cannot be selected"):
        HistoricalStore(plan["store"], read_only=True).visible_bars(quarantined["dataset_id"], NOW)
    assert cli.run_collection(manifest, plan, provider, exclusion_record=path) == results
    assert quota.attempt_count() == attempts + 1
    with pytest.raises(AtlasError, match="same approved"):
        cli.run_collection(manifest, plan, provider)
    assert quota.attempt_count() == attempts + 1


@pytest.mark.parametrize("fault", ["feed", "date", "run", "evidence", "execution"])
def test_exclusion_rejects_changed_scope_or_evidence(tmp_path, monkeypatch, fault):
    manifest, plan, provider, quota, path = prepare(tmp_path, monkeypatch)
    record = json.loads(path.read_text())
    if fault == "feed":
        record["feed"] = "sip"
    elif fault == "date":
        record["date"] = str(DAY)
    elif fault == "run":
        record["run_id"] = "different"
    elif fault == "execution":
        record["execution_authorized"] = True
    else:
        record["evidence"]["AAPL"]["checksum"] = "changed"
    write_record(path, record)
    attempts = quota.attempt_count()
    with pytest.raises(AtlasError):
        cli.run_collection(manifest, plan, provider, exclusion_record=path)
    assert quota.attempt_count() == attempts


def test_new_quarantine_still_stops_collection(tmp_path, monkeypatch):
    manifest, plan, provider, quota, path = prepare(tmp_path, monkeypatch, later_empty=True)
    with pytest.raises(AtlasError, match=f"{DAY}/AAPL; failures=NO_VALID_BARS"):
        cli.run_collection(manifest, plan, provider, exclusion_record=path)


def test_crash_after_exclusion_checkpoint_recovers_without_repeating_http(tmp_path, monkeypatch):
    manifest, plan, provider, quota, path = prepare(tmp_path, monkeypatch)
    checkpoint = cli.CollectionState.checkpoint
    tripped = False

    def crash(self, task_id, **values):
        nonlocal tripped
        checkpoint(self, task_id, **values)
        if values.get("status") == "EXCLUDED" and not tripped:
            tripped = True
            raise RuntimeError("synthetic crash after commit")

    monkeypatch.setattr(cli.CollectionState, "checkpoint", crash)
    attempts = quota.attempt_count()
    with pytest.raises(RuntimeError):
        cli.run_collection(manifest, plan, provider, exclusion_record=path)
    assert quota.attempt_count() == attempts
    results = cli.run_collection(manifest, plan, provider, exclusion_record=path)
    assert len(results) == 15 and quota.attempt_count() == attempts + 1


def test_exclusion_dry_run_does_not_enable_execution(tmp_path, monkeypatch, capsys):
    manifest, plan, provider, quota, path = prepare(tmp_path, monkeypatch)
    mp, rp = tmp_path / "local/manifest.json", tmp_path / "local/plan.json"
    mp.write_text(json.dumps(manifest))
    rp.write_text(json.dumps(plan))
    args = ["--manifest", str(mp), "--run-plan", str(rp), "--exclusion-record", str(path)]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["approved_excluded_dates"] == [str(DAY - timedelta(days=1))]
    assert not result["network_enabled"] and not result["coverage_complete"]
    attempts = quota.attempt_count()
    assert cli.main([*args, "--execute", "--resume"]) == 1
    assert "IEX_EXECUTION_NOT_APPROVED" in capsys.readouterr().err
    assert quota.attempt_count() == attempts
    assert load_exclusion(path, manifest, plan)["execution_authorized"] is False
