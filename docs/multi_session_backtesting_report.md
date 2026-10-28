# Multi-session historical research report

Scope: offline local extension at accepted checkpoint
`37e7e8e4e7829a22784bfd8507e93297b8257848`. No real collection was authorized or run.

## Implementation

`atlas.multi_session_research` adds accepted immutable dataset references, session
preparation, multi-instrument chronological replay, strategy comparisons and rolling
training/validation/test evaluation. `atlas.research_history_cli` exposes planning
and offline replay through `python -m atlas.main research-history`. The existing
single-session historical CLI and all Phase 3A–4B contracts/tests remain unchanged.

Reuse includes Phase 3B's availability projection, five-minute aggregation,
historical execution quotes and explicit cost/FX contracts; the common `ORBStrategy`,
`RiskEngine.authorize`, independent exit gate and `PortfolioLedger` enforce actual
decisions and accounting. Independent single-session reports are **not** summed to
pretend that independently reset portfolios share capital.

One GBP 1,000 ledger runs across all admitted symbols and dates. Events are sorted
by permitted visibility time and canonical instrument identity. At equal timestamps,
all held symbols receive available bid marks before exits and new capital requests;
strategy transitions then see only bars admitted by that time. Competing entries use
a declared canonical-ID allocation order, not a hindsight ranking by profitability.
Daily loss accounting rolls through the existing ledger; capital and realised P/L
persist into the following session. Each day's source/event queue is bounded to that
day's datasets; the engine does not materialize two years of event objects at once.

Each instrument/session can generate one ORB hypothesis. Entry quotes must have
market event time strictly after signal decision time, and independent risk authorization
must recompute/reserve against shared cash, costs and aggregate/daily/stressed limits.
One entry attempt fills no more than the declared volume participation and cancels
the remainder. Stops latch on observed bid evidence; target/time exits use later
eligible quotes and independent exit risk approval. Partial exits preserve the existing
conservative risk envelope. No position is closed using undisclosed OHLC extrema.

The new version explicitly differs from the older single-session bar-contingent exit
model: it uses quote-driven disposals, not a bar's high/low to infer an executable
intraminute path. Old behavior/tests are preserved. Model versions and full assumptions
are included in reproducible run identities.

At a session boundary, any unliquidated position or reservation prevents replay of
subsequent sessions. The engine does not fabricate a close, reset cash, silently carry
shares through an action, or publish a supposedly reconciled multi-day liquidation.
Sessions with no admitted evidence are reported as such; they do not prove no real
market opportunity existed.

## Availability, source and FX rules

VERIFIED admits only source bars with known original permitted availability.
UNKNOWN bars remain excluded, even if a dataset is ACCEPTED. ASSUMED requires the
existing explicit 60–3,600 second bar-end delay and is labelled exploratory. Source
data and availability records are never mutated by research projections.

A point-in-time verified result additionally requires external origin, known admitted
bar availability for every source row, verified dated FX and complete corporate-action
coverage with relevant knowledge available before the session. No newly collected
Massive Basic data is claimed to meet those conditions. Artificial fixtures are always
labelled **ARTIFICIAL FIXTURES; EXPLORATORY; NOT MARKET PERFORMANCE**.

GBP/USD conversion, spread, slippage, commissions, FX fees, taxes, other fees and
volume participation are explicit Phase 3B configuration inputs. The cost engine
penny-rounds cash expense components and avoids charging spread twice on executable
bid/ask prices. Principal/basis/P&L retain exact Decimal arithmetic. Per-session FX
must be available and fresh; a stale rate is not silently refreshed to a new date.

For exploratory sensitivity work only, the bundle can explicitly choose
`FIXED_ASSUMED_PER_SESSION`. This repeats the configured artificial conversion factor
at each session open, labels its source as a fixed research assumption, and is refused
in VERIFIED mode or with a claimed verified FX policy. This is not a historical FX
series, FX trading strategy or account settlement model.

Source aggregate OHLC may contain valid sub-tick values; five-minute strategy-bar
tick rules still apply. Missing minutes are not synthesized. Executable quotes are
the existing model of a minute's open, received only at that bar's permitted
availability. The model uses **reported bar volume, not historical order-book depth**;
it is reproducible exploratory simulation, not evidence those fills were achievable.

## Training, validation and test separation

`WalkForward(training_sessions, validation_sessions, test_sessions, step_sessions)`
partitions **distinct chronological dates**, keeping all instruments on a date in the
same partition. All widths are positive integers and the step must be at least the
test width, preventing repeated test dates. No fold is produced with insufficient data.

The caller freezes the candidate ORB grid before evaluation. Every candidate is
reported on training and then validation. The selected candidate maximizes validation
net GBP P/L, using frozen input order for ties. Test results are not consulted during
selection. The selected parameters are tested once, with a selection cutoff at the
end of validation, strictly before the first test-session open. Validation itself has
a cutoff at the end of training. Earlier test dates may enter later rolling training
windows, as information available by that later training date; test windows do not overlap.

Each training/validation/test portfolio and fold has its own GBP 1,000 starting account.
Those split resets are disclosed, and fold P/L is not joined into a fictitious continuous
equity curve. Within a split, instruments/days share one chronological account. The
report retains candidate parameters, scores, selected candidate, dates, cutoffs, source
manifest checksums, full configuration, decisions/fills and a deterministic evaluation ID.

Metrics include net/gross P/L, charged costs and FX costs, per-instrument realised P/L,
session-level equity changes, trade count, wins/losses/breakeven, win rate, net expectancy,
observed-equity maximum GBP drawdown and reconciliation. A completed trade is grouped
by entry hypothesis across partial exits, so partial fills do not inflate win rate.
Win rate is a ratio from zero to one. No-trade win rate and expectancy are null.

