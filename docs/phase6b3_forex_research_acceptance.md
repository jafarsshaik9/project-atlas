# Phase 6B-3 controlled historical Forex research and acceptance

This report accompanies the frozen
[research design](phase6b3_frozen_research_design.md). The experiment is offline
historical intraday research under assumed execution. No result authorizes broker
connectivity, trading, deployment or overnight holdings.

## Checkpoint and validation

Starting repository: `main` at
`ab08e9c12a50a3c6218c3b21a0a26e5f95b6f4b2`, clean and matching `origin/main`.
The original Phase 6B-2 checkpoint and its evidence were retained. The incomplete
initial prompt was clarified before research parameters or historical evaluations
were started; completed preflight work was reused.

Baseline: **1,512 passed** in 245.90 seconds. Pre-historical validation:
**1,657 passed** in 268.10 seconds. The first complete post-implementation suite
passed **1,663 tests** in 360.83 seconds. A further validator regression case brings
the phase's additions to **152 tests**: 145 research cases and seven independent
acceptance cases. Final verification is recorded below. No existing tests were
removed or relaxed.

Final suite: **1,664 passed**, zero failures, errors or skips, in **305.04 seconds**.
The 653 warnings are the existing Starlette/pandas deprecations. Ruff lint passed,
all 192 Python files passed formatting, and `pip check` found no broken
requirements.

Ruff lint, formatting and dependency checks are required to remain passing at
checkpoint. No new package dependency is introduced. Existing stock, paper,
dashboard, Telegram, OANDA transport and accepted source readers are unchanged.

## Frozen experiment

Manifest SHA-256:
`b3031e70acb3b1a801dcec2cef1bb394b659ff9333ac7fa4f44c8a79a8b6b499`.

The manifest was persisted before any historical strategy result was examined.
There are 180 planned active cells, consisting of three pairs × three independent
sessions × ORB/momentum/mean-reversion/directional baseline × five cost scenarios.
The nine no-trade references remain separate. C0–C3 use the exact frozen spread
and slippage assumptions and a declared conservative GBP 5/100,000-unit/side
commission. The 36 C4 cells are explicitly blocked because an additional execution
candle is outside the approved timing contract. They have unavailable performance,
not fabricated zero returns.

The cost schedule is a fixed retrospective research assumption from the authorized
design, not a claim that these charges or execution conditions were historically
published or available to an account. Its pre-period `known_at` is counterfactual
configuration semantics; no future observed spread distribution was used to
calibrate a parameter.

The fixed development/validation/holdout boundaries are enforced as UTC half-open
intervals. Final coverage ends at 22:50 UTC on October 28, 2026, including the last
available 22:49 candle. Each cell carries its own GBP 1,000 ledger across splits.
No overlapping session experiments are combined into a portfolio, and no cell is
funded again at a split boundary.

## Implementation and independent review

The new `atlas.forex.research` package provides the frozen design loader, causal
signal rules, bounded matrix runner, metrics and a one-intent specialization of
the accepted execution state machine. Only quiescent intervals with no possible
financial transition are skipped. Whole-session quality remains in the evidence.
Full-engine parity tests cover every pair, direction, rule and evaluated cost.

Two explicit compatibility extensions were implemented and tested before the
historical run: immutable entry-time bracket/sizing rules and V2 spread multipliers.
They do not change the inherited next-close timing, stop-first collision policy,
bid/ask directionality, zero assumed publication delay or gap/risk controls.
Legacy absolute intents retain their economic behavior; the new implementation
has its own source and configuration fingerprints.

The adversarial acceptance pass uses separate code and tests, by the same agent;
it is not a claim of external human certification. It checks source-price arithmetic,
indicator evidence, entry/exit chronology, forced risk exits, bid/ask conversion,
cash continuation, marked equity/drawdown, missing paths and reproducibility. The
validator itself was tested against artificially rewritten favorable entry prices.

