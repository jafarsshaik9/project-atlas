# Completed IEX collection validation

**Verdict: READY FOR BACKTESTING: exploratory IEX research with explicitly ASSUMED availability only.**
It is not a complete consolidated-market dataset or point-in-time verified research input. No strategy threshold was changed and no backtest or network request was run during this audit.

Audited at: 2026-10-28T20:43:17.293142+00:00. Manifest `85006fe931e9381c843b9dec9a85b189f6ef2ad02409a9c17ae23e52e485c0d9`; run `5843bb52df9858def548949f76666f268788f642c3ad1dd6212ed0739a809f03`.

## Evidence and integrity

- Rebuilt and validated the frozen calendar manifest and run-plan bindings. Inspected every one of 2,505 ledger partitions, including the reused pilot and five exclusion references.
- Revalidated accepted datasets through HistoricalStore.inspect and feed-specific inspection: manifest/data/quality hashes, raw source-page chains, source projections, timestamps, OHLCV contracts, calendar alignment, provider metadata, and stored quality findings.
- All 2,500 accepted/reused partitions passed; no duplicate dataset IDs or timestamps, out-of-session bars, invalid OHLCV, or SIP/provider mixing were found. Zero audit errors.
- The ledger contains 2,499 ACCEPTED, 1 REUSED, 4 EXCLUDED and 1 preserved QUARANTINED partition. The run is COMPLETED_WITH_GAPS.
- Source database and cache hashes were identical before and after the audit. SQL quick_check passed for all inspected databases. No accepted datasets or checkpoint records were modified.
- Actual available range: 28 October 2024 through 27 October 2026. The calendar contains 501 regular sessions; each symbol has 500 accepted sessions, with 10 March 2025 excluded. Early closes are accounted for: 194,100 expected minute slots per symbol in accepted sessions.
- 959,045 accepted bars match the downloaded cache total. Of 970,500 minute slots in accepted sessions, 11,455 are absent (98.8197% observed). The excluded session adds 1,950 absent slots, yielding 13,405 absent slots out of the original 972,450-slot plan.
- The cache has 501 HTTP response pages. Quota history records 501 full-collection HTTP 200 attempts, one SIP pilot, one IEX pilot, and one diagnostic re-fetch: 504 account-wide attempts. The displayed 502 consumed count is an account-wide delta since collection start and includes the diagnostic request; it is not strictly collection-only accounting. No recorded retries.

## Exact exclusions

| Symbol | Date | Calendar expectation | Recorded cause | Classification |
|---|---|---:|---|---|
| AAPL | 2025-03-10 | 390 minutes | NO_VALID_BARS; repeated HTTP 200 empty batch with no continuation | Approved whole-session data gap |
| AMZN | 2025-03-10 | 390 minutes | NO_VALID_BARS; repeated HTTP 200 empty batch with no continuation | Approved whole-session data gap |
| GOOGL | 2025-03-10 | 390 minutes | NO_VALID_BARS; repeated HTTP 200 empty batch with no continuation | Approved whole-session data gap |
| MSFT | 2025-03-10 | 390 minutes | NO_VALID_BARS; repeated HTTP 200 empty batch with no continuation | Approved whole-session data gap |
| NVDA | 2025-03-10 | 390 minutes | NO_VALID_BARS; repeated HTTP 200 empty batch with no continuation | Approved whole-session data gap |

This was a scheduled regular session (13:30 to 20:00 UTC, 09:30 to 16:00 New York), not a holiday or early-close exclusion. Both original collection and separately authorized re-fetch returned no bars across all five symbols. The underlying provider-side cause is unconfirmed. It must not be labelled a calendar exclusion or conclusively explained as normal sparse trading.

The approval record is bound to this run and session. Keep the operational classification as an approved data gap, retain the quarantined evidence, and disclose the excluded date in all research. A useful future display would distinguish "501 sessions processed, 500 accepted, 1 approved gap" and "5 symbols processed, 0 with complete requested-session coverage". This would change presentation only. `coverage_complete=false` remains correct; `completed_symbols=0` follows the current requirement that every requested session be accepted for a symbol to count as complete.

## Per-symbol coverage

Accepted-session missing minutes below exclude the additional 390 absent slots per symbol on 10 March 2025.

| Symbol | Accepted sessions | Bars | Missing minutes | Sessions with missing minutes | Complete opening 15m | Complete initial 20m |
|---|---:|---:|---:|---:|---:|---:|
| AAPL | 500 | 193,104 | 996 | 193 | 499 | 499 |
| AMZN | 500 | 192,949 | 1,151 | 241 | 499 | 499 |
| GOOGL | 500 | 192,177 | 1,923 | 289 | 500 | 500 |
| MSFT | 500 | 187,116 | 6,984 | 328 | 483 | 477 |
| NVDA | 500 | 193,699 | 401 | 38 | 500 | 500 |

## Per-month coverage (all five symbols)

Counts are symbol-session partitions; divide accepted partitions by five for distinct accepted dates. Complete symbol-by-month detail, warning codes and counts are in the CSV linked below.

