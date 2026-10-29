# Phase 6B-2 deterministic Forex intraday execution contract

Version: `FOREX_M1_CONSERVATIVE_REPLAY_V1`. Implementation-only research
infrastructure, 29 October 2026. This finalizes the executable portions of the
[Phase 6B-1C draft](phase6b1c_forex_data_quality_audit.md). All modeled fills are
**ASSUMED**, not verified broker fills. No strategy or historical performance run
is part of this phase.

## Components and boundaries

The new `atlas.forex.execution` package consumes immutable Forex contracts without
changing existing stock execution, `ForexPortfolio`, discovery, collection,
quality-v1, audit-v2, dataset readers, risk defaults, dashboard or Telegram behavior.

| Component | Responsibility |
| --- | --- |
| `contracts.py` | Frozen Pydantic inputs: costs, research limits, run scope, overlay annotations, observations, minute frames and order intents; fixed Decimal arithmetic context |
| `calendar.py` | London, New York and overlap windows; IANA DST conversion through existing `local_instant`; entry cutoff, flatten deadline and conservative holiday/rollover exclusions |
| `market.py` | Data eligibility, indicative spreads, quote stress, adverse prices, current-frame directional GBP conversion and bounded proof-checking partition adapter |
| `engine.py` | Single-session streaming state machine, deterministic order priority, risk checks, immutable positions/trades/events, blocked scenarios, accounting and reproducibility seals |
| `primitives.py` | Opening-range measurement on a complete visible midpoint grid; no breakout generation, traded-volume filter or strategy optimization |

The engine imports no broker, configuration loader, notification sender, wall clock
or database client. There is no new execution CLI, scheduler or historical strategy
runner. Callers supply already-validated inputs and explicit intents. All three
instrument metadata records must be supplied, including conversion instruments;
actual tradable intents can select any supported pair.

`market.frames_from_partitions` is the explicit integration point for bundles from
the existing read-only `dataset.partitions(plan)` interface. It checks manifest
identities, page hashes, quality, timestamps, bounds and duplicates again; buffers
at most three instrument partitions / 4,320 candles for one requested day; and
merges by UTC minute and instrument. It opens no database or provider. Callers must
select that day's partition bundles; the adapter rejects out-of-scope bundles and
repeated instruments, including empty partitions. It was exercised only with
artificial temporary stores in this phase.

## Run scope and time

A `Run` declares one research date/session, UTC coverage bounds, origin, caller
code version and calendar provenance. The start state is always flat and funded
with the explicitly configured GBP cash, default GBP 1,000. Resume from positions,
overnight carry and multiple-day replay are unsupported.

| Research session | Local nominal window |
| --- | --- |
| London | Weekdays 08:00–17:00 `Europe/London` |
| New York | Weekdays 08:00–17:00 `America/New_York` |
| Overlap | Date-specific intersection of those windows |

The existing `local_instant` rejects ambiguous and nonexistent local boundaries.
US and UK DST changes are independent; overlap is recomputed per date. All state
and event timestamps are UTC. No fixed UTC offset substitutes for IANA conversion.

The mandatory flat deadline is the earlier of session close minus the configured
buffer (default five minutes) and **16:45 New York**. The rollover exclusion is
16:45–17:30 New York. A final candle ending at the deadline may liquidate a position;
no new interval begins there. The last signal is two minutes before flattening,
allowing its next-close entry and at least one full management candle. Maximum
holding is an explicit integer 1–240 minutes, capped by the session deadline.

Weekend dates and New York December 24/25 and December 31/January 1 are excluded
conservatively in full. These annual research exclusions are not assertions of
historical market closure for every minute. Other supplied annotations remain
fail-closed. The underlying OANDA V1 calendar and V2 observed-boundary/holiday
evidence are preserved. Observations at disputed closing/reopening boundaries do
not grant trading permission.

Declared coverage must contain the whole permitted session. This is a scope
assertion, not permission to peek at future data quality. Future prices and future
gap annotations are never inspected to decide an earlier trade. Actual missing
observations are handled when their minute becomes due. Metadata outside the
frozen plan cannot extend historical source coverage.

## Information and execution timestamps

V1 explicitly assumes **zero publication delay**: the complete M1 candle becomes
available at its end. A nonzero `publication_delay_seconds` is rejected rather
than partly applying delayed availability to OHLC risk intervals. Actual source
publication times remain unknown. A later nonzero-delay model needs a new contract
and entry-interval treatment; V1 is unsuitable for latency-sensitive claims.

