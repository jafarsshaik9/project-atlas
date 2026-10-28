# Phase 5D — reported-price precision investigation

## Contract finding

The Phase 5C penny-grid rule was incorrect **for reported OHLC observations**.
`Instrument.valid_price()` represents the configured executable price grid; it
was also applied to every OHLC field in the visible evidence prefix by both
`ORBStrategy.evaluate()` and `RiskEngine.evaluate()`. One sub-cent observation
could therefore block every later decision in an otherwise eligible session.
The historical store itself did not impose this grid.

This is a distinction between prices, not a claim that any sub-cent datum is valid:

| Price context | Contract |
| --- | --- |
| Reported qualifying trades | Sub-cent executions can be legitimate, including midpoint executions. |
| Provider OHLC | First/last qualifying trade and qualifying extrema can retain sub-cent precision. VWAP is a separate statistic. |
| Historical observation validation | Finite positive Decimal OHLC, possible relationships, nonnegative volume, correct identity/unit/adjustment, timestamps, session, availability, source provenance and integrity. |
| Modeled executable quotes/fills | Existing instrument grid remains mandatory; this research config retains $0.01. |
| Unknown or corrupted observations | Continue rejecting invalid numeric values, impossible OHLC, malformed timing, mismatched provenance, duplicate/conflicting data and failed hashes. Plausible but erroneous vendor prices cannot be independently authenticated from aggregates alone. |

