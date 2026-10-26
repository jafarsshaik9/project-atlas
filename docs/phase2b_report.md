# Phase 2B completion report

26 October 2026. Working offline research foundation; no profitable strategy has
been established. All reported outcomes below are artificial software fixtures.

## Files created

| Files | Implemented responsibility |
| --- | --- |
| src/atlas/domain.py | Validated instruments, currency/quote units, UTC bars/quotes, sessions, signals, costs, research-only ranking, positions, snapshots, decisions and audit events |
| src/atlas/sessions.py | Clock/calendar protocols, monotonic replay clock, injectable fixture calendar and independent bar quality checks |
| src/atlas/synthetic.py | Original LSE/NYSE/NASDAQ fixtures, explicit calendar dates, artificial FX/fees and reproducible exports |
| src/atlas/costs.py | Decimal costing, effective dates, fees/taxes/FX, bid/ask spread handling, cost uncertainty and affordability |
| src/atlas/portfolio.py | GBP-base cash, average carrying cost, realized/unrealized/daily P&L, FX cost attribution, reservations, partial fills, history and reconciliation |
| src/atlas/risk.py | Independent fail-closed decisions, policy/snapshot versions, aggregate/daily/stressed risk and atomic authorization/reservation |
| src/atlas/strategy.py | ORB v1, configurable opening range, completed-bar confirmation, stop/target/time-exit hypotheses and expiry |
| src/atlas/backtest.py | Availability-order replay, next-actionable fills, gap/ambiguous-bar/no-fill treatment and complete synthetic reports |
| src/atlas/research_cli.py | Offline command handlers and strictly typed example configuration loading |
| tests/conftest.py | Original common research fixtures |
| tests/test_domain_sessions_costs.py | Contract, timestamp/session, synthetic-data and hand-calculated cost tests |
| tests/test_portfolio_risk.py | Accounting invariants, partial fills, cash/reservations, risk rejection and concurrency tests |
| tests/test_strategy_replay_cli.py | ORB/replay, no-look-ahead, gap/exit/no-trade, manifest and offline CLI tests |
| tests/fixtures/synthetic_manifest.json | Checksums and deterministic parameters for 32 original datasets |
| tests/fixtures/synthetic_data.json | Complete exported original OHLCV/quote observations |
| examples/research.json | GBP 1,000 and configurable 0.5%/1%/1% starting research settings |
| examples/risk.synthetic.json | Provisional allocation, freshness and stress policies; safety switches disabled |
| examples/costs.synthetic.json | Explicit non-authoritative, effective-dated synthetic fee schedule |
| docs/synthetic_data.md | Dataset provenance, scenarios, session fixtures and regeneration instructions |
| docs/backtesting_assumptions.md | Fill, accounting, risk and cost assumptions and remaining limitations |
| docs/phase2b_report.md | This completion and verification record |

Modified: src/atlas/main.py, README.md, docs/atlas_architecture.md,
docs/development_roadmap.md, docs/claude_trader_evaluation.md and
docs/setup-report.md. Existing ResearchSettings/VirtualLedger compatibility
interfaces and both earlier test modules were preserved.

## Configuration and command verification

Starting capital remains parameterized, default GBP 1,000. Planned per-trade
risk is 0.5% (GBP 5), daily loss threshold 1% (GBP 10), and concurrent planned
risk 1% (GBP 10), all based on initial configured capital. These are provisional
research parameters, not optimal settings or guaranteed loss limits.
No broker balance supplies simulation capital.

The existing health command passed. research-check passed with the explicit
research/risk/cost JSON examples. synthetic-backtest ran successfully for the
default XLON case, XNAS gap and sideways cases, and half-day XNAS time-exit JSON.
Both offline commands are tested with sockets and HTTP sends disabled and with
broker-credential environment reads set to fail the test.

## Automated verification

| Check | Actual result |
| --- | --- |
| Python | 3.14.10, project virtual environment |
| pytest | 266 passed in 1.03 seconds; exit 0 |
| Existing tests | Original 40 broker cases and 40 capital/settings cases retained |
| New Phase 2B cases | 186 |
| ruff check . | All checks passed; exit 0 |
| ruff format --check . | 27 files already formatted; exit 0 |
| Dependencies | No new dependency or software installation |

Tests cover GBP/GBX units, USD conversion, Decimal expenses, malformed/non-finite
data, tick/quantity constraints, publication chronology, missing/duplicate/
incomplete candles, stale quotes, DST mismatch dates, holidays/half-days,
known/effective costs and spread treatment, overspending, partial fills,
reservations, concurrent authorization, daily/aggregate risk, invalid snapshots,
duplicates, shorts/leverage/execution switches, kill switch, gap losses,
ambiguous OHLC, time exits, no-trade cases and deterministic replay.

## Example artificial results

**SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE**

| Fixture | Initial GBP | Ending equity GBP | Local entries | Explicit costs GBP | Net P&L GBP |
| --- | ---: | ---: | ---: | ---: | ---: |
| XLON orb | 1,000.00 | 1,004.56 | 1 | 0.34 | +4.56 |
| XNAS gap | 1,000.00 | 970.77 | 1 | 0.40 | -29.23 |
| XLON sideways | 1,000.00 | 1,000.00 | 0 | 0.00 | 0.00 |

The default ORB fixture had gross P&L GBP 4.90, observed drawdown GBP 2.06,
maximum cash utilisation 24.90%, planned risk GBP 4.25 and stressed risk GBP 9.56.
The US gap lost more than its planned GBP 4.48 and stressed GBP 8.23 and recorded
one daily-threshold breach. This illustrates model and risk-bound limitations,
not an empirical strategy result. All three reconciled with no remaining position.

## Security and architecture review

SHA-256 comparison verified no change to broker.py, broker credential config.py,
scripts/broker-check.ps1, pyproject.toml or the original test_broker.py.
The fixed demo account-summary URL and GET-only production HTTP method remain.
Static import inspection found no broker/network/subprocess imports in the nine
new offline modules. The main CLI routes offline commands before broker-credential
loading. No API secrets or private configuration were inspected, no third-party
trading scripts were run, and no skill pack or new connector was installed.

Git exclusions remain effective for .env, .venv, logs and local research files.
No project log files were found. This source/behaviour review is not a general
security certification. Independent risk recomputation and reservation cannot
be replaced with a supplied approval flag; no AI component is present.

**No Trading 212 Practice or real-money orders were placed.**
The code contains no broker order endpoint or live environment enablement.
All new fills are local ledger operations.

## Limits and next phase

The system uses artificial observations and fee schedules, a finite injected
fixture calendar, one instrument/session per replay, instantaneous GBP conversion,
OHLC exit assumptions and an in-memory ledger. It does not supply authoritative
fees/taxes, real-data performance, calibrated net-return probabilities, a full
walk-forward framework, settlement, durable recovery, FX trading or ISA support.
An invalid/missing time-exit quote leaves a prominently flagged open position.

No dependency blocker remains for the approved synthetic scope. Real-data
research is blocked on provider rights/quality, maintained calendar coverage and
verified cost applicability. Recommend a separate read-only data-validation
phase, followed by multi-instrument replay and chronological holdout validation.
Broker execution remains a separately authorized future project.
