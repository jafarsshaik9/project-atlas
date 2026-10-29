# Lean Forex profitability validation

29 October 2026. **Decision: DATA REQUIRED.** No new performance evaluation was
run. Technical acceptance passes for this evidence audit and preregistration;
neither profitability nor executable broker pricing has been established.

## Evidence and exposure

Started on `main` at `7ed14906913ac9ddd9854e259b92801b5a85061d`, matching the
private remote. One untracked file, `src/atlas/forex/contract_readiness.py`, belongs
to interrupted Phase 6B-6. Its bytes are preserved and excluded from this commit;
the working tree therefore retains that unfinished work. No new infrastructure
or strategy implementation was added.

Read-only SQLite manifest enumeration found the sole production Forex history:
`local/oanda/two-year/historical.sqlite3`, with 2,193 partitions / 4,386 pages:

| Pair | M1 candles | Available UTC interval, half-open |
| --- | ---: | --- |
| EUR/USD | 742,632 | 2024-10-28 00:00 to 2026-10-28 22:50 |
| GBP/USD | 742,948 | Same |
| USD/JPY | 742,242 | Same |

Other historical stores contain Alpaca IEX/pilot/refetch stock data, a Massive
pilot and an artificial smoke fixture. No separate OANDA price files or later
Forex corpus were found. Copies or partition subsets are not independent evidence.

The [frozen Phase 6B-3 study](phase6b3_frozen_research_design.md) evaluated
development through 2026-01-01, validation through 2026-06-01, and final holdout
through 2026-10-28 22:50. Its [acceptance](phase6b3_forex_research_acceptance.md)
records all 144 evaluated cells and 36 blocked delay cells. Blocking one execution
scenario did not preserve a fresh chronological sample. Phase 6B-4 attributed
the saved results; Phase 6B-5 used them for redesign. No original tuning is alleged,
but **all three periods have now informed diagnostics and hypothesis selection**.
An untraded minute, different pair or new chronological split does not undo that
exposure. Performance evaluation stops here as instructed.

## One frozen hypothesis and economics

**First-hour cost-filtered London EUR/USD continuation.** Information-driven
opening-hour repricing might persist when the initial displacement is substantial
relative to friction. This is an economic hypothesis, not an observed edge.
EUR/USD is selected for liquidity economics, not an old winning cell.

At 09:00 London, use the 60 complete eligible candles from 08:00. Trade the sign
of the midpoint open-to-close return only if its magnitude is at least 0.15% and
its price displacement is at least five times estimated round-trip friction.
Attempt once daily, with next eligible candle-close execution, 0.15% fill-relative
stop, 0.30% target and maximum 120-minute intraday holding. Existing rollover,
session, gap and stop-first rules apply. Compared with the failed 15-minute ORB
and rolling 20-minute momentum rules, this fixes one decision time, requires an
hour's displacement and explicitly screens observable costs. Changing those
features is not evidence of improvement; their choice is informed by exposed data.

Retain GBP 1,000, maximum GBP 5 planned risk, GBP 10 aggregate risk/daily loss,
one position and full adverse-notional cash funding, including short exposure.
Sizing respects available equity/cash and integer units; no leverage, funding
relaxation, deposits or risk compounding. GBP 5 is a ceiling, not a required risk
spend or guaranteed loss cap. Prior near-total capital reservation may still make
effective risk much smaller or cause exposure-cap exits.

Base costs are matched indicative bid/ask, 0.5 pip adverse slippage per side and
assumed GBP 0.00005/unit/side commission. Prespecified stresses are 1.5x spread and
1.5-pip slippage separately. The cost gate uses signal-time prices/conversion only.
These are conservative research assumptions, not a verified broker tariff;
unknown publication latency and execution realism remain blockers to strong claims.

The [machine-readable preregistration](../examples/forex-lean-profitability-v1.json)
freezes the complete formulas, funding, metrics, decisions and stop rules. SHA-256:

`6f84852343fbeb0971bf94ebb7a2c670c3ae60c7ce04212d8830503b2356a4a9`

## Results, uncertainty and review

| Requested measure | This hypothesis |
| --- | --- |
| Net P&L, expectancy, profit factor, drawdown | Unavailable: not evaluated |
| Trade count, capital utilization, cost sensitivity | Unavailable: not evaluated |
| Expectancy uncertainty / robustness | Not estimable without independent outcomes |

Preregistered prospective evidence is 26 calendar weeks, **2026-11-02 to
2027-05-03 UTC exclusive**, with contemporaneous EUR/USD and GBP/USD conversion
observations. None is available now. Require at least 100 completed trades and
adequate active-week coverage; report sparse outcomes as inconclusive. Freeze
two-/four-week block-bootstrap uncertainty, cost stresses, largest-five-winner
removal and half-sample descriptions without choosing a favorable variant.
No sample-boundary move or retuning after outcomes is permitted.

A separate critical pass by the same agent, not external certification, checked
leakage, cost units, funding and selection bias. Findings: the old holdout cannot
be recycled; assumed costs/zero publication delay cannot prove executable edge;
unknown exits cannot become zero losses; overlapping old cohorts are correlated.
The existing frozen matrix runner accepts only its original strategy names: any
later evaluation needs a small reviewed adapter, not substitution under an old
label. No implementation or evaluation was attempted here. Material cost/model
changes require a new preregistration before the future sample is examined.

## Technical acceptance and next decision

Full existing suite: **1,757 passed**, zero failures/errors/skips, 653 existing
warnings, 404.04 seconds. It started before this lean task during Phase 6B-6
preflight and completed unchanged. Targeted existing leakage/split/missing-window
checks: **14 passed, 131 deselected**. No new tests were warranted for documentation
and JSON. All **152 Git-tracked Python files** pass Ruff lint/formatting;
`pip check` passes. Manifest arithmetic/configuration values validate against the
existing immutable execution types; the old frozen design hash still loads.

Preservation verified **5,287 readable files**, including the original 5,216 and
prior verification evidence. Two previously unreadable Massive reports remain
unverified. Local inventories, freeze receipt, schema checks, targeted XML and
review evidence are in `local/verification/lean-profitability/`; full-suite XML
is `local/verification/phase6b6-baseline.xml`. No protected result was rewritten.

**Next decision:** obtain separate authorization for suitable genuinely later
evidence and resolve cost/latency provenance before evaluation. No data collection
is authorized by this report. Do not begin another phase automatically.
No broker API requests, trades, market-history downloads, credential access,
account changes or Telegram notifications occurred in this task.