## Offline demonstration

Saved artifact: `local/research/two-year/offline-demo-report.json` (ignored).
Original disposable fixtures model five artificial symbols labelled FAKE-AAPL,
FAKE-MSFT, FAKE-NVDA, FAKE-AMZN and FAKE-GOOGL on five dates, 2026-10-19 through
2026-10-23. Each fixture provides an explicitly partial 60-minute test interval;
these 1,500 generated rows are **not actual stock history or the target two-year dataset**.

| Fixture result | Value |
| --- | ---: |
| Shared starting capital | GBP 1,000.00 |
| Ending equity | GBP 989.6800 |
| Net P/L | GBP -10.3200 |
| Explicit charged costs | GBP 20.40 |
| Completed trades | 20 |
| Wins / win rate | 0 / 0 |
| Net expectancy | GBP -0.5160 per trade |
| Maximum observed drawdown | GBP 10.8720 |
| Reconciliation | PASS |

The five instruments competed for the same capital; all five did not automatically
receive independently funded positions. Reversing the input reference order produced
the identical report and run identity. Negative performance is retained rather than
changing assumptions to manufacture a profitable demonstration.

Three walk-forward folds used one training date, one validation date and one test
date, with a one-date step. Candidate opening ranges were five and 15 minutes.
Validation selected the 15-minute candidate in all three folds; there were 12 test
trades across the disjoint test windows. These are synthetic software checks, not
an out-of-sample finding about the five real stocks.

## CLI configuration

Use an existing public Phase 3B configuration file for explicit settings. Its example
placeholder dataset ID is replaced by the bundle's accepted dataset references.
Relative paths resolve against the bundle's directory. An illustrative bundle:

```json
{
  "schema_version": 1,
  "configuration": "historical-assumptions.json",
  "fx_policy": "FIXED_ASSUMED_PER_SESSION",
  "datasets": [
    {"database": "historical.sqlite3", "dataset_id": "accepted_dataset_sha256"}
  ],
  "strategies": [
    {"opening_range_minutes": 5},
    {"opening_range_minutes": 15}
  ],
  "walk_forward": {
    "training_sessions": 252,
    "validation_sessions": 63,
    "test_sessions": 63,
    "step_sessions": 63
  }
}
```

The example needs at least 378 distinct accepted session dates; the placeholder entry
is not executable evidence. Omit `walk_forward` to compare fixed candidates without
claiming parameter selection was out of sample. Omit `strategies` to run the configured
single hypothesis across the portfolio. `AS_CONFIGURED` FX requires explicitly suitable
dated inputs; the programmatic `SessionRef` API supports a distinct config/rate per session.

```powershell
.venv/Scripts/python.exe -m atlas.main research-history backtest --bundle local/research/bundle.json --output local/research/new-result.json --json
```

`--allow-artificial` is required for fixture inputs. Historical stores are read-only;
new output paths use exclusive creation. No broker/provider/notification initialization
or environment credential settings are needed to plan or replay.

## Validation and limitations

New deterministic offline tests: **35 passing** across collection and multi-session
modules. They cover request planning and tampering, ET cutoff/leap dates, quota sharing,
HTTP retries, pagination, interruption/resume, duplicate prevention, publication-before-
checkpoint crashes, provider-configuration drift, cross-process exclusivity and process-death lock release, missing
bar quarantine, corporate actions, shared capital, strict availability, stale FX,
chronological accounting, walk-forward boundaries, future-test changes not affecting
selection, future price shocks not changing earlier fills, reproducibility and offline CLI.

Final validation:

| Check | Result |
| --- | --- |
| Full pytest suite | **1,137 passed**, 389 dependency deprecation warnings; 144.98 seconds |
| New offline tests | **35 passed** as part of the full suite |
| Ruff lint | All checks passed |
| Ruff formatting | 120 Python files already formatted |
| pip check | No broken requirements found |
| Git diff whitespace | Passed; local LF/CRLF notices only |
| Five-symbol fixture replay / reordered replay | Identical reports and IDs; reconciled |
| Walk-forward demonstration | Three disjoint test folds; 12 synthetic test trades |

No existing tests were removed or weakened. The warning categories remain the
existing Starlette/httpx and pandas timezone-array dependency deprecations.

No two-year market dataset has been downloaded and **no two-year real-stock performance
results exist**. The accepted pilot remains the only inspected real-data partition.
Action coverage, original publication/revision history, complete historical universe,
precise oldest-day entitlement and non-display licence remain unverified. Calendar
coverage retains the existing community-rule/Nasdaq-alias limitations. Full-scale
collection and replay performance has not been benchmarked; accepted source hashes
and the exact code version are needed to reproduce future experiments.

Out-of-sample software separation does not remove survivorship/universe selection,
multiple-testing, candidate-design or researcher selection bias. There is no automatic
parameter optimization, claim of statistical significance, live-price execution,
overnight corporate-action processing, settlement, borrowing, leverage or margin.
This extension deliberately refuses unresolved overnight exposure and unsupported
structural-action sessions instead of adding weaker accounting paths.

The collector programmatic entry point is implemented and tested with the real adapter
over `httpx.MockTransport`, but there is no authorized real collection trigger in this
CLI. Real download activation, credentials, licensed usage and any action-source
expansion require separate approval. No credentials or `.env` were read, no real HTTP
attempt was charged to the existing account quota, and no commit or push was performed.
