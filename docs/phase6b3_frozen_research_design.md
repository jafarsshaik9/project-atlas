# Phase 6B-3 frozen historical Forex research design

Experiment: `PHASE6B3_PREDEFINED_INTRADAY_V1`. Starting checkpoint:
`ab08e9c12a50a3c6218c3b21a0a26e5f95b6f4b2`, `main`. Preflight confirmed the
remote checkpoint, clean working tree, 1,512 baseline tests, prior evidence seals,
execution-source hashes and 5,216 unchanged readable protected artifacts. The two
older unreadable Massive reports remain explicitly unverified.

The complete continuation of the user's Phase 6B-3 instructions was incorporated
before any historical strategy result was evaluated. The early work was read-only
inspection and baseline validation; it did not consume a strategy holdout.

## Preregistration and source scope

The machine-readable manifest is
[`examples/forex-research-phase6b3-v1.json`](../examples/forex-research-phase6b3-v1.json).
Its exact UTF-8 file SHA-256 is:

`b3031e70acb3b1a801dcec2cef1bb394b659ff9333ac7fa4f44c8a79a8b6b499`

The adjacent `.sha256` file records it. A narrow `.gitattributes` rule disables
newline translation for that JSON file, so Git checkouts preserve the exact
hashed bytes. The loader refuses any content change. A separate local freeze
receipt and copy were written before the first historical evaluation. Model
source fingerprints, smoke evidence and full-suite prerequisites were sealed
before starting the matrix; wall-clock duration and RSS are separate observations
and do not enter economic result hashes.

Only the existing immutable OANDA M1 MBA corpus is used: EUR_USD, GBP_USD and
USD_JPY, 2024-10-28 00:00 UTC through **2026-10-28 22:50 UTC exclusive**. The
exclusive endpoint includes the actual final candle starting at 22:49. There is
no added data, source repair, timestamp rewrite, interpolation or capped price.
All 2,193 partitions / 4,386 pages remain tied to their original source proofs.

| Split | Inclusive start UTC | Exclusive end UTC |
| --- | --- | --- |
| Development | 2024-10-28 00:00 | 2026-01-01 00:00 |
| Validation | 2026-01-01 00:00 | 2026-06-01 00:00 |
| Final holdout | 2026-06-01 00:00 | 2026-10-28 22:50 |

No boundary is moved in response to outcomes. All positions must exit inside their
session and split. The final matrix has one frozen evaluation; parameters are not
selected using development, validation or holdout results. Verification repeats
only a prespecified development subset. Holdout verification reconstructs saved
ledgers and source-price arithmetic instead of generating a second strategy run.

## Exact hypothesis family

There are **180 active cells**: three pairs × three independent session cohorts ×
four active rules × five cost scenarios. All cells receive a deterministic status.
There are also nine independent pair/session no-trade baselines, with unchanged
GBP 1,000 capital, no trades and zero P/L. London, New York and overlap follow
the Phase 6B-2 IANA/DST-aware calendar; they are overlapping experiments and are
never summed into a jointly executable portfolio.

All indicators use provider midpoint features on completed M1 candles. Execution
uses bid/ask; midpoint is never a substitute fill. At most one order attempt is
made per pair/session, which is stricter than the user's one-entry maximum. A
rejected attempt is not retried at a more favorable later price.

| Rule | Signal and frozen bracket |
| --- | --- |
| ORB | Range is the first 15 session minutes' midpoint high/low. First subsequent eligible close strictly outside the range gives the direction. Stop is the opposite boundary. Target distance is 1.5 × actual fill-to-stop price distance. Zero/incomplete/ineligible range gives no signal. The stricter engine requires all range-to-signal evidence slots to be eligible. |
| Momentum | Current close versus close exactly 20 completed minutes earlier: +0.10% long, −0.10% short. The 21-candle contiguous grid includes both endpoints. Stop distance is 0.15% of actual entry, target distance 0.225%. |
| Mean reversion | SMA of the previous 20 closes, excluding the signal candle. A close at least 0.15% below/above it gives long/short. Stop distance is 0.15% of actual entry. Target is the immutable signal-time SMA, and must remain on the profitable side of the eventual fill after declared costs. |
| Directional baseline | At the first eligible completed candle after the 15-minute range clock, choose long for close > open, short for close < open. A doji ends the opportunity. Stop/target distances are 0.15%/0.225% of actual entry. |

