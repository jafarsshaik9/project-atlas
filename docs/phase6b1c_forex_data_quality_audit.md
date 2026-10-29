# Phase 6B-1C: full OANDA corpus audit

Audit date: 29 October 2026. Starting HEAD: `e723ba5` (approved resumable
historical collection); working tree was clean. The complete Phase 6B-1C requirements
were received during the audit and incorporated. No trading strategy or performance
claim is made.

**Decision: CONDITIONAL PASS for research and carefully constrained intraday
backtesting preparation; BLOCKED for overnight simulation and unrestricted execution
claims.** Collection completion and internal integrity do not establish historical
execution fidelity. Missing-minute treatment, availability, fill assumptions and
currency conversion still require explicit replay policies.

## Scope and evidence

The frozen Stage B plan covers EUR_USD, GBP_USD and USD_JPY, OANDA live historical
M1 MBA candles, unsmoothed, from **2024-10-28 00:00 UTC through 2026-10-28
22:50 UTC, exclusive**. The requested end was 2026-10-29 00:00 UTC. Thus **70
minutes per pair / 210 pair-minutes remain outside the frozen collection**. This
is a planning-time cap, not a failed download; extending it requires new partitions
under a new plan rather than modifying accepted records.

Plan hash:
`a9962e5fcebe922f084c1218601a57249297ebd18bcc1ccbb8598b9b35ebf406`.

Local evidence is under `local/verification/phase6b1c-run2/`:

- `audit.json`: full source audit, exact spread quantiles, anomaly records,
  per-pair/month statistics, missing intervals shared across pairs and readiness.
- `partitions.jsonl`: one record per planned partition, with dataset ID, original
  recomputed quality-v1 diagnostics and additional gap hypotheses.
- `context.json`: independent gap timing, closure-boundary observations, quota
  status, pilot linkage and discovery metadata checks.
- `details.json`: session/month/hour spread distributions including p90, seven
  outlier case studies with neighboring candles, missing-minute distributions,
  session denominators and versioned calendar reclassification counts.
- `missing-minutes.jsonl.gz`: exactly one detailed record per unexplained missing
  pair-minute, including previous/next observed candles, UTC and New York times,
  session, rollover/DST proximity, shared instruments and gap duration.
- `source-hashes-before.json`: preservation baseline for existing data/research
  artifacts. Verification outputs are deliberately outside those source trees.
- `usability.json`: residual missing-minute exposure by session and shared-gap totals.
- `validation.json`: final test results, code/artifact hashes and detailed evidence checks.

Final source verification passed for all 2,193 partitions and 4,386 pages; both
SQLite integrity checks returned `ok`, with no missing/extra jobs, datasets or
ledger entries, no orphan pages and no time regressions. The existing final
collection report reproduced exactly. Hashes for **5,216 readable artifacts were
unchanged** out of 5,218 inventoried files. Two old Massive JSON reports denied
read access (`massive-pilot/report.json` and
`massive-pilot/report-zero-attempt-config-stop-20261027.json`); their content hashes
could not be verified. They were not edited. All stock and Forex databases in
the inventory were readable and unchanged. The enrichment pass separately
rechecked both Forex database hashes before and after its work.

The audit uses SQLite `mode=ro`, runs full database integrity checks, and visits
every planned partition. Existing validators check manifest identity, plan/job
identity, checkpoint/page ordering and hashes, all candle schemas and exact Decimal
OHLC values, components, complete flags, minute-aligned source timestamps, time
bounds, strict ordering and saved quality checksums. Additional checks cover extra
or missing ledger/jobs/datasets, orphan pages, cross-partition time regressions,
spread quantiles, flat candles, zero activity, repeated closes, midpoint-close
residuals, large ranges and price discontinuities. Source memory is bounded to one
daily partition. Diagnostic summaries grow with the finite plan.

This establishes internal consistency of stored normalized observations. Original
HTTP response bytes are not retained; checksums cannot independently authenticate
what OANDA originally sent, nor detect a coordinated rewrite of all records and
their hashes. No authenticated provider requests were made for this audit.

## Coverage and gap findings

