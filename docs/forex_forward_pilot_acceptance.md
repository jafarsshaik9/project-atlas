# Minimal forward-pilot technical acceptance

**Final decision: BLOCKED — DATA SOURCE.** The offline synthetic preparation is
accepted. Real-data ingestion and market-performance evaluation remain blocked;
no source/provider request has been authorized. No profitability claim is made.

## Scope and checkpoint

Started on `main` at `bdd109e52b7e99d8d9c0580692b66c34a83b354a`. Existing
untracked Phase 6B-6 work is retained unchanged and excluded from the pilot commit.
Only one new module, one focused test file and these three documents are added.
The original strategy manifest and its SHA-256 remain exactly
`6f84852343fbeb0971bf94ebb7a2c670c3ae60c7ce04212d8830503b2356a4a9`.

No critical trading-rule ambiguity required interpretation or modification.
Methodological limitations remain: assumed zero publication delay, indicative
close spreads, arbitrary research commission, full-notional funding/exposure
effects, unverified product/data rights and finite OHLC information. These
prevent broker-realistic conclusions; passing a hash or a fixture cannot close
them. The original 26-week period and acceptance criteria are unchanged.

## Separate critical review

This was a distinct review pass by the implementation agent, not external human
certification. No subagent or external broker was used.

| Challenge | Finding and disposition |
| --- | --- |
| Hidden network/order route | Runtime module imports no provider/config loader, HTTP/socket client, broker adapter or notification sender. Real-data origin is unconditionally blocked before storage. Tests trap socket/HTTP/provider construction; attempted prohibited calls fail locally. |
| Missing observations skipped by a fast replay helper | Adapter explicitly creates empty clock slots across the session before invoking the inherited helper. They contain no candles/prices. Missing first-hour/entry/management tests reject or invalidate as appropriate. |
| Look-ahead | Signal consumes exactly 60 sequential completed slots. A sentinel raises if it reads a 61st. Later prices do not select earlier entries. Actual acquisition times are retained; zero-delay execution remains a disclosed model assumption. |
| Costs/GBP accounting | Inherited BA direction, adverse slips, per-side fees, directional conversion and price/P&L reconciliation reused. Independent test identities reconcile net to gross less fees and model bridge costs. No median-spread or midpoint execution fallback. |
| Funding and resets | Existing sizing enforces cash/full notional, GBP5/10 limits and one position. Excessive minimum size rejects; ledgers continue independently without deposits or raised risk caps. Unknown position paths remain blocked on later days. |
| Duplicate opportunity/concurrent writes | Duplicate or reordered dates fail; exclusive writer lock and exclusive record creation prevent concurrent admissions. Stale locks/partial records require review, never silent overwrite. |
| Protected path/symlink | Output must resolve beneath the separate synthetic root; a resolved target outside its lexical root is rejected. No historical store is opened. |
| Evidence drift | Strategy hash checked at each run; source/engine/metadata fingerprints bind the book. Receipt chain and actual file checksum retained. Coordinated full-chain rewriting/tail deletion needs an independently retained head receipt to detect. No claim of cryptographic provider authentication. |
| Late start | Explicit unrecorded calendar dates precede each late/resumed book entry. Fixed evaluation dates never shift. |
| Synthetic results mistaken for market evidence | Dedicated status report hard-labels every market-performance measure NOT EVALUATED, independent of synthetic book results. |

The first fixture run had five failures: two calendar-only fixtures predated cost
registration; two demanded that a displacement below the fixed C2 cost threshold
still trade; one gap fixture exited at the exposure cap before its missing minute.
Fixtures were corrected, not strategy rules. Small artificial position ceilings
allow management-gap tests to reach the gap instead of the inherited exposure
exit. The initial failed XML is retained. Review also tightened empty-slot handling,
output-path isolation, fingerprint continuity and visible late-start evidence.

## Validation

- Baseline relevant execution/research suites: **246 passed**.
- Final relevant suites including **24 focused new cases: 270 passed**, no
  failures, errors or skips. These are synthetic/unit checks, not a historical run.
- Ruff lint and formatting pass for existing tracked Python plus the two pilot
  files. The unfinished untracked Phase 6B-6 file is excluded and unchanged.
- `pip check`: no broken requirements; no dependencies added.
- Protected verification: **5,287 readable artifacts unchanged**, including
  original stock/Forex source data and prior verification results. The two old
  unreadable Massive reports retain their existing unverified status.

No existing test was relaxed or removed; a broad full-system rerun was unnecessary
for this isolated adapter. No historical strategy, result, broker implementation,
stock component, dashboard, Telegram behavior or preregistered parameter changed.
Relevant-suite XML, earlier failed-fixture XML, preservation inventories, status
JSON and technical receipts are local-only under
`local/verification/forex-forward-pilot/`. Git contains source, tests and concise
documentation only, not fixtures presented as real forward performance.

## Outstanding action

Review [provider authorization](forex_forward_data_authorization.md): a permitted
source must supply EUR/USD MBA and contemporaneous GBP/USD conversion, actual
receipt times, rights, rate limits and known maximum cost. No suitable anonymous
source or complete paid/authenticated scope is established. The proposed licensed
file handoff avoids Atlas credential/network access but still needs explicit
provenance/rights approval and a narrowly reviewed real-ingestion change.

The exact next action is that authorization decision. No external collection,
further research phase or broker operation starts automatically. All real-market
metrics remain **NOT EVALUATED**.

No broker requests, market-history downloads, account/credential access, trades,
account changes or Telegram notifications occurred. No network call was made to
research or operate market data; Git backup, if completed, is the explicitly
permitted private-repository operation only.