Maximum holding is 60 minutes for every rule, including the inherited ORB default.
Flattening remains the earlier of close minus five minutes and 16:45 New York;
the last signal permits the next-close entry and one full management minute.
Weekends, conservative holiday exclusions, rollover and disputed boundaries retain
the Phase 6B-2 restrictions. No future-session quality screen removes bad days.

## Entry-rule and cost compatibility

Phase 6B-2's original `Intent` contains absolute prices and units. To express the
user's exact fill-relative percentages without using a future fill at signal time,
this phase adds the explicit immutable
`DECLARATIVE_ENTRY_BRACKETS_AND_SIZING_V1` rule. The template records its kind and
signal-time frozen inputs. At the already scheduled next-close execution point,
the engine resolves the actual stop, target and units using only current prices,
current conversion, current capital and the fixed budget. An audit event records
the resolution. Template price fields are signal-time previews, not instructions
to pretend the fill occurred earlier. Legacy absolute intents retain their behavior.

This extension changes no next-close timing, OHLC path assumption, bid/ask direction,
missing-data behavior, rollover restriction or risk cap. An invalid resulting
bracket, minimum-size failure or unprofitable mean-reference target is rejected.

`FX_CLOSE_COSTS_V2` adds a declared spread multiplier. V1 still requires multiplier
one. The observed close spread is multiplied symmetrically around the bid/ask
endpoint center; stressed exit-side envelopes use that same quote stress for
threshold checks. Slippage is charged adversely per side. No intrabar simultaneous
spread or actual broker execution is claimed.

| Scenario | Spread multiplier | Slippage per side | Status |
| --- | ---: | ---: | --- |
| C0 | 1.0× | 0.5 pip | Evaluated |
| C1 | 1.5× | 0.5 pip | Evaluated |
| C2 | 1.0× | 1.5 pips | Evaluated |
| C3 | 2.0× | 2.0 pips | Evaluated |
| C4 | C0 prices plus another execution candle | C0 | BLOCKED: additional-candle timing is outside the approved contract |

Thus **144 cells are executable and 36 C4 cells are explicitly blocked**. C4 is
not simulated by shifting the signal time or looking at a later quote. Extending
timing would require a separately reviewed contract, not a silent experimental
reinterpretation.

All evaluated scenarios use the same declared conservative commission of
**GBP 0.00005 per base unit per side**, equivalent to GBP 5 per 100,000 units per
side. This is an assumed research charge, not a verified OANDA fee. The manifest's
cost-effective timestamp is a modeling convention before the data interval;
the experiment was actually frozen in 2026. It is not evidence those assumptions
or rules were formulated before the historical market events.

Modeled entry spread above five pips rejects the opportunity. Spread stress can
therefore change eligibility and trade counts as well as realized costs. No median
spread, zero-cost fallback or after-the-fact fee calibration is used.

## Capital, missing paths and metrics

Each pair/session/rule/cost cell begins with its own GBP 1,000 ledger and carries
its cash continuously across all three splits. There is no split/day capital
reset and no combined overlapping-cohort capital curve. Maximum risk is GBP 5
per trade, GBP 10 concurrent and GBP 10 daily equity loss, subject to stricter
available capital. A cell trades one pair with one position maximum. Both long
and short reserve full adverse notional; no short proceeds, margin benefit or
leverage are introduced. Integer/unit-step sizing is constrained by cash, marked
equity, risk and explicit costs. Minimum-size failures are visible rejections.

The first unresolved position invalidates that cell's capital path. Known partial
cash and paid fees remain in evidence; later sessions are blocked with
`BLOCKED_UNKNOWN_CAPITAL`. They are not restarted at GBP 1,000 and the unknown loss
is not booked as zero. Complete-case metrics contain only valid completed trades;
portfolio P/L and return are unavailable for an unknown path. Known unresolved
entry fees are separately retained in the independent audit. This conservative
choice can make a large part of a cell inconclusive, which is preferable to an
invented continuous portfolio. A realized capital failure, if encountered, is
recorded and rejects suitability; losses are never capped to preserve the account.

