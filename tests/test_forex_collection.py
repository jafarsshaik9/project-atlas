"""Phase 6B-1 offline fixtures; never load credentials or contact a provider."""

import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from test_forex_oanda import CONFIG, Clock, instrument, payload

from atlas.config import AtlasError
from atlas.forex import collection_cli
from atlas.forex.access import Quota, approval_record, phrase
from atlas.forex.collection import (
    START,
    CollectionLedger,
    approval_phrase,
    approve,
    collection_permit,
    iter_dataset,
    make_plan,
    metadata,
    report,
    run_collection,
    validate_plan,
    worker_lock,
)
from atlas.forex.dataset import export_dataset, reload_dataset
from atlas.forex.history import ForexStore, windows
from atlas.forex.models import CandlePlan, parse_candles
from atlas.forex.provider import OandaReader, ReadOnlyTransport
from atlas.forex.quality import closure, evaluate, open_minutes
from atlas.market_data.contracts import canonical, digest

NOW = datetime(2024, 11, 5, 0, 1, 43, tzinfo=UTC)
SYMBOLS = ("EUR_USD", "GBP_USD", "USD_JPY")


def build(tmp_path, **changes):
    return make_plan(
        tuple(instrument(s) for s in SYMBOLS),
        "live",
        tmp_path / "corpus",
        **({"now": NOW, "origin": "ARTIFICIAL_OFFLINE_FIXTURE"} | changes),
    )


def resign(plan):
    plan["plan_hash"] = digest({k: v for k, v in plan.items() if k != "plan_hash"})
    return plan


class Reader:
    def __init__(self, fail_at=None, empty=False):
        self.calls = []
        self.fail_at = fail_at
        self.empty = empty

    def candles(self, plan, start, end):
        self.calls.append((plan.instrument.name, start, end))
        if len(self.calls) == self.fail_at:
            raise AtlasError("OFFLINE_INTERRUPTION")
        data = payload(start=start, count=0 if self.empty or not open_minutes(start, end) else 1)
        data["instrument"] = plan.instrument.name
        return parse_candles(data, plan, start, end)


def run(plan, reader=None, **changes):
    return run_collection(
        plan, reader or Reader(), lock_path=plan["output"] + "-worker.sqlite3", **changes
    )


def test_frozen_window_and_five_days_for_each_pair(tmp_path):
    a = build(tmp_path)
    assert validate_plan(a) == a
    assert a["effective_end"] == "2024-11-05T00:01:00+00:00"
    assert a["requested_end"] == "2026-10-29T00:00:00+00:00"
    assert len(a["partitions"]) == 15
    assert a["initial_requests"] == 30 and a["request_cap"] == 90
    assert {p["candle_plan"]["instrument"]["name"] for p in a["partitions"]} == set(SYMBOLS)
    assert a["partitions"][-1]["candle_plan"]["end"] == "2024-11-02T00:00:00Z"
    b = build(tmp_path, stage="B", pilot=a, now=NOW + timedelta(days=20))
    assert validate_plan(b) == b and b["effective_end"] == a["effective_end"]
    assert len(b["partitions"]) == 27
    assert {r["key"] for r in a["partitions"]} <= {r["key"] for r in b["partitions"]}
    assert a["plan_hash"] != b["plan_hash"]


def test_future_end_cap_and_full_target(tmp_path):
    a = build(tmp_path, now=datetime(2026, 10, 30, tzinfo=UTC))
    b = build(tmp_path, now=datetime(2026, 10, 30, tzinfo=UTC), stage="B", pilot=a)
    assert b["effective_end"] == "2026-10-29T00:00:00+00:00"
    assert len(b["partitions"]) == 2193
    assert b["initial_requests"] == 4386
    assert all(r["requests"] == 2 for r in b["partitions"])
    validate_plan(b)


@pytest.mark.parametrize("change", ["missing", "duplicate", "reorder", "cap", "end", "components"])
def test_rehashed_inconsistent_plans_are_rejected(tmp_path, change):
    a = build(tmp_path)
    if change == "missing":
        a["partitions"].pop()
    elif change == "duplicate":
        a["partitions"][1] = a["partitions"][0]
    elif change == "reorder":
        a["partitions"].reverse()
    elif change == "cap":
        a["request_cap"] += 1
    elif change == "end":
        a["effective_end"] = "2026-11-01T00:00:00+00:00"
    else:
        a["components"] = "M"
    with pytest.raises(AtlasError, match="PLAN_INVALID"):
        validate_plan(resign(a))