| Pair | Candles | Unexplained missing minutes | Holiday-date minutes | Other dates, NY 17:00 hour | Other minutes |
| --- | ---: | ---: | ---: | ---: | ---: |
| EUR_USD | 742,632 | 8,284 | 5,761 | 1,929 | 594 |
| GBP_USD | 742,948 | 7,987 | 5,777 | 1,844 | 366 |
| USD_JPY | 742,242 | 8,691 | 5,856 | 2,675 | 160 |
| Total | 2,227,822 | 24,962 | 17,394 | 6,448 | 1,120 |

“Holiday-date” means December 24/25 and December 31/January 1 in New York, not
that every minute on those dates is proven closed. Buckets are disjoint. The
`audit.json` gap-candidate classification instead labels whole intervals by their
start and uses a narrower 17:05–17:15 reopening heuristic; it should not be confused
with this minute-by-minute table from `context.json`.

All 2,193 planned partitions are represented: **1,881 with observations and 312
observed-empty Saturday partitions**. There are no pending or quarantined partitions.
The source plan assumes 2,249,736 open pair-minutes. Of those, 2,224,774 are observed
(98.89045%); another 3,048 candles occur inside assumed closures. Missing minutes
classified as expected daily breaks total 5,098; assumed weekend absences total
899,828. Do not use a 24/7 denominator as a Forex coverage score.

The largest missing intervals repeat across all three pairs around Christmas and
New Year. Twelve intervals of 115 minutes and twelve of 1,319 minutes account for
17,208 missing pair-minutes. UTC midnight splits each holiday absence across
partitions. The current quality-v1 calendar has no holiday exceptions, explaining
why those absences remain “unexplained” in the immutable report.