The quiescent specialization stops financial processing when a position becomes
unresolved. To retain the Phase 6B-2 resumption diagnostic, the audit supplies a
separate `UNRESOLVED_STRESS_DIAGNOSTIC_OVERLAY_V1` when needed. It records the first
defensible intraday adverse resumption quote without booking an exit, assigning
P/L, changing strategy parameters or rewriting the original result. This is a
diagnostic-completeness supplement, not a change to economic outcomes or a new
holdout strategy evaluation.

An initial validator assertion incorrectly gave a newly observed exposure breach
priority over a simultaneous maximum-holding exit on December 10, 2024. The
accepted engine correctly manages stops, targets and mandatory deadlines before
testing exposure of positions that remain open. The validator was corrected and
a synthetic regression case added. The failed audit receipt is retained as
`audit-attempt-1.json`. This changed neither the frozen execution implementation
nor a trade, price, parameter or historical result. The holdout was not rerun.

## Historical outcomes

The entire predefined matrix is accounted for: **144 evaluated cells and 36
explicitly BLOCKED C4 cells**. Each evaluated cell has 731 dated records: 301
eligible development sessions, 106 validation sessions and 108 holdout sessions.
Weekends and the frozen conservative holiday dates account for the other 216
dates. There are **58,447 completed trades across separate experiments**, zero
invalidated positions and zero unresolved positions. That trade count is a count
of correlated experimental observations, not a jointly executable portfolio.

Every C0 validation and holdout cohort has negative net expectancy. Every C1,
C2 and C3 holdout cohort also has negative net P/L. **None is PROMISING**. The
frozen classifier returns **27 REJECTED** cohorts and **nine INCONCLUSIVE** sparse
mean-reversion cohorts. The latter have only 12-29 holdout trades, below the
predeclared 30-trade rejection threshold and far below the 100 required to be
PROMISING. These conclusions were not used to revise any parameter.

The following ranges compare nine independent pair/session cohorts per rule.
Currency values are GBP; holdout drawdown is marked equity from the start of that
split, not a claim about an unseen intraminute equity path.

| Rule | Development C0 net range | Validation C0 net range | Holdout C0 trades | Holdout C0 net range | Holdout C0 drawdown range | Classification |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| ORB | -119.59 to -64.17 | -39.49 to -24.25 | 106-108 | -39.70 to -15.40 | 15.99-39.70 | 9 REJECTED |
| Momentum | -104.56 to -80.19 | -38.04 to -20.74 | 64-96 | -31.74 to -13.83 | 16.38-32.91 | 9 REJECTED |
| Mean reversion | -55.12 to -32.49 | -25.57 to -8.15 | 12-29 | -9.39 to -3.01 | 5.86-12.64 | 9 INCONCLUSIVE |
| Directional baseline | -110.26 to -69.40 | -38.35 to -20.14 | 97-103 | -31.15 to -23.39 | 25.86-31.25 | 9 REJECTED |

All 36 C0 development cohorts were also negative; no rule demonstrated positive
net performance consistently across any chronological split. C0 holdout profit
factors range from 0.21 to 0.79, with longest losing streaks of 4-22 trades. Exact
unrounded values and per-cohort downside measures remain in the CSV evidence.

Holdout net P/L ranges under all evaluated cost assumptions:

| Rule | C0 base | C1 spread stress | C2 slippage stress | C3 severe stress |
| --- | ---: | ---: | ---: | ---: |
| ORB | -39.70 to -15.40 | -41.78 to -18.25 | -50.04 to -24.40 | -61.95 to -38.54 |
| Momentum | -31.74 to -13.83 | -36.86 to -14.51 | -40.85 to -17.69 | -55.94 to -22.22 |
| Mean reversion | -9.39 to -3.01 | -10.04 to -0.75 | -14.03 to -4.37 | -16.71 to -4.48 |
| Directional baseline | -31.15 to -23.39 | -35.86 to -25.34 | -43.45 to -34.60 | -56.15 to -42.95 |