def test_discovery_metadata_requires_all_three_accessible_pairs():
    evidence = {
        "status": "READ_ONLY_DISCOVERY_COMPLETE",
        "discovery": {"configured_account_accessible": True},
        "instruments": [instrument(s).model_dump(mode="json") for s in SYMBOLS],
    }
    assert len(metadata(evidence)) == 3
    evidence["instruments"].pop()
    with pytest.raises(AtlasError):
        metadata(evidence)
    evidence["discovery"]["configured_account_accessible"] = False
    with pytest.raises(AtlasError):
        metadata(evidence)


def test_stage_separation_and_explicit_confirmation(tmp_path):
    a = build(tmp_path)
    b = build(tmp_path, stage="B", pilot=a)
    with pytest.raises(AtlasError, match="CONFIRMATION"):
        approve(CONFIG, a, "yes", now=1)
    record = approve(CONFIG, a, approval_phrase(a), now=1)
    assert collection_permit(CONFIG, a, record, now=2).maximum_attempts == 90
    with pytest.raises(AtlasError):
        collection_permit(CONFIG, b, record, now=2)
    with pytest.raises(AtlasError, match="Stage A"):
        approve(CONFIG, b, approval_phrase(b), now=2)
    result = run(a)
    b_record = approve(CONFIG, b, approval_phrase(b), now=2, pilot_report=result)
    collection_permit(CONFIG, b, b_record, now=3, pilot_report=result)
    changed = result | {"checksum": "changed"}
    with pytest.raises(AtlasError):
        collection_permit(CONFIG, b, b_record, now=3, pilot_report=changed)


@pytest.mark.parametrize("field", ["environment", "output", "request_cap", "expires", "plan_hash"])
def test_modified_or_expired_approval_refused(tmp_path, field):
    a = build(tmp_path)
    record = approve(CONFIG, a, approval_phrase(a), now=1)
    with pytest.raises(AtlasError, match="APPROVAL"):
        collection_permit(CONFIG, a, record, now=86401)
    record[field] = "tampered"
    with pytest.raises(AtlasError, match="APPROVAL"):
        collection_permit(CONFIG, a, record, now=2)


def test_discovery_approval_cannot_authorize_collection(tmp_path):
    a = build(tmp_path)
    record = approval_record(CONFIG, "accounts", phrase(CONFIG.environment, "accounts"), now=1)
    with pytest.raises(AtlasError):
        collection_permit(CONFIG, a, record, now=2)


def test_renewal_preserves_total_budget_identity(tmp_path):
    a = build(tmp_path)
    first = approve(CONFIG, a, approval_phrase(a), now=1)
    renewed = approve(CONFIG, a, approval_phrase(a), now=100)
    assert first["budget_id"] == renewed["budget_id"]
    assert first["signature"] != renewed["signature"]


@pytest.mark.parametrize("mutation", ["date", "symbol", "component", "method", "host"])
def test_transport_enforces_exact_approved_windows_before_http(tmp_path, mutation):
    a = build(tmp_path)
    clock = Clock()
    record = approve(CONFIG, a, approval_phrase(a), now=clock())
    permit = collection_permit(CONFIG, a, record, now=clock())
    quota = Quota(tmp_path / "quota.sqlite3", clock=clock, sleep=clock.sleep)
    sent = []
    transport = ReadOnlyTransport(
        CONFIG, permit, quota, inner=httpx.MockTransport(lambda r: sent.append(r))
    )
    p = CandlePlan.model_validate(a["partitions"][0]["candle_plan"])
    start, end = next(windows(p))
    params = dict(
        granularity="M1",
        price="MBA",
        smooth="false",
        includeFirst="true",
        **{"from": start.isoformat(), "to": end.isoformat()},
    )
    path = "/v3/instruments/EUR_USD/candles"
    host = "https://api-fxtrade.oanda.com"
    method = "GET"
    if mutation == "date":
        params["to"] = (end + timedelta(minutes=1)).isoformat()
    elif mutation == "symbol":
        path = "/v3/instruments/AUD_USD/candles"
    elif mutation == "component":
        params["price"] = "M"
    elif mutation == "method":
        method = "POST"
    else:
        host = "https://api-fxpractice.oanda.com"
    with pytest.raises(AtlasError):
        transport.handle_request(httpx.Request(method, host + path, params=params))
    assert not sent and quota.count(permit) == 0
    transport.close()


