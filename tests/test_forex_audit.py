"""Audit regression cases use artificial sources and no credentials."""

import gzip
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_forex_collection import build, run

from atlas.config import AtlasError
from atlas.forex.audit import gap_candidate, quantiles, scan
from atlas.forex.audit_calendar import LONDON, classification, near_dst, session
from atlas.forex.audit_details import enrich
from atlas.forex.collection import CollectionLedger, report
from atlas.forex.dataset import partitions
from atlas.forex.history import ForexStore
from atlas.market_data.contracts import digest


def test_read_only_missing_store_never_creates_files(tmp_path):
    path = tmp_path / "absent" / "historical.sqlite3"
    with pytest.raises(AtlasError, match="NOT_FOUND"):
        ForexStore(path, read_only=True)
    assert not path.parent.exists()
    plan = build(tmp_path)
    with pytest.raises(AtlasError, match="NOT_FOUND"):
        report(plan)
    with pytest.raises(AtlasError, match="NOT_FOUND"):
        list(partitions(plan))
    assert not Path(plan["output"]).exists()


def test_read_only_connections_reject_mutation(tmp_path):
    ledger = CollectionLedger(tmp_path)
    readonly = CollectionLedger(tmp_path, read_only=True)
    for store, table in ((readonly, "partitions"), (readonly.store, "fx_jobs")):
        with store.connect() as db, pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute(f"DELETE FROM {table}")
    assert ledger.get("absent") is None


def test_audit_validates_full_scope_and_never_promotes_to_execution_ready(tmp_path):
    a = build(tmp_path)
    b = build(tmp_path, stage="B", pilot=a)
    run(b)
    out = tmp_path / "audit"
    out.mkdir()
    result = scan(b, out)
    assert result["source_integrity"] == "PASS"
    assert result["realistic_backtest_ready"] is False
    assert sum(result["counts"].values()) == len(b["partitions"])
    assert result["bars"] > 0
    assert all(s["gap_candidates"] for s in result["symbols"].values())
    assert len((out / "partitions.jsonl").read_text().splitlines()) == len(b["partitions"])


def test_audit_refuses_corrupted_page(tmp_path):
    a = build(tmp_path)
    b = build(tmp_path, stage="B", pilot=a)
    run(b)
    ledger = CollectionLedger(b["output"])
    with ledger.store.connect() as db:
        db.execute("UPDATE fx_pages SET content='[]' WHERE rowid=1")
    out = tmp_path / "audit"
    out.mkdir()
    with pytest.raises(AtlasError, match="INTEGRITY"):
        scan(b, out)


def test_audit_refuses_saved_quality_tamper(tmp_path):
    a = build(tmp_path)
    b = build(tmp_path, stage="B", pilot=a)
    run(b)
    ledger = CollectionLedger(b["output"])
    with ledger.connect() as db:
        key, record = db.execute("SELECT key,record FROM partitions LIMIT 1").fetchone()
        value = json.loads(record)
        value["quality"]["counts"] = {}
        db.execute("UPDATE partitions SET record=? WHERE key=?", (json.dumps(value), key))
    out = tmp_path / "audit"
    out.mkdir()
    with pytest.raises(AtlasError, match="CORRUPT"):
        scan(b, out)


def test_exact_weighted_quantiles_and_unverified_gap_hypotheses():
    stats = quantiles({"1.1": 95, "9.9": 5})
    assert stats["p50"] == stats["p95"] == "1.1"
    assert stats["p99"] == "9.9"
    assert quantiles({}) == {}
    # 17:05 New York in summer and winter; UTC offset must change with DST.
    for month, hour in ((7, 21), (11, 22)):
        a = datetime(2025, month, 10, hour, 5, tzinfo=UTC)
        assert gap_candidate(a, a) == "ROLLOVER_REOPEN_CANDIDATE_UNVERIFIED"
    a = datetime(2025, 12, 25, 12, tzinfo=UTC)
    assert gap_candidate(a, a) == "HOLIDAY_CANDIDATE_UNVERIFIED"


@pytest.mark.parametrize("stamp", ["2025-07-07T20:59:00+00:00", "2025-11-03T22:04:00+00:00"])
def test_v2_observed_boundary_is_review_not_trading_permission(stamp):
    t = datetime.fromisoformat(stamp)
    assert classification(t, observed=True) == "OBSERVED_BOUNDARY_REVIEW_NOT_CONFIRMED_TRADABLE"
    assert "ASSUMED" in classification(t, observed=False)


def test_v2_holidays_are_narrow_and_evidence_qualified():
    assert classification(datetime(2025, 12, 25, 12, tzinfo=UTC), observed=False) == (
        "HOLIDAY_CLOSURE_SUPPORTED_NOT_ACCOUNT_VERIFIED"
    )
    assert (
        classification(datetime(2025, 12, 24, 12, tzinfo=UTC), observed=False)
        == "UNRESOLVED_ABSENCE"
    )
    assert (
        classification(datetime(2025, 12, 25, 22, 5, tzinfo=UTC), observed=False)
        == "UNRESOLVED_ABSENCE"
    )
    assert classification(datetime(2025, 12, 25, 12, tzinfo=UTC), observed=True) == "OBSERVED"


def test_research_session_overlap_tracks_us_uk_dst_mismatch():
    assert session(datetime(2025, 3, 17, 12, 30, tzinfo=UTC)) == "OVERLAP"
    assert session(datetime(2025, 2, 17, 12, 30, tzinfo=UTC)) == "LONDON_ONLY"
    assert near_dst(datetime(2025, 3, 30, 12, tzinfo=UTC), LONDON)


def test_enrichment_missing_minutes_have_brackets_and_reconcile(tmp_path):
    a = build(tmp_path)
    b = build(tmp_path, stage="B", pilot=a)
    run(b)
    out = tmp_path / "audit"
    out.mkdir()
    primary = scan(b, out)
    primary["checksum"] = digest(primary)
    details = enrich(b, primary, out)
    count = 0
    with gzip.open(out / "missing-minutes.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            t = datetime.fromisoformat(record["utc"])
            if record["previous"]:
                assert datetime.fromisoformat(record["previous"]["start"]) < t
            if record["next"]:
                assert datetime.fromisoformat(record["next"]["start"]) > t
            count += 1
    assert count == sum(
        s["missing"]["UNEXPLAINED_MISSING_MINUTE"] for s in primary["symbols"].values()
    )
    assert sum(sum(s["month"].values()) for s in details["missing_summary"].values()) == count
    assert details["source_unchanged"]
    assert all("p90" in s["ALL"] for s in details["spread_pips"].values())