For an M1 candle starting at 08:00:

1. Its OHLC and indicative close spread become available at the assumed 08:01 cutoff.
2. An intent may have `signal_at = information_cutoff = 08:01`, backed by an
   explicitly declared complete evidence window ending then.
3. Eligibility is 08:01 plus the configured 1–60 second order latency.
4. The only scheduled market entry is the next complete candle close, **08:02**.
5. That entry uses 08:02 endpoint information; the candle's earlier open/high/low
   cannot supply an earlier fill or an exit for the newly created position.
6. Management begins with the following complete candle. Its decisions are recorded
   at its close, with the exit recognition timestamp separate from the entry time.

Signal time, information cutoff, order eligibility, entry execution and exit
recognition are retained in immutable results. V1 intents are minute aligned and
must reference every required evidence slot. Missing, incomplete, crossed, wide-
spread, annotated or extreme-range evidence rejects the signal. `opening_range`
enforces the same eligibility on a complete 1–60 minute grid, refuses future
information/cost settings, and returns feature bounds and source IDs only.

## Prices and costs

| Operation | Observed endpoint before adverse costs |
| --- | --- |
| Long entry | Ask close |
| Long market exit | Bid close |
| Short entry | Bid close |
| Short market exit | Ask close |

Bid and ask candle endpoints are indicative and may represent independent source
updates. They are not authenticated simultaneous executable quotes. `Costs`
requires a provenance identifier and a `known_at` time no later than session open,
plus explicit slippage pips, commission GBP per unit per side, spread floor and
additional spread stress. A zero charge is an explicit assumption. No spread is
estimated from a future distribution or replaced with a median.

Let `s = (ask.close - bid.close)/pip`. Additional quote stress on each side is
`max(0, spread_floor - s)/2 + additional_spread/2`, expressed in pips. The widened
quote is worsened again by configured slippage on a market fill. Default entry
screening rejects modeled spread `max(s, floor) + additional` above five pips.
High spreads never defer an existing-position exit. The maximum entry range is
explicit (default 100 midpoint pips); flagged entry/signal windows are excluded,
while active-position adverse risk handling remains in force.

Stop and target thresholds use the correspondingly stressed **exit-side** OHLC
envelope. Slippage is an execution cost, not an extra threshold shift. Once a
contingent target/stop threshold is used, quote stress is already incorporated;
only slippage is added to its assumed execution price. This avoids both an
optimistically missed stressed stop and double-counted spread stress.

Commissions, spread stress, slippage and conversion sides are separate assumptions.
No financing, leverage, margin benefit, account tier, lot convention, quote size,
partial liquidity, queue position or actual OANDA fill behavior is inferred.

## OHLC ambiguity and discontinuities

Only positions already open before the whole management candle can use its
envelope. Decisions are recognized/booked at candle close; exact intrabar exit
time is recorded as UNKNOWN. The contingent execution model is a versioned
research assumption, not a reconstructed tick path.

| Condition | Policy |
| --- | --- |
| Stop and target both touched | Default `STOP_FIRST_V1`: adverse stop, ambiguity flag retained. Explicit `INVALIDATE_V1` instead makes the path unresolved. Target-first is unsupported. |
| Long stop gap-through | Worse of stressed bid open and stop, then adverse slippage |
| Short stop gap-through | Worse of stressed ask open and stop, then adverse slippage |
| Target gap-through | Target price without favorable opening-price improvement, less/plus adverse slippage |
| Contiguous price discontinuity | Apply those same opening-gap rules; log price inputs through the frame chain and trade audit. A jump is not itself a missing candle. |
| Actual missing interval | Never use target recovery or a stop-price fill through the unknown interval; invalidate the affected position path |

The current candle's spread stress is an assumption applied when the completed
interval is evaluated, never evidence for an earlier recorded execution. No
independently observed bid high/ask low is combined into a simultaneous quote.

## Missing data and blocked scenarios

Frames are consumed strictly in chronological order. Omitted leading/intermediate/
trailing minutes are clock ticks with **zero observations**; no synthetic candle,
price, executable quote or conversion is created. Ineligible inputs keep their
source IDs, overlay version and machine-readable reason codes in audit events.
The full ordered overlay and its evidence references are sealed in configuration.