def test_approved_mock_transport_and_persistent_budget(tmp_path):
    a = build(tmp_path)
    clock = Clock()
    record = approve(CONFIG, a, approval_phrase(a), now=clock())
    permit = collection_permit(CONFIG, a, record, now=clock())
    quota = Quota(tmp_path / "quota.sqlite3", clock=clock, sleep=clock.sleep)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + CONFIG.token
        return httpx.Response(200, json=payload(start=START, count=1))

    transport = ReadOnlyTransport(CONFIG, permit, quota, inner=httpx.MockTransport(handler))
    p = CandlePlan.model_validate(a["partitions"][0]["candle_plan"])
    start, end = next(windows(p))
    assert len(OandaReader(transport).candles(p, start, end)) == 1
    other = Quota(tmp_path / "quota.sqlite3", clock=clock, sleep=clock.sleep)
    assert other.count(permit) == 1 and len(calls) == 1
    # A lower permit cap cannot replenish the same persistent approval budget.
    from dataclasses import replace

    capped = replace(permit, maximum_attempts=1)
    with pytest.raises(AtlasError):
        other.acquire(capped)
    transport.close()


def test_stage_a_reused_in_b_and_restart_is_request_free(tmp_path):
    a = build(tmp_path)
    events = []
    reader = Reader()
    pilot = run(a, reader, progress=events.append)
    assert pilot["status"] == "COMPLETE_REVIEW_REQUIRED" and len(reader.calls) == 30
    assert pilot["counts"] == {"ACCEPTED": 15}
    assert events[-1]["remaining_partitions"] == 0
    b = build(tmp_path, stage="B", pilot=a)
    second = Reader()
    result = run(b, second)
    assert len(second.calls) == b["initial_requests"] - a["initial_requests"]
    assert result["counts"]["OBSERVED_EMPTY_EXPECTED_CLOSED"] == 3
    third = Reader()
    assert run(b, third) == result and not third.calls
    assert list(iter_dataset(b))[0]["origin"] == "ARTIFICIAL_OFFLINE_FIXTURE"


def test_resume_after_partial_pagination(tmp_path):
    a = build(tmp_path)
    interrupted = Reader(fail_at=2)
    with pytest.raises(AtlasError, match="INTERRUPTION"):
        run(a, interrupted)
    recovered = Reader()
    run(a, recovered)
    assert len(recovered.calls) == a["initial_requests"] - 1
    assert recovered.calls[0][1] == START + timedelta(hours=12)


def test_crash_after_dataset_commit_before_collection_checkpoint(tmp_path):
    a = build(tmp_path)

    def crash():
        raise RuntimeError("offline crash")

    first = Reader()
    with pytest.raises(RuntimeError):
        run(a, first, checkpoint_hook=crash)
    assert len(first.calls) == 2
    second = Reader()
    run(a, second)
    assert len(second.calls) == a["initial_requests"] - 2


def test_crash_before_page_commit_cannot_publish_duplicate_data(tmp_path):
    a = build(tmp_path)
    p = CandlePlan.model_validate(a["partitions"][0]["candle_plan"])
    store = ForexStore(tmp_path / "isolated.sqlite3")

    def crash():
        raise RuntimeError("before commit")

    with pytest.raises(RuntimeError):
        store.collect(p, Reader(), checkpoint_hook=crash)
    reader = Reader()
    identity = store.collect(p, reader)
    assert len(reader.calls) == 2 and len(store.inspect(identity)[1]) == 2


def test_unexpected_empty_partition_quarantines_and_resume_stops(tmp_path):
    a = build(tmp_path)
    with pytest.raises(AtlasError, match="QUARANTINED"):
        run(a, Reader(empty=True))
    assert report(a)["counts"]["QUARANTINED"] == 1
    second = Reader()
    with pytest.raises(AtlasError, match="QUARANTINED"):
        run(a, second)
    assert not second.calls


def test_overlap_conflict_preserves_accepted_partition(tmp_path):
    a = build(tmp_path)
    run(a)
    changed = build(tmp_path, page_minutes=1440)
    reader = Reader()
    with pytest.raises(AtlasError, match="OVERLAPPING"):
        run(changed, reader)
    assert not reader.calls and report(a)["counts"] == {"ACCEPTED": 15}


