"""Only deterministic artificial fixtures; external I/O fails immediately."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError
from test_forex_execution import META, frame

from atlas.config import AtlasError
from atlas.forex import forward_pilot as pilot
from atlas.forex.execution.calendar import window
from atlas.forex.execution.contracts import MINUTE, Run, arithmetic_context

D = Decimal
DAY = date(2026, 11, 2)
OPEN = datetime(2026, 11, 2, 8, tzinfo=UTC)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    original = pilot.ROOT
    target = tmp_path / "examples"
    target.mkdir()
    target.joinpath("forex-lean-profitability-v1.json").write_bytes(
        original.joinpath("examples/forex-lean-profitability-v1.json").read_bytes()
    )
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    import socket

    import httpx

    import atlas.forex.provider as provider

    def prohibited(*args, **kwargs):
        raise AssertionError("NETWORK_OR_BROKER_CALL_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", prohibited)
    monkeypatch.setattr(socket, "create_connection", prohibited)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", prohibited)
    monkeypatch.setattr(provider, "OandaReader", prohibited)
    monkeypatch.setattr(provider, "ReadOnlyTransport", prohibited)
    return tmp_path / "local/forex-forward-pilot/synthetic/test"


def batch(side="long", day=DAY, absent=(), missing_component=None):
    opening = datetime.combine(day, datetime.min.time(), UTC).replace(hour=8)
    received = []
    for i in range(185):
        bid = "1.10000" if i < 59 else "1.10400" if side == "long" else "1.09600"
        changes = {"EUR_USD": {"bid": bid, "ask": str(D(bid) + D(".0002"))}}
        if missing_component and i == 59:
            changes["EUR_USD"]["missing"] = missing_component
        if i not in absent:
            received.append(
                pilot.ReceivedFrame(
                    frame=frame(opening + i * MINUTE, changes),
                    collected_at=opening + (i + 1) * MINUTE,
                )
            )
    return pilot.Batch(
        origin="ARTIFICIAL_OFFLINE_FIXTURE",
        source="ARTIFICIAL_TEST",
        day=day,
        received=tuple(received),
        instruments=tuple(m.model_copy(update={"maximum_units": D(100)}) for m in META),
    )


def win(day):
    start = datetime.combine(day, datetime.min.time(), UTC)
    cfg = pilot.configuration("C0", D(1000))
    # Calendar-only artificial tests may precede this strategy's registration.
    cfg = cfg.model_copy(update={"costs": cfg.costs.model_copy(update={"known_at": start})})
    return window(
        Run(
            day=day,
            session="LONDON",
            coverage_start=start,
            coverage_end=start + 1440 * MINUTE,
            code_version="TEST",
            calendar_provenance="TEST",
            origin="ARTIFICIAL_OFFLINE_FIXTURE",
        ),
        cfg,
    )


def test_frozen_hash_and_no_synthetic_market_metrics(workspace):
    assert pilot.design()["hypothesis_count"] == 1
    assert set(pilot.status_report()["metrics"].values()) == {"NOT EVALUATED"}
    path = pilot.ROOT / "examples/forex-lean-profitability-v1.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(AtlasError, match="FROZEN_MANIFEST_CHANGED"):
        pilot.design()


@pytest.mark.parametrize(
    "day,hour",
    [
        (date(2027, 3, 26), 8),
        (date(2027, 3, 29), 7),
        (date(2026, 10, 23), 7),
        (date(2026, 10, 26), 8),
    ],
)
def test_london_dst_calendar(day, hour):
    assert win(day).opening.hour == hour


@pytest.mark.parametrize("side", ["long", "short"])
def test_next_close_fill_risk_cash_and_fee_reconciliation(workspace, side):
    result = pilot.prepare_day(batch(side), workspace)
    for cell in result["scenarios"].values():
        execution = cell["execution"]
        assert execution["signals"] == 1 and execution["entries"] == 1
        trade = execution["trade"]
        p = trade["position"]
        assert p["intent"]["side"] == side
        assert datetime.fromisoformat(p["intent"]["signal_at"]) == OPEN + 60 * MINUTE
        assert datetime.fromisoformat(p["opened_at"]) == OPEN + 61 * MINUTE
        assert D(p["planned_risk_gbp"]) <= 5 and D(p["reserved_gbp"]) <= 1000
        assert D(p["intent"]["units"]) > 0
        assert D(trade["net_gbp"]) == D(trade["gross_gbp"]) - D(p["entry_fee_gbp"]) - D(
            trade["exit_fee_gbp"]
        )
        bridge = execution["cost_bridge"]
        assert D(bridge["gross_model_pnl_gbp"]) - D(bridge["total_modeled_costs_gbp"]) == D(
            trade["net_gbp"]
        )
    assert result["market_performance"] == "NOT EVALUATED"


def test_no_future_access_at_signal(workspace):
    frames = [r.frame for r in batch().received]

    def prefix():
        yield from frames[:60]
        raise AssertionError("FUTURE_READ")

    with localcontext(arithmetic_context()):
        intent, issue = pilot.signal(
            prefix(), pilot.configuration("C0", D(1000)), win(DAY), META[0]
        )
    assert intent and not issue


@pytest.mark.parametrize("minute", [0, 20, 59, 60, 62])
def test_missing_prices_are_not_bridged(workspace, minute):
    result = pilot.prepare_day(batch(absent=(minute,)), workspace)
    for cell in result["scenarios"].values():
        execution = cell["execution"]
        if minute < 60:
            assert execution["signals"] == 0
        elif minute == 60:
            assert execution["entries"] == 0
        else:
            assert execution["invalidated"] == 1 and cell["blocked"]
            assert execution["net_pnl_gbp"] is None


def test_missing_bid_does_not_become_midpoint_fill(workspace):
    row = pilot.prepare_day(batch(missing_component="bid"), workspace)
    assert row["scenarios"]["C0"]["execution"]["signals"] == 0


def test_duplicates_and_backdating_rejected(workspace):
    b = batch()
    with pytest.raises(ValidationError, match="Duplicate"):
        pilot.Batch(**(b.model_dump() | {"received": (b.received[0], *b.received)}))
    with pytest.raises(ValidationError, match="before its end"):
        pilot.ReceivedFrame(frame=b.received[0].frame, collected_at=OPEN)
    with pytest.raises(AtlasError, match="OUTSIDE_PREREGISTERED_WINDOW"):
        pilot.prepare_day(batch(day=date(2026, 10, 30)), workspace)


def test_one_daily_attempt_and_tamper_evidence(workspace):
    row = pilot.prepare_day(batch(), workspace)
    before = (workspace / "000001.json").read_bytes()
    with pytest.raises(AtlasError, match="ALREADY_PROCESSED"):
        pilot.prepare_day(batch(), workspace)
    assert (workspace / "000001.json").read_bytes() == before
    stored = json.loads(before)
    stored["source"]["source"] = "TAMPERED"
    (workspace / "000001.json").write_text(json.dumps(stored))
    with pytest.raises(AtlasError, match="HASH_MISMATCH"):
        pilot.prepare_day(batch(day=date(2026, 11, 3)), workspace)
    assert row["input_sha256"] == pilot.digest(batch().model_dump(mode="json"))


def test_lock_and_protected_path_isolation(workspace):
    with pytest.raises(AtlasError, match="SEPARATE_SYNTHETIC"):
        pilot.prepare_day(batch(), pilot.ROOT / "local/oanda/two-year")
    workspace.mkdir(parents=True)
    (workspace / ".writer.lock").touch()
    with pytest.raises(FileExistsError):
        pilot.prepare_day(batch(), workspace)


def test_external_inputs_always_blocked(workspace):
    with pytest.raises(AtlasError, match="EXTERNAL_DATA_NOT_AUTHORIZED"):
        pilot.prepare_day(batch().model_copy(update={"origin": "FORWARD_OBSERVATIONS"}), workspace)
    assert not workspace.exists()


def test_late_start_and_instrument_convention_gate(workspace):
    b = batch(day=date(2026, 11, 3))
    row = pilot.prepare_day(b, workspace)
    assert row["unrecorded_calendar_days"] == ["2026-11-02"]
    bad = META[0].model_copy(update={"pip_location": -2})
    with pytest.raises(ValidationError, match="pip conventions"):
        pilot.Batch(**(b.model_dump() | {"instruments": (bad, *META[1:])}))


def test_unknown_capital_stays_blocked_without_restart(workspace):
    first = pilot.prepare_day(batch(absent=(62,)), workspace)
    second = pilot.prepare_day(batch(day=date(2026, 11, 3)), workspace)
    for name in pilot.SCENARIOS:
        assert second["scenarios"][name]["blocked"]
        assert second["scenarios"][name]["execution"] is None
        assert (
            second["scenarios"][name]["ending_cash_gbp"]
            == first["scenarios"][name]["ending_cash_gbp"]
        )


def test_minimum_size_cannot_override_capital(workspace):
    b = batch()
    instruments = tuple(
        m.model_copy(update={"minimum_units": D(100000), "maximum_units": D(100000)})
        for m in b.instruments
    )
    row = pilot.prepare_day(b.model_copy(update={"instruments": instruments}), workspace)
    for cell in row["scenarios"].values():
        assert cell["execution"]["entries"] == 0
        assert cell["execution"]["rejected"] == 1
        assert cell["ending_cash_gbp"] == "1000"


def test_session_boundary_and_file_acquisition_provenance(workspace):
    b = batch()
    bad = pilot.ReceivedFrame(frame=frame(OPEN - MINUTE), collected_at=OPEN)
    with pytest.raises(AtlasError, match="OUTSIDE_SESSION"):
        pilot.prepare_day(b.model_copy(update={"received": (bad, *b.received)}), workspace)
    row = pilot.prepare_day(b, workspace)
    assert (
        row["file_sha256"] == hashlib.sha256((workspace / "000001.json").read_bytes()).hexdigest()
    )
    assert (
        row["source"]["received"][0]["collected_at"]
        == b.received[0].model_dump(mode="json")["collected_at"]
    )
    assert (
        row["source"]["received"][0]["frame"]["start"]
        != row["source"]["received"][0]["collected_at"]
    )


def test_network_traps_fail_before_any_external_request(workspace):
    import socket

    import atlas.forex.provider as provider

    with pytest.raises(AssertionError, match="FORBIDDEN"):
        socket.create_connection(("invalid.local", 443))
    with pytest.raises(AssertionError, match="FORBIDDEN"):
        provider.OandaReader()


def test_determinism_and_persistent_independent_ledgers(workspace):
    first = pilot.prepare_day(batch(), workspace / "first")
    second = pilot.prepare_day(batch(), workspace / "second")
    assert first == second
    next_day = pilot.prepare_day(batch(day=date(2026, 11, 3)), workspace / "first")
    assert next_day["previous"] == first["checksum"]
    for name in pilot.SCENARIOS:
        assert (
            next_day["scenarios"][name]["starting_cash_gbp"]
            == first["scenarios"][name]["ending_cash_gbp"]
        )
    assert (
        hashlib.sha256(
            (pilot.ROOT / "examples/forex-lean-profitability-v1.json").read_bytes()
        ).hexdigest()
        == pilot.SHA
    )