Changes in spreads can change eligibility, bracket prices and size, so a stressed
cell is not simply its C0 trades with a fee subtracted. No improving stress result
is selected as evidence for a trading edge. Identical New York/overlap results in
some cohorts follow from their shared opening and early entry/holding interval;
they are not independent confirmations of a hypothesis.

The no-trade baseline has zero trades, zero P/L and unchanged GBP 1,000 equity in
each of its nine references. Every active C0 holdout cohort underperformed it.
There is no historical evidence here to justify deploying these rules.

## Quality, risk and robustness interpretation

All session exclusions, signal-window failures, order rejections, market
restrictions and conversion failures remain in the detailed per-date evidence.
Missing pair-minutes within selected windows range from 0-93 per development
cohort, 0-49 in validation and 0-57 in holdout. These are scoped session counts,
not a reclassification of the corpus-wide 7,754 unresolved pair-minutes. They
must not be summed across overlapping sessions or repeated strategy/cost cells.
Observed missing candles prevented affected signals or execution; they were never
forward-filled. No actual open position in this matrix became unresolved.

High-spread and range checks are minute-level eligibility diagnostics: their
counts can exceed the number of signals or rejected orders. Ten C0 ORB session
attempts had incomplete/ineligible opening ranges. Twelve C0 trades used the
conservative stop-first collision policy across the independent experiments.
The seven frozen range-review candles remain intact. Four C0 development trades
across separate ORB/directional cohorts encountered an extreme observation; no
validation or holdout trade did. All holdout apparent profits remain negative,
including after removing the five largest winners. There is no exceptional-profit
dependency supporting a positive claim.

The inherited unleveraged marked-exposure cap is enforced after mandatory exits.
It can close a position when notional value increases beyond available equity,
even before its strategy stop/target or holding deadline. There were 8,134 such
C0 exits across independent experiments. This is an explicit consequence of the
strict inherited no-borrowing contract, not a simulation of broker margin rules.
Its effect must remain visible; it was not relaxed to improve performance.

The files below include gross-model P/L, modeled costs, actual commission,
precommission price P/L, net return, win rate, winner/loser averages, expectancy,
profit factor, marked drawdown, losing streak, holding/exposure duration, best and
worst days, top-five contribution and invalidation rates for each split. Gross
and total costs use the frozen accounting counterfactual described in the design;
they are not measured real-world intrabar transaction costs.

Exploratory expectancy intervals use calendar-week clusters only where at least
30 trades and 20 weeks exist. Sparse cells have unavailable intervals rather
than misleading precision. Within-week trade dependence is respected; cross-week
dependence and cross-cohort correlation can remain. The complete family is 180
planned cells, including 36 blocked cells. Intervals are not multiplicity-adjusted,
and no best-cell significance claim or Sharpe ratio is made. The negative and
sparse results do not demonstrate persistent profitability.

## Evidence and reproducibility

Committed, compact derived evidence:

- [All 180 cell statuses and three splits](phase6b3_results/cell-status-matrix.csv).
- [All scalar split performance and quality metrics](phase6b3_results/performance.csv).
- [Machine-readable reason counts and clustered uncertainty](phase6b3_results/diagnostics.csv).
- [All 36 pair/session/rule classifications](phase6b3_results/cohort-classifications.csv).
- [Manifest/source seals, no-trade references and robustness ranges](phase6b3_results/research-summary.json).
- [Frozen manifest](../examples/forex-research-phase6b3-v1.json) and
  [exact SHA-256 receipt](../examples/forex-research-phase6b3-v1.sha256).

