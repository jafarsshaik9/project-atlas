# Minimal Forex forward pilot readiness

**Decision: BLOCKED — DATA SOURCE.** Offline synthetic preparation is accepted;
no provider, endpoint, entitlement or expenditure has been authorized. No real
forward observation or hypothetical market trade has been evaluated. This is not
a new infrastructure phase or a profitability conclusion.

## Frozen contract review

Starting `main`: `bdd109e52b7e99d8d9c0580692b66c34a83b354a`. The unfinished,
untracked `src/atlas/forex/contract_readiness.py` is preserved byte-for-byte.
The [original manifest](../examples/forex-lean-profitability-v1.json) remains:

`6f84852343fbeb0971bf94ebb7a2c670c3ae60c7ce04212d8830503b2356a4a9`

Independent rule inspection found no critical unresolved trading-rule ambiguity:

| Requirement | Frozen definition / implementation |
| --- | --- |
| Instrument/time | EUR/USD; London, IANA `Europe/London`, existing DST-aware calendar |
| First hour | Sixty contiguous eligible M1 candles starting 08:00–08:59 local; decision at their 09:00 completed cutoff |
| Direction | Sign of last midpoint close minus first midpoint open, minimum absolute return 0.15% |
| Cost filter | Absolute displacement >=5 times signal spread plus both assumed slips and commissions translated with signal GBP/USD ask; repeat independently for C0/C1/C2 |
| Timing | One daily opportunity; existing one-second eligibility, next-minute close, 09:01 local; no same-candle or high/low entry |
| Bracket | 0.15% stop / 0.30% target from actual assumed fill; stop-first ambiguity; maximum 120 minutes |
| Intraday | Existing session flatten/rollover/holiday restrictions; no overnight carry |
| Funding | GBP1,000; GBP5 planned risk; GBP10 aggregate/daily loss; one position; full adverse-notional funding and existing marked-exposure cap |
| Costs | Observed indicative endpoints only; C0 0.5 pip/side and GBP0.00005/unit/side; C1 1.5x spread; C2 1.5-pip slip/side; current directional GBP conversion |
| Gaps | No filled prices; missing signal/entry observations reject; unresolved positions stop the affected capital ledger |
| Evaluation | Fixed 2026-11-02 to 2027-05-03 UTC exclusive, 100 C0 trades, 26 weeks, >=20 active weeks for uncertainty; frozen block-bootstrap, stress and acceptance rules remain authoritative |

These specify a **modeled** experiment. Actual publication latency is unknown;
zero assumed delay is not verified by retaining acquisition times. Real late
receipts cannot be presented as earlier actionable quotes. Broker-specific costs,
minimum sizes and data rights remain unresolved. Such evidence may require a
new preregistration before evaluation, never an in-place parameter change.
No performance ranking or new threshold calibration occurred.

## Minimal implementation and reuse

`atlas.forex.forward_pilot` adds only a frozen-hash loader, first-hour signal
adapter, synthetic day-book and hash-linked local evidence records. It reuses:

- `FxCandle`, `FxInstrument`, `Frame`: exact Decimal schema, timestamp/OHLC checks.
- Execution calendar and market gates: DST, completeness, bid/ask and sessions.
- `Intent` / `EntryRule`: existing entry-time brackets and constrained sizing.
- `research.execution.evaluate_intent`: existing fill, risk, accounting and cost
  bridge, independently exercised against full replay by existing regression tests.

Historical readers/stores, providers, old strategy names/results, dashboard and
notification code are unchanged. The adapter opens no historical database or
provider. EUR/USD needs contemporaneous GBP/USD quotes for conversion. The engine
requires metadata for three supported pairs; it does **not** require USD/JPY
prices to trade EUR/USD, and this is not authorization to collect a third pair.

`Batch` holds a source identifier, day, M1 frames, instrument metadata, explicit
origin and each frame's acquisition timestamp. Schema validation rejects duplicate
or unordered market timestamps and early acquisition. Market eligibility rejects
missing/incomplete bid or ask rather than substituting midpoint execution.
`prepare_day` **unconditionally rejects `FORWARD_OBSERVATIONS`**; there is no data
access or live-routing enablement flag. Only synthetic preparation can run now.

For an explicitly synthetic caller:

```python
from pathlib import Path
from atlas.forex.forward_pilot import Batch, prepare_day, status_report

batch = Batch.model_validate(synthetic_fixture_dictionary)
receipt = prepare_day(batch, Path("local/forex-forward-pilot/synthetic/demo"))
market_status = status_report()  # Every market-performance metric: NOT EVALUATED
```

The function validates structure before replay. Signal selection consumes exactly
the first 60 slots, without reading later prices or quality to choose an entry.
Missing minutes become empty clock slots, not synthetic prices. Their risk effect
is applied only when due. Dates outside the fixed period fail; days cannot be
repeated, reordered or restarted. Three independent cost ledgers persist cash,
fixed risk/exposure ceilings and equity peaks; they are not a combined portfolio.

Day records preserve source candles, market/acquisition times, completeness,
signal/rejection reasons and the existing execution event/trade fields: signal,
eligibility, entry/exit timestamps, side, units, fill/bracket, GBP risk/reservation,
fees, cost assumptions, modeled gross/net and unknown paths. Hypothetical prices
are not broker-confirmed fills. The stored cost bridge is a reference attribution,
not measured intrabar spread/slippage. Paid fees on unknown paths remain recorded;
unknown P&L is never zero-filled. Profits remain cash but do not increase caps.

Evidence uses exclusive sequential JSON records, writer exclusion, fsync,
content/previous-record hashes, source/processing/execution fingerprints and a
returned actual file SHA-256. Changed code/metadata block continuation. Retain
receipts externally: deleting a terminal record or coordinated rewriting of a
whole chain cannot be independently detected without a trusted external anchor.
Interrupted writes/locks fail closed for review; no automatic recovery overwrites
records. All pilot outputs are separate and Git-ignored.

## Real forward status

| Measure | Status |
| --- | --- |
| Period | 2026-11-02 00:00 UTC to 2027-05-03 00:00 UTC exclusive |
| Eligible/missing days; signals/trades | NOT EVALUATED |
| Net, expectancy, profit factor, drawdown | NOT EVALUATED |
| Capital utilization; cost sensitivity | NOT EVALUATED |
| Data-quality warnings; strategy-rule violations | NOT EVALUATED |
| Sample sufficiency | No real observations; insufficient |

Synthetic fixture economics never populate this table. No small-sample or
profitability declaration is possible. If collection starts late, the missing
start and subsequent days remain visible; do not backfill history or move the
fixed evaluation boundaries.

Next action is a separate decision on the
[data authorization checklist](forex_forward_data_authorization.md), not collection.
