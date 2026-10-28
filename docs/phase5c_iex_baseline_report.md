# Phase 5C: first full historical IEX baseline

**Completed. The unchanged baseline was unprofitable after costs: 13 simulated round trips, net GBP -12.8720, final equity GBP 987.1280.**

## Scope and reproducibility

Baseline run: `f764276f5047b95c4d79b4867c7064dc1914d0fa21abaaaaa671f595c97a612b`. Result checksum: `2b830e0acdeeae8dba786e6cd5680b6660fbfb6b8ed222cdeb9dfee8c5e1bdaf`.
All 2,500 audited accepted/reused IEX dataset IDs exactly match the replay catalog. The engine revalidated their manifests, raw sources, checksums and quality before use. All 959,045 source rows were selected with explicitly ASSUMED availability. The replay processed 500 distinct available sessions from 2024-10-28 through 2026-10-27, with one shared GBP 1,000 portfolio across AAPL, AMZN, GOOGL, MSFT and NVDA.
Five quarantined partitions on 2025-03-10 remain excluded. No missing bars were fabricated, interpolated or substituted. All accepted source and inspected paper database files were hash-checked unchanged. No credentials, market-data requests, broker endpoints, orders or Telegram delivery were used.

## Frozen assumptions

- Existing example configuration: ASSUMED 60-second delay after bar end; original publication times remain unknown. Results are not VERIFIED or point-in-time evidence.
- Raw USD IEX OHLCV; five-minute candles require five observed constituent minutes. Fifteen-minute opening range, 2R target, five-minute signal lifetime, five-minute exit buffer and minimum five-minute volume of 100 remain unchanged. Tick size USD 0.01; whole-share minimum and increment.
- One shared GBP portfolio; canonical instrument-ID order resolves simultaneous allocation. Long-only, no leverage. Planned risk 0.5% of initial capital, daily loss threshold 1%, concurrent planned risk 1%; existing 25% position-value cap, 2% gap stress and 2% stressed-risk cap remain.
- Fixed assumed GBP 0.80 per USD refreshed at each session open using the existing FIXED_ASSUMED_PER_SESSION policy. This is not historical FX evidence.
- Existing costs: 5 bps spread in rounded bid/ask quotes, 5 bps slippage per side, 15 bps FX per side, zero modeled commissions/taxes/other fixed costs, 2x stress multiplier. These are research assumptions, not broker rates.
- Existing historical execution: later minute-open quote observations only, 1% bar-volume participation, whole-share sizing, one entry attempt, partial exits permitted, no OHLC-extrema fills. The engine refuses unresolved overnight positions instead of fabricating exits.
- The baseline is exploratory/in-sample description, not an out-of-sample claim. No parameter optimisation was performed.

## Reconciled performance

| Metric | Value |
|---|---:|
| Completed round trips | 13 |
| Winning / losing / breakeven | 4 / 9 / 0 |
| Win rate | 30.7692% |
| Gross profits before charged costs, GBP | 6.9680 |
| Gross losses before charged costs, GBP | 10.3200 |
| Net winning P/L, GBP | 3.9300 |
| Absolute net losing P/L, GBP | 16.8020 |
| Net realised P/L, GBP | -12.8720 |
| Net unrealised P/L, GBP | 0 |
| Final equity and cash, GBP | 987.1280 |
| Maximum drawdown, GBP | 15.0180 |
| Maximum drawdown from contemporaneous peak | 1.4986% |
| Profit factor after costs | 0.233901 |
| Average net P/L per completed trade, GBP | -0.990154 |
| Days with / without trades | 13 / 487 |
| Charged estimated costs, GBP | 9.52 |
| Max marked exposure, GBP | 234.4480 |
| Max observed cash utilisation | 23.5399% |
| Mean observation-weighted cash utilisation | 0.2596% |

Gross profit/loss aggregates round trips before explicit charged costs, but after spread already embedded in executable prices. Net profit factor uses net winning P/L divided by absolute net losing P/L. Drawdown was recomputed from all 193,763 persisted valuation observations; unseen intrabar extremes are unavailable. Utilisation is post-observation marked equity minus available cash, not a wall-clock average.
All 26 fills reconcile independently to cash and realised P/L. There are no residual positions. Completed trades group partial exits by their original entry; the engine's raw exit-intent counter is not blindly used as a round-trip count.
Charged costs total GBP 9.52: GBP 7.09 FX conversion and GBP 2.43 slippage. Separate commission, tax and spread cash charges are zero under the frozen model. Spread still affects simulated bid/ask prices; its exact counterfactual GBP opportunity cost is not separately recorded and is reported unavailable.

