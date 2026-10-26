# Original synthetic datasets

**SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE**

No source here is exchange data, broker data or a profitable-strategy dataset.
atlas.synthetic constructs original, deterministic Decimal observations without
network access, randomness, external services or third-party trading code.
The recorded seed is zero and the generator explicitly records randomness=none.

tests/fixtures/synthetic_manifest.json records 32 datasets: 16 scenarios for
artificial LSE and NASDAQ instruments. NYSE is supported by the same generator
and independently tested. Each manifest entry stores scenario, venue, session
date, generator version, artificial-data label and a SHA-256 of the canonical
Python contract representation. Revisions changing that representation must
regenerate and review the manifest; hashes are not universal market-data IDs.
The complete original observations are also exported in
tests/fixtures/synthetic_data.json as decimal strings and ISO UTC timestamps.

| Scenario | Artificial behaviour / test purpose |
| --- | --- |
| rising | Rising completed bars, eligible hypothesis and later target |
| falling | Falling session, no confirmed long breakout |
| sideways | Range-bound session, no trade |
| volatile | Wider ranges and larger hypothetical stop distances |
| low_liquidity | Tiny volumes fail the strategy's liquidity condition |
| orb | Three five-minute opening bars followed by a closing breakout |
| false_breakout | Confirmation followed by a stop |
| missing | A missing opening-range candle must reject the hypothesis |
| duplicate | Repeated candle slot must reject data eligibility |
| incomplete | A candle explicitly marked incomplete remains unusable |
| stale_quote | A late-arriving old quote must reject new risk |
| gap | A post-entry gap exits worse than the planned stop |
| ambiguous | A candle touches both stop and target; stop wins |
| time_exit | Neither level hits; close using a fresh flat-by quote |
| no_fill | Next actionable quote has zero available ask quantity |
| partial_fill | Next quote supplies one share; remainder is cancelled |

The UK instrument is quoted in GBX, starting at 250 GBX = GBP 2.50; the US
instrument is quoted in USD. USD/GBP conversion uses a labelled artificial rate
of 0.8 GBP per USD; GBP conversion is exactly one. Neither is a forecast or
broker conversion quote. Quote units are carried with each observation and
checked independently before a new trade.

Every observation includes source, instrument identity, UTC event/availability/
received times, completion, adjustment and quality. Bars carry interval bounds
and OHLCV; quotes carry bid/ask sizes. Tests also alter original observations to
exercise late publication, future evidence, invalid prices/units, malformed
timestamps, no exit liquidity and unavailable time exits.

## Explicit calendar fixtures

fixture_calendar() is an injectable finite test calendar. It is **not a
maintained or authoritative exchange calendar**. It stores UTC session bounds,
exchange-local date, timezone name and explicit offset for each fixture.
No timezone database or calendar dependency was installed.

The fixture dates cover 6/9/27/30 March and 23/26/30 October/2 November 2026
to exercise US/UK DST changes and the mismatch weeks. London opens at local
08:00; New York at local 09:30 in these artificial schedules. A 24 December
half-day and explicit closed 25 December/25 October fixtures exercise shortened
and closed sessions. Unknown venue/date pairs raise an error, including unknown
weekdays; there is no weekday fallback.

These fixtures test timestamp mechanics and failure behaviour. Real-world
holiday, auction, halt and special-session coverage requires a separately
reviewed calendar/data provider in a future phase.

## Inspecting or regenerating

From the project directory:

    .\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --scenario missing
    .\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --venue XNAS --scenario gap --json
    .\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --scenario time_exit --session-date 2026-12-24

To regenerate the original manifest after intentionally changing fixture logic:

    .\.venv\Scripts\python.exe -c "from pathlib import Path; from atlas.synthetic import write_manifest; write_manifest(Path('tests/fixtures/synthetic_manifest.json'))"

Review changed hashes and scenario assertions together. Never regenerate data
to make an unsuccessful economic result look successful.
