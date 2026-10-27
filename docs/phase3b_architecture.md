# Phase 3B historical backtesting architecture

Phase 3B is an offline research consumer of immutable, accepted Phase 3A datasets.
It does not download data, persist fills into the dashboard history, send Telegram
messages or import broker code.

## Data and availability boundary

`HistoricalStore.inspect` first revalidates the manifest, source-page chain, quality
report, normalized rows and checksums. QUARANTINED data is rejected. The store is
opened read-only and inspected again after replay to detect unexpected mutation.

Two modes are deliberately separate:

- `VERIFIED` selects only bars whose source availability is `KNOWN`. UNKNOWN and
  previously assumed bars remain invisible.
- `ASSUMED` permits UNKNOWN bars at `bar end + configured delay` (60–3,600 seconds).
  This projection exists only in memory. Results say `ASSUMED/INCOMPLETE POINT-IN-TIME
  EVIDENCE`; they are never represented as point-in-time verified.

Atlas does not substitute ingestion time for publication time. A run is marked
point-in-time verified only when every selected bar is KNOWN, all source rows were
selected, and the explicit FX input is also marked point-in-time verified.

## Replay path

```text
accepted immutable 1-minute rows
        |
        +-- availability projection (VERIFIED or ASSUMED)
        |
        +-- exact five-minute ORB bars (incomplete groups are omitted, never filled)
        |
        +-- next-minute-open execution observations
        |      spread applied to bid/ask
        |      capacity <= configured % of reported source volume
        |
        +-- ORB v1 -> existing RiskEngine -> existing PortfolioLedger
        |
        +-- deterministic report, fills, risk decisions and audit trail
```

An execution observation whose market event is not strictly after the signal decision
is ignored. The first fill attempt therefore cannot use the breakout bar or an earlier
bar. Same-bar OHLC stop/target ambiguity continues to use the conservative existing
replay policy. Missing source minutes remain missing and normally prevent ORB evidence
from passing the five-minute continuity checks.

Reported volume is not order-book liquidity. The participation ceiling is a declared
model constraint and zero volume means zero fill capacity. Spread is incorporated in
the modeled executable bid/ask and is not double-counted by the cost engine. Slippage,
commission, FX conversion, taxes and other costs are explicit Decimal configuration;
unknown values are not silently replaced with zero.

## Currency, risk and accounting

Initial support is USD-quoted XNYS/XNAS equities in a GBP account. Instrument tick and
quantity rules are explicit configuration, not inferred from ticker. A required
USD-to-GBP `FxPolicy` includes rate, event time, availability time, source and a
point-in-time verification flag. Stale or future FX causes the existing risk engine to
reject entry.

Starting capital remains configurable through `ResearchSettings` and is GBP 1,000 in
the example. Existing 0.5% planned trade risk, 1% daily loss threshold, 1% concurrent
risk, long-only, no leverage and no broker execution constraints remain authoritative.

## Reproducibility and evaluation

The run manifest fingerprints the accepted dataset ID and manifest, complete replay
configuration, calendar content, cost/risk/ORB settings and common replay fill model.
Identical input produces the same run ID and report.

Every run is labelled `IN_SAMPLE` or `OUT_OF_SAMPLE`. OUT_OF_SAMPLE requires an
explicit parameter-selection cutoff strictly before the replay window. Phase 3B v1
does not tune parameters; the cutoff contract prevents a future optimizer from
silently using evaluation observations.

## Limitations

- One recorded US trading session per run; portfolio continuity across sessions and
  walk-forward orchestration are deferred.
- The AAPL pilot has UNKNOWN publication availability, so only ASSUMED exploratory
  replay can expose it today.
- A fixed explicit FX input is supported; a sourced point-in-time FX series is not.
- Minute OHLCV does not reveal intraminute sequence, queue position, hidden liquidity,
  halts or exact executable quotes.
- Corporate actions, revisions, delistings and survivorship remain bounded by the
  Phase 3A dataset evidence.
- No Phase 3B result establishes profitability or authorizes paper/live trading.