Alpaca documents trade-condition-dependent minute OHLC and higher-timeframe
aggregation from minute bars. It does not specify rounding OHLC to cents.
[Alpaca Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).
The SEC explicitly distinguishes sub-penny executions from prohibited
sub-penny orders/quotes, including midpoint and price-improvement executions.
[Regulation NMS adopting release](https://www.sec.gov/rules/final/34-51808fr.pdf).
IEX's fixed-midpoint-peg filing identifies midpoint execution in sub-pennies.
[IEX filing, Federal Register, 7 August 2023](https://www.govinfo.gov/content/pkg/FR-2023-08-07/pdf/2023-16710.pdf).
These sources justify the observation/order distinction; they do not establish
the exact trade condition behind any individual stored OHLC value, or verify
today's tick schedule for every instrument.

## Minimal correction

- Remove only the observational OHLC tick check in strategy and risk review.
- Keep `MarketBar` integrity checks, unit/raw-adjustment checks, minimum volume,
  session/availability/contiguity checks and every strategy threshold unchanged.
- Keep `Instrument.valid_price()`, cost validation, executable bid/ask checks,
  portfolio fill/mark checks, and existing floor/ceil simulated quote construction.
- Keep the legacy single-partition candle-driven execution guard: that path uses
  OHLC as a fill/mark price, unlike the quote-driven shared-portfolio replay.
- Preserve raw minute bars and unrounded five-minute OHLC aggregation; do not
  round away a breakout. Stop/target plans retain their existing grid rounding.
- Version the observation contract as `orb-5m-v2-observed-prices` and include it
  in multi-session run configuration/identity. No Phase 5C output is overwritten.

The paired `precision_probe()` diagnostic reconstructs the old gate at its old
precedence. It counts individual off-grid candles separately from later
evaluations rejected because their visible evidence still contains those bars.
It does not simulate independent risk approval or claim portfolio trades.

## Controlled experiment

The Phase 5C bundle/configuration and immutable dataset references are copied
unchanged into ignored `local/research/phase5d/`. All 2,500 accepted IEX
partitions, 959,045 source bars and 500 dates are retained. The five excluded
10 March 2025 partitions remain excluded. The starting portfolio is GBP 1,000,
shared chronologically across AAPL, MSFT, NVDA, AMZN and GOOGL. Availability
remains **ASSUMED**, with the same 60-second delay, fixed USD/GBP 0.80, spreads,
slippage, fees, volume participation, risk limits and execution rules.

The full baseline is descriptive in-sample research, not a new out-of-sample
claim. Phase 5C's previous walk-forward results remain historical results of
the old contract; they are not relabeled as corrected results.

Local reproducibility commands (repository root; offline Python runners block
socket connections and never load credentials):

```powershell
.venv/Scripts/python.exe local/research/phase5d/run_offline.py
.venv/Scripts/python.exe local/research/phase5d/audit.py
```

These runners use exclusive output creation and refuse to overwrite this run.
The baseline itself uses existing `load_bundle()` / `run_multi_session()`;
the paired audit uses `prepare()` / `precision_probe()` and compares every
legacy reason and timestamp to the checksum-verified frozen Phase 5C probes.
To replay to another new output using the existing CLI, use:

```powershell
.venv/Scripts/python.exe -m atlas research-history backtest --bundle local/research/phase5d/baseline-bundle.json --output local/research/phase5d/repeat-result.json --json
```

## Results and validation

The full replay completed and reconciled. The corrected contract found more
eligible signals, **not a profitable strategy**. No parameter selection or
profitability-based choice was made.

| Metric | Phase 5C | Corrected Phase 5D |
| --- | ---: | ---: |
| Completed round trips | 13 | 871 |
| Wins / losses / breakeven | 4 / 9 / 0 | 254 / 615 / 2 |
| Win rate | 30.7692% | 29.1619% |
| Gross profit before charged fees | GBP 6.9680 | GBP 533.5280 |
| Gross loss before charged fees | GBP 10.3200 | GBP 600.1200 |
| Charged costs | GBP 9.52 | GBP 644.34 |
| Net realised P/L | GBP -12.8720 | GBP -710.9320 |
| Unrealised P/L | GBP 0 | GBP 0 |
| Final cash / equity | GBP 987.1280 | GBP 289.0680 |
| Maximum drawdown | GBP 15.0180 | GBP 713.9160 |
| Maximum drawdown percentage | 1.498584% | 71.249386% |
| Profit factor after costs | 0.233901 | 0.304466 |
| Average net P/L per trade | GBP -0.990154 | GBP -0.816225 |
| Days with / without trades | 13 / 487 | 427 / 73 |
| Rejected entry decisions | 6 | 479 |

Drawdown percentage uses the contemporaneous peak, not starting capital.
Gross P/L includes the spread embedded in executable bid/ask prices; it excludes
the separately charged FX/slippage costs. Phase 5D charged GBP 478.77 FX costs
and GBP 165.57 slippage. Gross net P/L was already negative at GBP -66.5920;
charged costs then reduced it to GBP -710.9320. Exact counterfactual spread
opportunity cost is unavailable from this ledger.

| Symbol | Completed trades | Net P/L, GBP | Charged costs, GBP |
| --- | ---: | ---: | ---: |
| AAPL | 220 | -179.1400 | 173.62 |
| AMZN | 234 | -182.5500 | 173.39 |
| GOOGL | 154 | -123.3720 | 108.18 |
| MSFT | 0 | 0 | 0 |
| NVDA | 263 | -225.8700 | 189.15 |

All traded symbols lost money. GOOGL had the smallest aggregate loss and NVDA
the largest; these are unequal-exposure descriptive totals, not rankings for
strategy selection. MSFT's zero P/L represents no trades, not superior returns.

### Precision rejection census

- 959,045 raw minute bars inspected; **521,191** contain an off-cent OHLC value.
  These were already accepted source observations, not newly imported data.
- **186,103** derived five-minute candles evaluated; **110,387** contain an
  off-cent OHLC value. All 2,500 partitions / 500 dates contain such candles.
- **142,141 evaluations** were rejected solely by the old tick gate across
  **2,492 partitions**. Repeated prefix evaluation explains why this exceeds
  the number of individual off-grid candles. In the other eight partitions,
  earlier eligibility checks prevented reaching the tick gate.
- **2,291 of 2,481 complete opening ranges** contain off-cent observations.
- The correction produces **1,331 additional valid strategy signals** across
  455 dates: **19 → 1,350**. These are actionable strategy hypotheses before
  independent portfolio risk, not 1,331 guaranteed profitable trades.
- All 186,103 legacy timestamp/reason sequences match the frozen Phase 5C
  probes. Corrected probe reason totals also match the actual shared replay.

| Symbol | Evaluated 5m candles | Off-grid candles | Tick-gated evaluations | Affected complete opening ranges | Additional signals |
| --- | ---: | ---: | ---: | ---: | ---: |
| AAPL | 38,156 | 21,779 | 31,007 | 460 | 284 |
| AMZN | 38,007 | 21,021 | 28,644 | 464 | 265 |
| GOOGL | 37,361 | 20,880 | 26,056 | 454 | 265 |
| MSFT | 33,890 | 20,785 | 19,908 | 459 | 225 |
| NVDA | 38,689 | 25,922 | 36,526 | 454 | 292 |

Each symbol has 500 partitions containing off-grid candles. Real persisted
examples below are UTC minute starts on **28 October 2024**, unadjusted USD
values; these values were neither rounded nor rewritten:

| Symbol | Start UTC | Open | High | Low | Close |
| --- | --- | ---: | ---: | ---: | ---: |
| AAPL | 13:30 | 233.32 | 234.255 | 233.32 | 234.255 |
| AMZN | 13:31 | 189.555 | 189.92 | 189.53 | 189.585 |
| GOOGL | 13:32 | 168.29 | 168.29 | 167.895 | 168.205 |
| MSFT | 13:30 | 431.635 | 431.85 | 429.49 | 429.63 |
| NVDA | 13:30 | 143.05 | 143.13 | 142.265 | 142.35 |

Full dataset IDs, timestamps and provenance for these examples are saved in
`local/research/phase5d/precision-summary.json`. The first AAPL example belongs
to dataset `e0aaf61ae6d6ced02c6996fd4d83d7f0739a8bbf3edfdfd59232236cc36f2144`.

### Remaining rejection categories

These are evaluations, not independent opportunities or unique sessions:

| Strategy outcome | Original | Corrected |
| --- | ---: | ---: |
| STRATEGY_DATA_QUALITY | 142,141 | 0 |
| MISSING_BARS | 39,792 | 39,792 |
| MARKET_CLOSED | 2,495 | 2,495 |
| OPENING_RANGE_FORMING | 1,505 | 7,472 |
| NO_BREAKOUT | 138 | 69,958 |
| TRIGGER_ALREADY_PAST | 13 | 65,028 |
| INVALID_OR_EXPIRED_PLAN | 0 | 8 |
| SIGNAL | 19 | 1,350 |

In this dataset no surviving unit/adjustment/minimum-volume condition explains
the original quality rejections: the paired test isolates them all to the tick
gate. The unchanged generic `STRATEGY_DATA_QUALITY` reason still guards those
conditions in other data. Numeric/OHLC/source-integrity failures remain input
validation failures, separate from these strategy and portfolio outcomes.

The 2,500 partition probes classify as 1,350 with a valid signal, 981 with a
complete opening but no observed later breakout, 150 with an observed breakout
blocked by other strategy eligibility/filter rules, and 19 incomplete openings.
These are observation probes, not independent trading accounts.

Actual shared-account outcomes reconcile **1,350 signals = 871 filled entries +
479 rejected entries**. All positions close. Every unfilled entry has a
recorded risk rejection; no unexplained execution-only disappearance remains.
Risk reasons can overlap on a single decision:

| Risk reason | Decisions containing reason |
| --- | ---: |
| POSITION_SIZE | 370 |
| INSUFFICIENT_CASH | 271 |
| PER_TRADE_RISK | 35 |
| AGGREGATE_RISK | 20 |
| DAILY_RISK_BUDGET | 3 |
| ENTRY_BID_AT_OR_BELOW_STOP | 2 |
| INVALID_ENTRY_PLAN | 2 |
| STRESSED_RISK | 2 |

MSFT has 227 valid signals and no fills. Its rejected decisions include 225
position-size failures and two bid-at/below-stop failures, plus overlapping
cash/risk reasons. Its zero-trade result is now explained by unchanged portfolio
and executable-price constraints, rather than the observation precision gate.
Of the **73 zero-trade dates**, 43 have no valid signals and 30 have signals
whose entries are all rejected. No zero-trade date is unaccounted for in this
diagnostic sense; this does not prove the vendor data or model is perfect.

### Evidence, validation and limitations

- New run ID: `8d0719b3d2a00ad67634a9cd8481543af99c10f41e9fe44413ff6dfd617b64a6`.
- Result checksum: `afaf8fbd2e40d224353a586cbee0238c8c762c8f16977c0fdff4490d4c673605`.
- Original run ID: `f764276f5047b95c4d79b4867c7064dc1914d0fa21abaaaaa671f595c97a612b`.
- All 2,517 frozen Phase 5C artifact hashes match. Both accepted historical
  databases and the three protected paper database file hashes are unchanged.
  Dataset proof lists and every per-partition parameter configuration match.
- Existing ledger reconciliation independently reproduces cash, realised P/L,
  fees, unique fills and drawdown. New isolated read-only
  `local/research/phase5d/research-dashboard.html` agrees with the ledger;
  original paper portfolios are unchanged.
- Full pytest: **1,261 passed**, 653 existing warnings; **14 new tests**.
  Targeted observation/performance tests: 21 passed. Ruff lint and formatting
  pass; `pip check` reports no broken requirements. No existing tests removed.
- New tests cover sub-cent observation acceptance, exact unrounded breakout
  comparison, risk approval, nonfinite/nonpositive/impossible OHLC rejection,
  off-grid executable quote rejection, retained volume/contiguity filters,
  gate/signal count separation, no future-observation influence, and run
  contract identity. Existing order/fill/risk tests remain intact.
- Local outputs include `baseline-result.json`, `performance.json`,
  `comparison.json`, `precision-summary.json`, `precision-partitions.csv`,
  2,500 paired precision probes, fills/trades CSVs, source hashes and test logs.
  Generated research artifacts, data, databases and credentials stay out of Git.
- No authenticated provider requests, broker actions, notifications or source
  dataset modifications occurred.

Confidence remains limited by IEX-only coverage (including 19 incomplete opening
ranges and the excluded date), missing five-minute intervals, unknown original
publication times, fixed assumed FX, modeled liquidity/costs, incomplete
corporate-action metadata and a non-survivorship-free five-stock universe.
An accepted aggregate is not evidence of an executable consolidated quote.

Next investigate **execution/cost realism and the treatment of sparse IEX
intervals**, keeping observation integrity and strategy thresholds fixed. Costs
are the largest measured drag here, but removing them would still leave a loss.
After those assumptions are independently reviewed, rerun the frozen chronological
out-of-sample evaluation under the corrected contract. Do not optimise on this
full-period result or relax position/cash limits merely to produce MSFT trades.