@pytest.mark.parametrize("kind", ["page", "checkpoint", "projection", "dataset_link"])
def test_corruption_stops_reuse(tmp_path, kind):
    a = build(tmp_path)
    run(a)
    ledger = CollectionLedger(a["output"])
    key = a["partitions"][0]["key"]
    if kind == "page":
        with ledger.store.connect() as db:
            db.execute("UPDATE fx_pages SET checksum='corrupt' WHERE job=?", (key,))
    else:
        with ledger.connect() as db:
            if kind == "projection":
                db.execute("UPDATE partitions SET symbol='GBP_USD' WHERE key=?", (key,))
            elif kind == "checkpoint":
                db.execute("UPDATE partitions SET checksum='corrupt' WHERE key=?", (key,))
            else:
                value = ledger.get(key)
                value["dataset"] = ledger.get(a["partitions"][1]["key"])["dataset"]
                db.execute(
                    "UPDATE partitions SET record=?,checksum=? WHERE key=?",
                    (canonical(value), digest(value), key),
                )
    reader = Reader()
    with pytest.raises(AtlasError):
        run(a, reader)
    assert not reader.calls


def test_single_worker_lock_releases_after_interruption(tmp_path):
    path = tmp_path / "worker.sqlite3"
    with worker_lock(path):
        with pytest.raises(AtlasError, match="WORKER_BUSY"):
            with worker_lock(path):
                pytest.fail("Second worker entered")
    with worker_lock(path):
        pass


def test_low_disk_fails_before_reader(tmp_path, monkeypatch):
    from collections import namedtuple

    import atlas.forex.collection as module

    monkeypatch.setattr(module.shutil, "disk_usage", lambda p: namedtuple("Disk", "free")(0))
    reader = Reader()
    with pytest.raises(AtlasError, match="disk space"):
        run(build(tmp_path), reader)
    assert not reader.calls


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2025-03-08T15:00:00+00:00", "EXPECTED_WEEKEND_CLOSURE_ASSUMED"),
        ("2025-03-09T21:04:00+00:00", "EXPECTED_WEEKEND_CLOSURE_ASSUMED"),
        ("2025-03-09T21:05:00+00:00", None),
        ("2025-03-02T22:05:00+00:00", None),
        ("2025-11-02T22:04:00+00:00", "EXPECTED_WEEKEND_CLOSURE_ASSUMED"),
        ("2025-11-02T22:05:00+00:00", None),
        ("2025-03-10T20:59:00+00:00", "EXPECTED_DAILY_BREAK_ASSUMED"),
        ("2025-03-10T21:05:00+00:00", None),
    ],
)
def test_published_closure_assumptions_follow_new_york_dst(stamp, expected):
    assert closure(datetime.fromisoformat(stamp)) == expected


def test_spreads_missing_minutes_and_decimal_values(tmp_path):
    a = build(tmp_path)
    p = CandlePlan.model_validate(a["partitions"][0]["candle_plan"])
    bars = Reader().candles(p, p.start, p.start + timedelta(hours=12))
    result = evaluate(p, bars)
    assert result["spread_by_utc_hour"]["0"]["sum"] == "1.0"
    assert result["counts"]["UNEXPLAINED_MISSING_MINUTE"] > 1400
    assert result["counts"]["EXPECTED_DAILY_BREAK_ASSUMED"] == 6
    assert not result["critical"] and bars[0].bid.c == Decimal("1.10003")
    assert result["availability"] == "UNKNOWN_ORIGINAL_PUBLICATION_TIMES"


def test_unusual_volatility_and_crossed_endpoints_flagged_not_discarded(tmp_path):
    a = build(tmp_path)
    p = CandlePlan.model_validate(a["partitions"][0]["candle_plan"])
    data = payload(start=p.start)
    data["candles"][0]["mid"]["h"] = "1.2"
    data["candles"][1]["bid"] = dict(o="1.2", h="1.2", l="1.2", c="1.2")
    bars = parse_candles(data, p, p.start, p.end)
    result = evaluate(p, bars)
    assert result["warnings"]["RANGE_OVER_100_PIPS_REVIEW"] == 1
    assert result["warnings"]["ASYNC_ENDPOINT_CROSS_REVIEW"] == 1
    assert not result["critical"]


def test_duplicate_candles_rejected_by_contract(tmp_path):
    p = CandlePlan.model_validate(build(tmp_path)["partitions"][0]["candle_plan"])
    data = payload(start=p.start)
    data["candles"][1] = copy.deepcopy(data["candles"][0])
    with pytest.raises(AtlasError, match="CONTRACT"):
        parse_candles(data, p, p.start, p.end)


