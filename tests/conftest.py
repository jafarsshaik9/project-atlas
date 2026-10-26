"""Own synthetic fixtures only; third-party research snapshots are never imported."""

from dataclasses import dataclass

import pytest

from atlas.portfolio import PortfolioLedger
from atlas.research import ResearchSettings
from atlas.risk import RiskEngine
from atlas.strategy import ORBStrategy
from atlas.synthetic import fixture_calendar, generate, synthetic_costs


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