Detailed local-only evidence is under `local/verification/phase6b3-v1/`:
`freeze-receipt.json`, `prerequisites.json`, `results/run-seal.json`,
`results/summary.json`, `results/cell-matrix.csv`, `results/results.sqlite3`,
`results/runtime.json`, the exclusive evaluation/holdout-access receipts,
`robustness.json`, independent audit, preservation inventories and test XML.
The SQLite database stores compressed, separately checksummed per-session
decisions and trade/event evidence. It is derived research output, not modified
source data. Raw source data, local databases and these detailed verification
artifacts are Git-ignored and excluded from the commit.

The bounded historical run took **1,712.9 seconds**, used four workers, and
reported a peak single-process footprint of **108,421,120 bytes**. This is not a
claim of total-machine peak RAM. The derived database is **321,355,776 bytes**.
Only one UTC date of source candles is buffered at a time. There were no matrix
processing failures. Holdout economic evaluation occurred once; the independent
pass verifies saved outputs against sources and does not regenerate holdout
signals. Exact result/hash reproduction and full-engine economic parity are
checked on the fixed 12 development probes, alongside synthetic repeated-run
and cross-process determinism tests.

Preservation verification matched **all 5,216 readable protected artifacts**,
with no added, deleted or changed files in the protected source/research inventory.
Two previously unreadable old Massive reports retain that limitation; they are
not inputs to this Forex experiment. Accepted Forex partition/page hashes were
verified during collection reads and independently reread for trade acceptance.
All **2,193 partitions, 4,386 pages and 2,227,822 candles** remain frozen. Both
Forex database file hashes also match the Phase 6B-1C source-hash receipt.

## Acceptance and next-phase restriction

**Decision: PASS for the integrity of the authorized controlled historical
research.** This does not classify a strategy as profitable or tradable.
Unsupported C4 remains explicitly BLOCKED, as allowed by the predefined design.

The independent audit passed all **105,264 session records** and **58,447 source
trade checks**. All 12 fixed development reproduction probes matched exactly and
retained full-engine economic parity. The audit performed **zero holdout strategy
reruns**. There are no unresolved material execution-integrity findings, no
post-result model changes and no holdout-driven parameter changes. The only
failed assertion was the documented, resolved validator priority error.

Acceptance gates:

| Requirement | Evidence/outcome |
| --- | --- |
| Frozen design before results; exact hash | Freeze receipt, immutable manifest, matching SHA-256 |
| Complete matrix; no optimization | 144 evaluated, 36 explicitly BLOCKED; fixed 180-cell family |
| Splits and single frozen holdout | Per-trade chronology checks and holdout-access receipt |
| Execution contract and conservative costs | Shared engine, source-price/GBP/risk arithmetic checks, declared V2 assumptions |
| Invalidations and exclusions | Every date retained; zero invalidated/unresolved positions; reasons published |
| No material leakage or overnight exposure | Causal-prefix tests and all-trade source/session checks passed |
| Determinism | Cross-process synthetic agreement, 12 exact development reproductions, all saved seals verified |
| Frozen historical preservation | 5,216 readable file hashes unchanged; both Forex databases match Phase 6B-1C |
| Automated validation | 1,664 tests; Ruff lint and all 192 files formatted; dependency check passed |
| Adversarial review | Separate validator passed; false-positive priority assertion resolved without economic changes |
| No unauthorized external actions | No authenticated broker activity, trading, history downloads or notifications |

The detailed [local acceptance receipt](../local/verification/phase6b3-v1/acceptance.json)
and [independent audit](../local/verification/phase6b3-v1/independent-audit.json)
record seals and machine-readable verification. The local checkpoint receipt
records the final commit/push without creating a self-referential committed hash.

No strategy is recommended for deployment or trading. Further offline research
would require a separately authorized, preregistered hypothesis and honest
treatment of the now-examined holdout. This period cannot become a fresh untouched
holdout for a strategy selected or revised using these results. Publication
latency, tick paths, actual executable spreads, broker fills and account-specific
costs remain unverified. Overnight research remains blocked, and Phase 6B-4 is
not started.

No authenticated broker requests, market-data downloads, real or paper orders,
account changes or Telegram notifications occurred.