[OANDA's UK holiday schedule](https://www.oanda.com/uk-en/trading/holiday-trading-hours/)
lists late Forex reopening on January 1, 2025, December 25, 2025 and January 1,
2026, supporting this explanation. The older
[December 2024 OANDA notice](https://www.oanda.com/eu-fr/node/358974) is from a
different regional offering and is supporting context only. These are not an
account-specific historical calendar. No automatic holiday reclassification or
exclusion was applied.

Short gaps concentrate after rollover, but absence of a candle cannot distinguish
no price updates from provider/source outages. HTTP success does not resolve that
ambiguity. The 1,120 remaining minutes require conservative handling too. No gap
was filled, prices interpolated, or observed candles dropped. Missing 12:00 UTC
page-boundary observations occur on the four holiday dates per pair; the evidence
does not indicate a systematic half-day pagination loss.

There are 5,800 UTC missing timestamps shared by all three pairs, 807 shared by
exactly two pairs, and 5,948 affecting exactly one pair; weighted total:
`5,800*3 + 807*2 + 5,948 = 24,962`. Duration distributions report both interval
counts and minute-weighted counts. The largest original unexplained interval is
1,319 minutes, a holiday portion cut at UTC partition boundaries; the compressed
per-minute evidence also records the full observed-to-observed absence across
those boundaries. There are 3,590 one-minute and 753 two-minute unexplained
intervals. Detailed monthly/hour/session distributions are in `details.json`.

| Potential cause | Finding |
| --- | --- |
| A: absent provider observations | Confirmed absent in the stored accepted responses; whether OANDA had no updates or omitted data cannot be established offline. |
| B: calendar classification | 17,208 minutes fit narrow holiday closures: 12,906 supported by UK historical entries and 4,302 by regional Christmas-2024 context. V2 preserves their evidence qualifications. |
| C: pagination/request boundaries | No observed defect: every planned page is committed, hashes and page bounds pass, and midday absences are holiday-related. This does not certify completeness of the upstream responses. |
| D: partition/timestamp defects | No observed defect: strict minute alignment, source-time equality, chronology, plan scopes and cross-partition uniqueness pass. The collector neither shifts timestamps nor deduplicates them silently. |
| E: interrupted/incomplete collection | No remaining incomplete jobs, pages, ledger records or requests. Pilot reuse and final request counts reconcile. |
| F: unresolved | 7,754 minutes remain outside narrow holiday intervals: EUR_USD 2,548; GBP_USD 2,251; USD_JPY 2,955. No-update/outage causes remain unknown; apply conservative replay rules. |

All 3,048 closure warnings occur at **16:59 or 17:04 New York**, with DST applied.
This is a systematic boundary discrepancy between actual candle observations and
the calendar assumption, rather than random weekend trading. The
[published hours](https://www.oanda.com/uk-en/trading/hours-of-operation/) describe
16:59–17:05 breaks; the stored data includes the closing minute and the minute
before the published reopening. Preserve source timestamps. A future versioned
calendar overlay may distinguish these observed boundary minutes, but should not
retroactively claim verified trading availability or alter quality-v1 hashes.

The implemented `OANDA_OBSERVED_BOUNDARIES_HOLIDAY_EVIDENCE_V2` overlay now makes
that distinction explicit. It labels observed boundary candles as requiring review,
and narrowly identifies missing minutes inside the supported holiday intervals.
Christmas 2024 receives a separate, regionally supported but unverified label.
It does not change the collection calendar, grant execution permission, or rewrite
quality-v1 evidence. Both original and revised labels accompany every missing minute.
US DST governs the broker calendar; UK DST only affects London research sessions.
Tests cover the weeks when the US and UK offsets change on different dates.

## Prices, provenance and execution limits

| Pair | Mean, pips | Median | p90 | p95 | p99 | Minimum | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EUR_USD | 1.690066 | 1.6 | 1.8 | 1.9 | 4.7 | 1.4 | 10.0 |
| GBP_USD | 2.189144 | 1.9 | 2.2 | 2.6 | 11.3 | 1.5 | 20.0 |
| USD_JPY | 1.833237 | 1.7 | 2.0 | 2.3 | 8.5 | 1.2 | 10.0 |

These are ask-close minus bid-close statistics, not simultaneous executable quotes.
Exact nearest-rank p50/p90/p95/p99 values are retained in `details.json`; hourly fixed-bin
distributions remain in the source report. The existing diagnostics found no
negative endpoint spreads, precision-beyond-display warnings, midpoint-close
envelope violations or spreads over 25 pips. These results are limited to the
specified diagnostics, not proof of executable prices or a complete cross-component
microstructure model.

There are no zero or negative close-derived spreads. Pips are 0.0001 for EUR_USD
and GBP_USD and 0.01 for USD_JPY. Components share a candle timestamp; underlying
tick synchronization cannot be recovered. Mean spreads during 16:45–17:30 New
York are 3.483 / 7.380 / 5.205 pips (EUR/GBP/JPY); Sunday 17:00–18:00 means are
4.686 / 10.895 / 6.915. Friday 16:00–17:00 means are 1.906 / 2.617 / 2.273.
These windows intentionally include boundary observations for diagnosis, not
execution authorization. Month, UTC-hour, New-York-hour and disjoint research
session distributions and the largest partition-boundary spread changes are
retained in `details.json`. Percentiles use exact observed Decimal values and
nearest-rank weighting; grouped windows overlap and must not be added together.

The saved quality report flags seven minute ranges over 100 pips and 25
cross-partition moves over 50 pips. The latter span Friday-to-Sunday closures and
must not be mistaken for consecutive-minute price errors. Exact timestamps,
bid/ask/mid OHLC, dataset references and elapsed-time distinctions are in the new
anomaly records. Large moves alone do not justify clipping or deleting data;
external price confirmation remains outside this offline integrity audit.

All seven large-range observations were located. The table reports absolute
midpoint price discontinuities at the immediate previous/current and current/next
joins. Every neighbor is one minute away and both joins are small compared with
the candle range. Full bid/ask/mid OHLC, activity, neighbor OHLC, dataset IDs and
pip arithmetic are in `details.json`, `large_range_case_studies`.

| Pair | UTC timestamp | Research session | Range pips | Spread pips | Previous/next join pips | Assessment |
| --- | --- | --- | ---: | ---: | --- | --- |
| GBP_USD | 2025-01-10 13:30 | Overlap | 101.1 | 2.3 | 0.1 / 0.0 | Plausible continuous volatility; externally unverified |
| USD_JPY | 2025-02-12 13:30 | Overlap | 105.6 | 2.1 | 1.8 / 0.3 | Plausible continuous volatility; externally unverified |
| USD_JPY | 2025-07-03 12:30 | Overlap | 119.9 | 3.2 | 0.9 / 0.1 | Plausible continuous volatility; externally unverified |
| USD_JPY | 2026-01-23 07:43 | Outside defined windows | 143.2 | 2.8 | 0.1 / 0.0 | Plausible continuous volatility; externally unverified |
| USD_JPY | 2026-01-27 09:52 | London only | 110.6 | 3.7 | 0.1 / 0.4 | Plausible continuous volatility; externally unverified |
| USD_JPY | 2026-02-11 13:39 | Overlap | 138.6 | 3.1 | 0.3 / 1.0 | Plausible continuous volatility; externally unverified |
| GBP_USD | 2026-03-23 11:05 | London only | 118.7 | 4.0 | 0.2 / 0.6 | Plausible continuous volatility; externally unverified |

The next candle does not instantly return to the pre-event level in these cases;
activity counts range from 2,052 to 3,080. No pip-conversion or Decimal precision
defect was found. This supports retention rather than clipping and does not prove
the exact historical event or account execution. Until reviewed, reject new risk
in affected signal windows and retain adverse handling for existing positions.

Discovery instrument metadata matches the plan, and Stage B binds the correct
Stage A hash. The persistent request ledger has **4,388 HTTP 200 responses and
zero unfinished attempts**: two discovery requests plus 4,386 candle requests,
consistent with two pages per partition and pilot reuse. Approval signatures were
not re-signed or independently authenticated; doing so would require credentials.

[OANDA's candle definition](https://developer.oanda.com/rest-live-v20/instrument-df/)
defines timestamps as candle starts, volume as a count of prices, and complete
relative to the candle ending time. The corpus does not record original publication
times or account-specific executable quote depth. Collection fetch timestamps must
not be used as historical availability, and activity counts must not inherit the
stock strategy's traded-volume filter.

## Safe correction and validation

`ForexStore` and `CollectionLedger` now support read-only connections. Report and
dataset consumers use them, so reading a nonexistent corpus cannot create empty
databases or indexes. The pilot CLI retains its explicit incomplete-pilot error.
This corrects an audit side effect without changing any accepted partition,
manifest, quality function or collection behavior. The new audit command writes
only to a new directory below `local/verification/` and refuses output overwrite.

Eleven new regression cases cover absent-source creation prevention, database write
rejection, full synthetic scope, corrupt pages, tampered quality records, weighted
quantiles, versioned holiday/boundary classifications, US/UK DST mismatch sessions,
and missing-minute bracket/reconciliation checks. The full suite includes the
existing Forex and stock tests: **1,418 passed in 251.07 seconds**, with 653 existing
deprecation warnings. Ruff lint and formatting checks and `pip check`
passed. The suite emitted existing Starlette/pandas deprecation warnings.

The final evidence check independently read all 24,962 compressed minute records,
verified unique instrument/timestamp keys and strict previous/next bracketing,
validated the three report checksums and confirmed all seven outlier neighborhoods
are contiguous. This supplements the synthetic regression cases.

## Backtesting readiness gates

Exploratory data analysis can use the validated observations now. A realistic
historical replay still needs all of the following concrete policies:

1. Restrict the declared range to the frozen effective end or collect the separate
   missing tail under an appropriate new plan. Never silently extend coverage.
2. Reject signal windows with missing required M1 slots; do not forward-fill prices
   or conversions. Decide explicitly how an already-open position behaves through
   gaps, including unavailable exits. Keep denominators and exclusions visible.
3. Exclude holiday/rollover windows conservatively until a versioned historical
   calendar and gap policy are approved. Candidate labels alone grant no exemption.
4. Use an explicit ASSUMED bar-close availability model with positive decision/fill
   sequencing; completed OHLC cannot be consumed at its start. Merge instruments
   chronologically before shared-capital decisions. Partition iteration is not a
   globally ordered event stream.
5. Model long entries at ask and exits at bid, with opposite sides for shorts,
   explicit slippage/commission, adverse gap-through stops and conservative
   stop/target ordering when both are touched. Do not treat historical endpoint
   components as exact quotes or infer intraminute order from OHLC.
6. Build timestamp-safe GBP conversion: USD/GBP from GBP_USD and JPY/GBP through
   USD_JPY and GBP_USD, with the appropriate conversion sides and freshness tests.
   Never use later closes to translate earlier P/L. Existing Forex accounting is
   a synthetic foundation, not a complete historical execution runner.
7. Supply financing and effective-dated product rules for rollover holdings, or
   enforce flat-before-rollover rules that also address unavailable exit prices.
   Current discovered size/margin metadata is not a verified two-year rules history.

No profitability, parameter optimization or backtest performance result was
produced. Readiness remains conditional even if every source integrity check passes.

## Deterministic data-quality contract for Phase 6B-2

Contract version: `FOREX_M1_CONSERVATIVE_REPLAY_V1_DRAFT`. This specifies a future
engine; this phase does not implement or run that engine. Assumed execution must
be labeled ASSUMED throughout. Use configurable costs supplied before evaluating
strategy performance, with no tuning against this corpus.

| Condition | Required behavior |
| --- | --- |
| Missing candle in the signal or opening-range window | Reject the signal/window; log every expected slot and exclusion. Do not interpolate or reuse a previous close. |
| Missing candle at a scheduled entry | Cancel that entry opportunity; do not move it to a favorable later price. A fresh signal requires a fresh complete window. |
| Missing candle at an exit or while a position is open | Mark the position and affected result unresolved. Keep its risk and outstanding position visible; withhold a complete-performance claim until resolved. Never drop that trade from the denominator. |
| Stop may have been crossed inside a gap | Do not assume a stop-price fill. On resumption, report the first observable adverse-side execution estimate with configured slippage as a stress scenario, and separately retain the unknown-path result. No best-case target fill. |
| Bid/ask missing, invalid, crossed, or candle incomplete | No new signal or fill from that candle; existing positions enter the unresolved path. Never substitute midpoint execution. |
| Indicative endpoint spread exceeds 5 pips | Reject new risk under this fixed conservative screening policy. Existing risk must still exit on the available adverse side with costs; never postpone an exit merely to wait for a narrower spread. The 5-pip rule is a declared research constraint, not an optimized or broker-guaranteed threshold. |
| Closed, unknown, or v2 observed-boundary session status | No new risk. For existing positions apply the gap/unresolved-exit rules. Observed candles remain in the source and risk review. |
| Price discontinuity between observations | Use the next observable adverse-side price for an assumed market fill, not the previous close or stop. An actual missing interval remains separately unresolved. |
| Stop and target both touched within M1 | Stop first, with gap-through treatment and costs. Long exits use bid, short exits ask. Independent bid-high/ask-low extremes cannot establish event order. |
| Signal based on completed M1 OHLC | Earliest assumed availability is bar end plus an explicit nonnegative publication delay. Entry uses a later eligible observation, never that candle's earlier open; deterministic ties must put signal creation before eligible later fills. |
| Outlier review candle in a new-entry window | Reject new risk until case review is explicitly accepted. For an existing position retain adverse range/stop treatment; exclusion sensitivity cannot erase the position or its loss. |
| Stale or missing GBP conversion | No new entry; do not value realized P/L using future rates. Mark valuation unresolved until an admissible conversion is available. |
| No valid exit before mandatory flat time | Keep the position unresolved; do not invent liquidation. The intraday result fails completeness rather than silently becoming an overnight trade. |

Replay results must report accepted, excluded and unresolved opportunities and
positions separately. A run with unresolved position paths cannot publish a single
complete return/Sharpe/drawdown result. Provide conservative scenarios and selection
sensitivity separately; an excluded session is not a zero-loss observation. Preserve
all source IDs, calendar versions, costs, availability assumptions and exclusion reasons.

## Strategy-specific readiness methodology

These are categorical decisions, not an invented numerical quality score.
All categories inherit verified internal provenance and complete MBA fields for
observed candles. Session counts use weekday 08:00–17:00 in London/New York,
with overlap as the intersection and end-exclusive boundaries. These are research
windows, not proof of instrument trading entitlement. `details.json` reports
expected-open minutes and unexplained missing minutes for each disjoint session;
London includes LONDON_ONLY plus OVERLAP, and New York includes NEW_YORK_ONLY plus OVERLAP.

| Disjoint session | Assumed-open minutes per pair | EUR missing | GBP missing | JPY missing | EUR / GBP / JPY residual after narrow holiday overlay |
| --- | ---: | ---: | ---: | ---: | --- |
| London only | 154,320 | 1,201 | 1,201 | 1,200 | 1 / 1 / 0 |
| Overlap | 128,100 | 961 | 960 | 960 | 1 / 0 / 0 |
| New York only | 153,797 | 1,425 | 1,330 | 1,308 | 229 / 134 / 112 |
| Outside those windows | 313,695 | 4,697 | 4,496 | 5,223 | 2,317 / 2,116 / 2,843 |

Residual counts come from `usability.json`; holidays remain qualified assumptions,
not proven full coverage. London and overlap have substantially lower residual
missing exposure, but still contain six of the seven range-review cases. Good
minute coverage therefore does not eliminate execution and outlier risk.

| Use | Decision | Conditions and material risks |
| --- | --- | --- |
| General historical descriptive research | CONDITIONAL PASS | Keep gaps and extreme observations visible; report actual sample/coverage and indicative spread semantics. No executable-price claim. |
| London-session strategies | CONDITIONAL PASS | Require complete signal/entry windows, weekday London DST conversion, outlier policy and the execution contract. London-only gaps and overlap gaps both matter. |
| New York-session strategies | CONDITIONAL PASS | Same requirements; avoid the rollover boundary and handle macro-time volatility and materially wider spread tails. |
| London/New York overlap | CONDITIONAL PASS | Compute the intersection per date, not fixed UTC hours. Exclude incomplete windows and review overlap outliers. |
| Opening Range Breakout | CONDITIONAL PASS for preparation only | Require every minute of the declared opening range and confirmation; define which local opening is intended. Do not inherit the equity volume filter or same-bar fills. No ORB results produced here. |
| Intraday long/short backtesting | CONDITIONAL PASS for preparation only | Implement and test bid/ask directionality, GBP conversion, costs, flat-time failure and unresolved-path accounting first. Current dataset acceptance alone is insufficient. |
| Overnight holding | BLOCKED | Missing path risk around closures, unverified historical financing/product rules, execution gaps and calendar uncertainty are unresolved. Intraday assumptions cannot be reused to claim overnight realism. |

Gate disposition before any Phase 6B-2 replay:

| Gate | Audit disposition | Blocking scope |
| --- | --- | --- |
| Source integrity and partition hashes | PASS, all planned partitions and pages verified | No remaining internal-integrity blocker |
| Missing-data behavior | Specified above; engine implementation/tests still required | All performance backtests until implemented |
| Session classifications | V1 retained; V2 diagnostic overlay and conservative rules supplied | Boundary/holiday trading and overnight; intraday only under conservative rules |
| Bid/ask execution assumptions | Documented; execution adapter not built | All performance backtests until implemented/tested |
| Outlier review | Seven local-sequence case studies; external authenticity unverified | New risk involving flagged windows unless explicitly reviewed; active risks cannot be discarded |
| Spread/cost limitations | Described; fixed spread screen and separate costs required | All strategy claims without explicit cost configuration |
| Look-ahead | No replay engine or availability upgrade introduced; chronological contract supplied | Future engine must pass no-look-ahead tests |
| No live pathways | GET-only provider transport unchanged; no authenticated calls/orders | PASS for this phase |
| Strategy-specific suitability | Matrix above | Overnight blocked; intraday conditional |
| Findings reviewed | Pending user review | Do not begin Phase 6B-2 in this task |

## Reproduction

From the repository root, use a new output directory each time:

```powershell
.venv/Scripts/python.exe -m atlas.forex.audit --plan local/oanda/phase6b1/stage-b-plan.json --output local/verification/phase6b1c-new
.venv/Scripts/python.exe scripts/forex_audit_context.py --plan local/oanda/phase6b1/stage-b-plan.json --output local/verification/phase6b1c-new/context.json
.venv/Scripts/python.exe -m atlas.forex.audit_details --plan local/oanda/phase6b1/stage-b-plan.json --audit-directory local/verification/phase6b1c-new
.venv/Scripts/python.exe -m pytest -q
```

An aborted run has no final `audit.json`; do not treat intermediate JSONL as a
completed audit. The first attempted audit stopped on an unreadable historical
report before scanning. The successful-path implementation records unreadable
files explicitly instead of overstating preservation coverage.