## Per-symbol performance

| Symbol | Trades | Wins | Losses | Net GBP P/L | Charged costs GBP |
|---|---:|---:|---:|---:|---:|
| AAPL | 2 | 1 | 1 | -0.3820 | 1.63 |
| AMZN | 3 | 2 | 1 | -0.5500 | 2.31 |
| GOOGL | 5 | 0 | 5 | -10.4380 | 3.63 |
| MSFT | 0 | 0 | 0 | 0 | 0 |
| NVDA | 3 | 1 | 2 | -1.5020 | 1.95 |

AAPL performed best among traded symbols (still a loss); GOOGL performed worst. MSFT stayed flat because it had no fills, which is not evidence of a profitable strategy.

## Monthly performance

| Month | Completed trades | Realised GBP P/L |
|---|---:|---:|
| 2024-10 | 0 | 0 |
| 2024-11 | 0 | 0 |
| 2024-12 | 3 | 1.0380 |
| 2025-01 | 0 | 0 |
| 2025-02 | 1 | 0.7000 |
| 2025-03 | 0 | 0 |
| 2025-04 | 0 | 0 |
| 2025-05 | 1 | -0.7240 |
| 2025-06 | 1 | -0.8080 |
| 2025-07 | 0 | 0 |
| 2025-08 | 0 | 0 |
| 2025-09 | 0 | 0 |
| 2025-10 | 0 | 0 |
| 2025-11 | 2 | -8.3460 |
| 2025-12 | 0 | 0 |
| 2026-01 | 1 | -2.1980 |
| 2026-02 | 0 | 0 |
| 2026-03 | 2 | 0.8400 |
| 2026-04 | 0 | 0 |
| 2026-05 | 1 | -0.9360 |
| 2026-06 | 1 | -2.4380 |
| 2026-07 | 0 | 0 |
| 2026-08 | 0 | 0 |
| 2026-09 | 0 | 0 |
| 2026-10 | 0 | 0 |

## Chronological train/validation/test evaluation

The existing walk-forward engine ran one non-overlapping 300/100/100-session fold with exactly one fixed ORB candidate. There was no parameter search or test-period tuning. Each segment has its own GBP 1,000 portfolio; split equity must not be concatenated into a continuous compounded curve.

| Segment | Dates inclusive | Sessions | Trades | Net GBP P/L | Final equity GBP |
|---|---|---:|---:|---:|---:|
| training | 2024-10-28 to 2026-01-09 | 300 | 9 | -10.3380 | 989.6620 |
| validation | 2026-01-12 to 2026-06-04 | 100 | 3 | -0.0960 | 999.9040 |
| test | 2026-06-05 to 2026-10-27 | 100 | 1 | -2.4380 | 997.5620 |

The implementation supports multiple folds, but this frozen full-width split yields one fold. The held-out result contains only one completed trade: confidence is very low. No alternative strategy was selected based on these outcomes.

## Shared-portfolio diagnostics

Actual chronological replay generated 19 unique strategy hypotheses: 13 entered and completed, six were independently rejected by risk with POSITION_SIZE. There were 26 approved risk decisions including exits. The six rejected opportunities exhausted all non-filled hypotheses; no additional expired/unfilled execution rejection was recorded. There were no partial fills or unresolved positions in this baseline.
Actual strategy reason counts below are evaluation events, not unique sessions, trades or mutually exclusive categories:

| Reason | Evaluation count |
|---|---:|
| MARKET_CLOSED | 2495 |
| MISSING_BARS | 39792 |
| NO_BREAKOUT | 138 |
| OPENING_RANGE_FORMING | 1505 |
| STRATEGY_DATA_QUALITY | 142141 |
| TRIGGER_ALREADY_PAST | 13 |

STRATEGY_DATA_QUALITY appeared in 2,492 symbol-session streams; MISSING_BARS in 1,085. These overlap and can occur after an earlier valid signal. NO_BREAKOUT occurred in 80 streams, so it must not be interpreted as 80 entirely breakout-free sessions.
The coverage audit found 19 incomplete 15-minute opening ranges and 25 partitions lacking a contiguous initial 20 minutes. Entire-day missing data adds five excluded partitions; accepted sessions have 11,455 missing minute slots.
One confirmed limitation: source aggregates can legitimately contain sub-cent OHLC values, while the unchanged strategy requires every visible OHLC value to align to the USD 0.01 instrument tick. For the first session alone, off-tick five-minute candle counts were AAPL 54, AMZN 42, GOOGL 38, MSFT 37 and NVDA 60; none of those sampled candles failed the minimum-volume rule. This is not a source-corruption finding. The generic rejection code combines multiple checks, so those sample counts must not be extrapolated as a complete cause breakdown.