def test_export_reload_roundtrip_retains_hashes_and_rejects_truncation(tmp_path):
    a = build(tmp_path)
    run(a)
    target = tmp_path / "export.jsonl"
    export_dataset(a, target)
    loaded = list(reload_dataset(target))
    assert len(loaded) == 15 and sum(len(i[3]) for i in loaded) == 30
    assert all(i[0] == digest(i[1]) for i in loaded)
    assert loaded[0][1]["plan"]["instrument"]["pip_location"] == -4
    with pytest.raises(FileExistsError):
        export_dataset(a, target)
    lines = target.read_text().splitlines()
    target.write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(AtlasError):
        list(reload_dataset(target))


@pytest.mark.parametrize("mutation", ["price", "duplicate", "trailer", "oversize"])
def test_export_tampering_rejected(tmp_path, mutation):
    a = build(tmp_path)
    run(a)
    target = tmp_path / "export.jsonl"
    export_dataset(a, target)
    lines = target.read_text().splitlines()
    if mutation == "price":
        value = json.loads(lines[2])
        value["value"]["bid"]["c"] = "1.10004"
        lines[2] = canonical(value)
    elif mutation == "duplicate":
        lines[3] = lines[2]
    elif mutation == "trailer":
        lines[-1] = canonical({"type": "end_archive", "partitions": 1, "chain": "changed"})
    else:
        lines[0] = "x" * 1_000_001
    target.write_text("\n".join(lines) + "\n")
    with pytest.raises(AtlasError):
        list(reload_dataset(target))


def test_cli_dry_run_never_loads_credentials_or_transport(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(collection_cli, "ROOT", tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    a = build(local)
    path = local / "plan.json"
    path.write_text(canonical(a))

    def forbidden(*a, **kw):
        pytest.fail("Dry run attempted credential or network access")

    monkeypatch.setattr(collection_cli, "load_config", forbidden)
    monkeypatch.setattr(collection_cli, "ReadOnlyTransport", forbidden)
    assert collection_cli.main(["collect", "--plan", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "DRY_RUN" and result["authenticated_attempts"] == 0


def test_cli_stage_b_requires_real_completed_pilot_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(collection_cli, "ROOT", tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    a = build(local)
    b = build(local, stage="B", pilot=a)
    path = local / "pilot.json"
    path.write_text(canonical(a))
    with pytest.raises(AtlasError, match="requires"):
        collection_cli.pilot_evidence(b, None)
    with pytest.raises(AtlasError, match="incomplete"):
        collection_cli.pilot_evidence(b, path)
    run(a)
    result = collection_cli.pilot_evidence(b, path)
    assert result["plan_hash"] == a["plan_hash"]


def test_cli_refuses_paths_outside_local(tmp_path, monkeypatch):
    monkeypatch.setattr(collection_cli, "ROOT", tmp_path)
    with pytest.raises(AtlasError):
        collection_cli.private_path(tmp_path / "tracked.json")


def test_unapproved_execution_stops_before_credentials_or_transport(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(collection_cli, "ROOT", tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    a = build(local, origin="EXTERNAL_HISTORICAL_MARKET_DATA")
    path = local / "plan.json"
    path.write_text(canonical(a))

    def forbidden(*args, **kwargs):
        pytest.fail("Unapproved execution accessed credentials/transport")

    monkeypatch.setattr(collection_cli, "load_config", forbidden)
    monkeypatch.setattr(collection_cli, "ReadOnlyTransport", forbidden)
    assert collection_cli.main(["collect", "--execute", "--plan", str(path)]) == 1
    assert "SEPARATE_COLLECTION_APPROVAL_REQUIRED" in capsys.readouterr().out


def test_jpy_pip_conversion_and_precision_warning_preserve_source(tmp_path):
    p = CandlePlan.model_validate(build(tmp_path)["partitions"][2]["candle_plan"])
    data = payload(start=p.start, count=1)
    data["instrument"] = "USD_JPY"
    row = data["candles"][0]
    for name, value in (("bid", "150.001"), ("ask", "150.011"), ("mid", "150.0061")):
        row[name] = dict.fromkeys(("o", "h", "l", "c"), value)
    bars = parse_candles(data, p, p.start, p.end)
    result = evaluate(p, bars)
    assert Decimal(result["spread_by_utc_hour"]["0"]["sum"]) == Decimal(1)
    assert result["warnings"]["PRECISION_BEYOND_DISPLAY_METADATA_REVIEW"] == 1
    assert bars[0].mid.c == Decimal("150.0061") and not result["critical"]