| Situation | Deterministic action |
| --- | --- |
| Missing signal-window slot | Reject that signal; a later fresh complete window may create a new intent |
| Missing scheduled entry | Cancel/reject that opportunity without deferring it |
| Missing bid/ask/mid, incomplete candle or crossed endpoint | Ineligible; no midpoint substitution |
| Closure/holiday/rollover/unknown annotation | No new risk; observed candles and original evidence remain untouched |
| Open position has missing/ineligible price or current conversion | Transfer its outstanding risk to `unresolved_positions`, invalidate the scenario and block all later entries |
| Prices resume after an unresolved interval | Record one adverse-close stress estimate with `FX_NOT_A_BOOKED_EXIT`; retain the unknown path and book no recovered target/profit |
| Flatten deadline has no defensible price/conversion | Invalid unresolved scenario, with no invented terminal liquidation |

An invalid scenario returns **`net_pnl_gbp = None`**, preserves partial booked cash
and all earlier trades, and retains the unresolved position. Partial cash must
not be presented as ending equity or complete performance. A rejected opportunity
remains a rejection; it is not silently removed from opportunity counts. A flat
run with rejected signals may have zero booked P/L, which is not proof of complete
market-data coverage. Future aggregation must report exclusions and unresolved
paths separately and cannot discard their losses or selection effects.

## Risk, currency and accounting

Defaults retain the foundation's GBP 1,000 cash, GBP 5 planned risk per trade,
GBP 10 concurrent risk and GBP 10 daily equity-loss threshold. Research settings
are explicit and frozen; no environment settings are read. Maximum concurrent
positions is configurable 1–3, with at most one position per pair. Units must
meet the supplied metadata minimum, maximum and unit step. Metadata is a declared
research restriction, not verified historical product rules.

Both long and short positions reserve full adverse ask notional in GBP. Shorts
create no spendable sale proceeds. Entries cannot exceed cash, conservative marked
equity, maximum exposure or risk budgets. Stops already breached across the
stressed liquidation spread at entry are rejected. Existing exposure is marked
before more entries; price-driven exposure breaches or daily equity-loss breaches
cause adverse-side exits when executable data exists. These thresholds are controls,
not guaranteed loss caps across jumps.

Conversion uses only complete eligible **same-frame** GBP_USD, plus USD_JPY for
JPY obligations. Stale/future rates are rejected; no previous rate is cached.
Positive USD proceeds buy GBP at GBP_USD ask; negative USD obligations sell GBP at
bid. JPY conversion uses the corresponding USD_JPY and GBP_USD ask/ask for income
and bid/bid for obligations. Gross quote P/L and the applied rate are retained.
All conversion occurs at the recorded close-recognition time, not an invented
earlier intrabar time.

Source prices retain their original Decimal precision. Derived GBP settlement
uses eight decimal places: converted P/L rounds toward negative infinity and fees
round upward. Cash and trade sums reconcile exactly in that unit. Arithmetic uses
an explicitly fixed 34-digit, half-even Decimal context independent of ambient
precision/rounding/traps. Starting cash has at most eight decimal places and is
bounded to GBP 1 billion as a numeric research guard.

## Determinism and reproducibility

At each minute the engine validates all instruments, handles existing exits,
records unresolved stress observations, enforces marked risk limits, handles
scheduled entries in order-ID order, then considers new signals. Instrument and
annotation order is canonical. Duplicate order IDs, duplicate observations,
unordered frames, unsupported instruments and out-of-session frames fail closed.

Results contain frozen trades, positions and events with canonical immutable JSON
detail. They seal settings, intents, metadata, overlay/evidence, origin, code
version, package source fingerprint, IANA tzdata package version, actual computed
session window, Decimal/settlement rules and the entire chronological input hash
chain. The result checksum covers all output fields except itself. Identical
inputs and recorded implementation/runtime rules produce identical hashes. No
randomness or wall-clock decision enters the model.

## Limits and later authorization

V1 offers reproducible conservative research assumptions, not tick execution,
verified publication timing or account fill fidelity. Nonzero publication delay,
overnight exposure, financing, leverage, partial fills, manual position recovery
and historical profitability/optimization are outside this contract. The supplied
source IDs and calendar/cost provenance must be truthful; hashes establish internal
consistency, not external authenticity.

Phase 6B-3 requires separate authorization after review of this contract, declared
cost assumptions, excluded windows/outliers, opportunity accounting and the frozen
dataset range. Overnight backtesting remains blocked. No historical research
runner is enabled by this implementation.