## Dashboard and persisted outputs

The existing authenticated dashboard reads synthetic HistoryStore activity and exposes a SYNTHETIC-only model. It has no native importer for these multi-session historical results. Historical fills were not injected into that store or the paper ledger.
A separate self-contained read-only historical HTML view is exported from the existing reporting projection and persisted result. It displays reconciled equity, completed trades, P/L, drawdown, costs, daily closing equity and all simulated fill activity. It uses no external assets, network calls, order controls or notifications. Display values are rechecked against the persisted fill ledger before rendering; automated tests reject altered metrics.

Artifacts (ignored local files):
- [Read-only research view](../local/research/phase5c/research-dashboard.html)
- [Baseline result, fill ledger and valuations](../local/research/phase5c/baseline-result.json)
- [Reconciled metrics](../local/research/phase5c/performance.json)
- [Monthly CSV](../local/research/phase5c/monthly-performance.csv)
- [Per-symbol CSV](../local/research/phase5c/per-symbol-performance.csv)
- [Completed trades CSV](../local/research/phase5c/completed-trades.csv)
- [Walk-forward summary](../local/research/phase5c/walk-forward-summary.json)
- [Input validation](../local/research/phase5c/input-validation.json)

## Reproduction and validation

Frozen inputs are `local/research/phase5c/baseline-bundle.json`, `baseline-config.json` and `walk-forward-bundle.json`. The local runner additionally disables socket connections. The existing offline CLI can reproduce the baseline into a new output path:

```powershell
.venv/Scripts/python.exe -m atlas.main research-history backtest --bundle local/research/phase5c/baseline-bundle.json --output local/research/phase5c/baseline-repeat.json | Out-Null
```

Full pytest: 1,247 passed. Ruff lint, Ruff formatting and pip check passed. Seven new reporting tests cover cash/fill reconciliation, partial-exit trade grouping, tamper detection, zero eligible observations, HTML consistency, and unchanged results with progress reporting. No trading-loop logic or thresholds were changed; additions collect strategy reasons/progress and reconcile exported metrics.

## Confidence and next work

Thirteen total trades and one held-out trade are inadequate to establish an edge or rank stocks reliably. Other limitations include IEX-only coverage, missing observations, unknown publication times, fixed FX, incomplete corporate-action metadata, an unverified point-in-time universe, modeled liquidity and historical minute-open execution. No live-trading inference is warranted.
Next, investigate and explicitly resolve the historical-aggregate versus strategy tick-validation contract, with source-preserving tests and a separately reviewed behaviour change. Improve rejection subcodes before any parameter optimisation. Then assess whether the chosen feed supports the intended strategy and execution model. Keep this baseline frozen; do not tune thresholds to force more trades.

## Complete independent diagnostics

All 2,500 independent partition probes completed; their strategy reason counts exactly match the actual shared replay. They start independent portfolios and are never summed into the shared performance result. Their 13 trades and six risk rejections happen to match this baseline, but that equivalence is not guaranteed for other runs.

The following partition classification is based on observed five-minute closes and coverage-verified opening ranges, not proof of what happened during missing minutes:

| Classification | Symbol-sessions |
|---|---:|
| Incomplete 15-minute opening range | 19 |
| Complete opening range, no observed later close above its high | 981 |
| Complete opening range, observed breakout but no valid strategy signal | 1,481 |
| Valid strategy signal | 19 |

A separate overlapping diagnostic flag, BREAKOUT_REJECTED_BY_STRATEGY, appeared in 1,498 partitions and 53,485 evaluations. It is not a count of unique missed trades. No independent execution-constraint rejection was recorded. The six actual shared-account risk rejections were two MSFT and four GOOGL POSITION_SIZE failures. Their exact dates and intent IDs are retained in the diagnostics summary.

- [Independent diagnostics summary and actual shared rejections](../local/research/phase5c/diagnostics-summary.json)
- [Per-partition diagnostics CSV](../local/research/phase5c/partition-diagnostics.csv)
- Full individual probes are retained under `local/research/phase5c/partition-probes/`.

The original engine processed the full dataset successfully without a replay-algorithm redesign or integrity shortcut. Existing per-day event queues kept replay bounded. Additions were reporting-only strategy counters/progress, reconciled performance metrics and the isolated HTML view. This checkpoint also includes the previously approved session-exclusion implementation and completed-collection audit documentation.