Metrics are reported by split and over each complete cell: counts, gross model
P/L, model costs, net, return on original GBP 1,000, win rate, average winner/loser,
expectancy, profit factor, recorded marked drawdown, loss streaks, durations,
best/worst trading days, top-five winner contribution and invalidations. Drawdown
uses observed close/recognition equity marks; unseen tick-level/intrabar drawdown
cannot be established. Unknown paths make reported complete-case drawdown only
a partial-path observation. No sparse-return Sharpe ratio is produced.

The gross/cost bridge is explicitly modeled: add back symmetric endpoint spread
and slippage at the same recognition timestamps, translate that reference using
midpoint GBP conversion, and take reference gross minus actual net as total modeled
cost. This supplies a reproducible accounting identity, **not observed intrabar
transaction costs or an executable cost-free counterfactual**. Price P/L before
commission and actual commission charges are also reported separately. Actual
ledger conversion retains the Phase 6B-2 income/obligation bid/ask sides and
eight-decimal conservative GBP settlement.

## Statistical interpretation and classifications

Uncertainty is exploratory: at least 30 trades and 20 calendar-week clusters are
required for a week-cluster ratio/sandwich standard error of expectancy and an
asymptotic normal 95% interval. Trades in a week are not assumed independent.
Cross-week dependence can remain; intervals are not corrected for the 180-cell
testing family and are not statistical proof of an edge.

PROMISING requires the user's ≥100 holdout trades, positive validation/holdout C0
expectancy, positive C1/C2 holdout expectancy, C0 holdout profit factor >1.10,
recorded drawdown ≤GBP 100, invalidation rate <1%, intact capital/integrity, and no
material concentration. Material exceptional-profit concentration is frozen at
50% of apparent net profit: top-five winners or positive P/L exposed to known
range-review candles must not reach that proportion. This is deliberately strict.

REJECTED requires negative C0 expectancy in both validation and holdout with at
least 30 valid trades in each and no unknown capital path, or a material predefined
risk failure. Other mixed/sparse/uncertain cases are INCONCLUSIVE. Unavailable
execution capabilities are BLOCKED. No strategy is promoted from development
results or selected as a winner from holdout scores. C3 severe-stress results and
C4's limitation remain visible even though the user's PROMISING thresholds name
C0/C1/C2 specifically.

## Bounded implementation and verification

Source buffering is one UTC date, at most 4,320 candles; all source reads remain
SQLite read-only. The runner validates manifest/page/quality proofs through the
existing adapter. Up to four worker processes evaluate independent cohorts; no
agent delegation, external data service or broker adapter is used. Worker hooks
reject credential-loader, provider/client or external connection construction.
Detailed derived records are compressed in local SQLite, separate from source.

For a one-intent cell, the research executor uses the accepted `_Engine` state
machine only during intervals where an order or position can exist. Eligible
prefix evidence is supplied causally, and processing stops after final resolution
when no further intent is possible. Whole-session quality and the full input hash
remain separate. Synthetic full-engine parity tests cover all pairs, directions,
rules and cost scenarios. The source fingerprint is checked during the run;
parameters or implementation cannot change halfway through the matrix.

The runner refuses output overwrite, requires matching synthetic/full-suite
prerequisites, and creates an exclusive experiment-start marker. A separate receipt
marks first holdout access. A material post-result implementation defect requires
a new experiment version with original evidence preserved and any holdout exposure
disclosed; it does not authorize a new parameter search or a fresh OOS claim on
already examined data.

The independent audit reconstructs all saved trade prices, conversions, capital
continuity and marked equity from original source candles, checks split/session
boundaries and invalidations, and reconciles published metrics. Exact repeated
research-result hashes and full-engine economic parity are checked on the fixed
development-only subset: October 28/29, 2024, all three pairs, London ORB, C0/C3.
The holdout gets output/provenance/arithmetic verification, not another strategy
evaluation or selection pass. This is a separate adversarial pass by the same
implementation agent, not external human peer certification.