| Month | Accepted partitions | Excluded partitions | Bars | Missing minutes in accepted partitions | Missing excluded minutes | Missing-interval warnings (partitions) |
|---|---:|---:|---:|---:|---:|---:|
| 2024-10 | 20 | 0 | 7,725 | 75 | 0 | 12 |
| 2024-11 | 100 | 0 | 37,437 | 663 | 0 | 70 |
| 2024-12 | 105 | 0 | 37,420 | 2,630 | 0 | 73 |
| 2025-01 | 100 | 0 | 38,339 | 661 | 0 | 68 |
| 2025-02 | 95 | 0 | 36,610 | 440 | 0 | 56 |
| 2025-03 | 100 | 5 | 38,660 | 340 | 1,950 | 39 |
| 2025-04 | 105 | 0 | 40,641 | 309 | 0 | 32 |
| 2025-05 | 105 | 0 | 40,282 | 668 | 0 | 63 |
| 2025-06 | 100 | 0 | 38,118 | 882 | 0 | 69 |
| 2025-07 | 110 | 0 | 40,841 | 1,159 | 0 | 83 |
| 2025-08 | 105 | 0 | 40,241 | 709 | 0 | 69 |
| 2025-09 | 105 | 0 | 40,370 | 580 | 0 | 51 |
| 2025-10 | 115 | 0 | 44,106 | 744 | 0 | 51 |
| 2025-11 | 95 | 0 | 36,112 | 38 | 0 | 18 |
| 2025-12 | 110 | 0 | 41,564 | 436 | 0 | 62 |
| 2026-01 | 100 | 0 | 38,845 | 155 | 0 | 42 |
| 2026-02 | 95 | 0 | 36,945 | 105 | 0 | 24 |
| 2026-03 | 110 | 0 | 42,722 | 178 | 0 | 39 |
| 2026-04 | 105 | 0 | 40,673 | 277 | 0 | 40 |
| 2026-05 | 100 | 0 | 38,983 | 17 | 0 | 11 |
| 2026-06 | 105 | 0 | 40,947 | 3 | 0 | 3 |
| 2026-07 | 110 | 0 | 42,895 | 5 | 0 | 4 |
| 2026-08 | 105 | 0 | 40,830 | 120 | 0 | 34 |
| 2026-09 | 105 | 0 | 40,793 | 157 | 0 | 43 |
| 2026-10 | 95 | 0 | 36,946 | 104 | 0 | 33 |

## Warnings and ORB suitability

All 2,500 accepted partitions carry ALPACA_METADATA_DECLARED, AVAILABILITY_UNKNOWN, CORPORATE_ACTION_COVERAGE_INCOMPLETE, IEX_LIMITED_COVERAGE and SURVIVORSHIP_UNASSESSED warnings. MISSING_INTERVALS affects 1,089 partitions. There are no other accepted-partition warning codes in this audit.

Missing minutes inside accepted sessions are compatible with sparse IEX coverage, but the stored response alone does not establish whether a particular absence means no qualifying IEX trade or a provider omission. Preserve these intervals as unknown. Do not fill with zero-volume bars, interpolate or substitute SIP.

There are 186,103 complete five-minute candles. The current ORB defaults to a 15-minute opening range: 2,481/2,500 symbol-sessions contain all its opening minutes, and 2,475 contain an uninterrupted first four candles (opening range plus one potential trigger candle). This is structural eligibility only; volume, tick size, availability, later gaps, signals, risk and execution can still reject trading. Existing strict contiguous-candle checks remain unchanged. MSFT has the greatest missing-minute coverage: 6,984 absent minutes and 483 complete opening ranges.

All 959,045 bars have unknown original publication availability. VERIFIED mode cannot treat them as point-in-time visible. Use an explicitly labelled ASSUMED availability policy, explicit FX/cost/liquidity assumptions, chronological splits and the existing risk/accounting rules. Raw prices have incomplete corporate-action coverage; results must not claim total-return accuracy or a point-in-time universe. No claim of SIP-equivalent prices, volumes or signal performance is justified.

## Targeted repair proposal

No further download is necessary to run the approved incomplete-data research. To restore complete session-date coverage, investigate only 10 March 2025. With separate future authorization, first query one affected symbol directly to distinguish a batch-specific empty response from missing symbol history; if data is available, collect only the five affected symbol/session partitions with explicit IEX/raw parameters and the shared persistent limiter. Follow bounded pagination and retries.

Import any repair into a separate immutable candidate store. Require normal source/quality/calendar validation, preserve both original quarantines and exclusion evidence, and publish a new reviewed research-catalog revision selecting the repaired partitions. Do not overwrite accepted data, silently remove the exclusion binding, change to SIP or redownload the two-year history. If IEX still returns empty data, retain the documented gap and seek provider clarification. Sparse intraday minutes should be repaired only with new source evidence, never manufactured.

## Detailed artifacts

- [Per-symbol/per-month CSV, including warning counts](../local/research/two-year/coverage-audit/coverage_by_symbol_month.csv)
- [All 2,505 partition audit rows](../local/research/two-year/coverage-audit/partitions.csv)
- [Machine-readable audit summary](../local/research/two-year/coverage-audit/coverage.json)
