"""Original artificial sessions, including an honest no-breakout sample path."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from atlas.costs import CostEngine, FeeSchedule
from atlas.domain import Currency, FxRate, Venue
from atlas.paper_data.contracts import Observation
from atlas.paper_data.mock import EventKind, MockCurrentProvider, MockEvent, mock_script

OPEN = datetime(2026, 10, 28, 13, 30, tzinfo=UTC)


def costs():
    return CostEngine(
        (
            FeeSchedule(
                "ARTIFICIAL_PAPER_COSTS_V1",
                OPEN - timedelta(days=1),
                OPEN + timedelta(days=2),
                (Venue.NYSE, Venue.NASDAQ),
                Decimal("2"),
                Decimal("1"),
                Decimal("0"),
                Decimal("0"),
                Decimal("1"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
            ),
        )
    )


def fx(at):
    return FxRate(Currency.USD, Decimal("0.8"), at, at, "ARTIFICIAL_USD_GBP")


def session_script(config, scenario="completed"):
    if scenario not in ("completed", "no-opportunities"):
        raise ValueError("Unknown mock paper session.")
    prices = (
        "100",
        "100.10",
        "99.90",
        "100",
        "100",
        "100.10",
        "100.20",
        "100.30",
        "100.40",
        "100.50",
        "100.50",
    )
    points = [(i * 60, price, "100") for i, price in enumerate(prices)]
    points += [
        (601, "100.50", "10"),
        (602, "100.50", "10"),
        (603, "100.50", "10"),
        (660, "101.60", "100"),
        (661, "101.60", "100"),
    ]
    base = mock_script(config, OPEN).events[0].observation.model_dump()
    events = []
    for index, (offset, price, volume) in enumerate(points):
        at = OPEN + timedelta(seconds=offset)
        observation = Observation(
            **(
                base
                | {
                    "observation_id": f"paper-session-{index}",
                    "source_reference": f"paper/{index}",
                    "event_at": at,
                    "available_at": at,
                    "received_at": at,
                    "price": price if scenario == "completed" else "100",
                    "volume": volume,
                }
            )
        )
        events.append(MockEvent(at=at, kind=EventKind.OBSERVATION, observation=observation))
    return MockCurrentProvider(tuple(events))
