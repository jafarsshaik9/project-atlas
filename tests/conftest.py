"""Own synthetic fixtures only; third-party research snapshots are never imported."""

from dataclasses import dataclass

import httpx
import pytest

from atlas.portfolio import PortfolioLedger
from atlas.research import ResearchSettings
from atlas.risk import RiskEngine
from atlas.strategy import ORBStrategy
from atlas.synthetic import fixture_calendar, generate, synthetic_costs


@pytest.fixture(autouse=True)
def isolated_activity_history(tmp_path, monkeypatch):
    """CLI tests must never populate a user's dashboard history."""
    monkeypatch.setenv("ATLAS_HISTORY_DB", str(tmp_path / "history.sqlite3"))
    monkeypatch.setenv("ATLAS_TELEGRAM_ENABLED", "false")

    def forbidden_network(self, request):
        pytest.fail("External HTTP is forbidden in automated tests; use MockTransport.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_network)


@dataclass
class Context:
    data: object
    bars: tuple
    signal: object
    quote: object
    ledger: PortfolioLedger
    risk: RiskEngine


@pytest.fixture
def context():
    data = generate()
    bars = data.bars[:4]
    signal = (
        ORBStrategy().evaluate(data.instrument, bars, data.session, bars[-1].visible_time).signal
    )
    assert signal is not None
    ledger = PortfolioLedger(ResearchSettings())
    return Context(
        data,
        bars,
        signal,
        data.quotes[0],
        ledger,
        RiskEngine(ledger, synthetic_costs(), fixture_calendar()),
    )
