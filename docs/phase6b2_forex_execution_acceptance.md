# Phase 6B-2 implementation and adversarial acceptance

Date: 29 October 2026. **Decision: PASS for implementation and synthetic acceptance
under `FOREX_M1_CONSERVATIVE_REPLAY_V1`.** This is not acceptance of real broker
execution fidelity or authorization to run a historical strategy.

## Starting evidence

Starting HEAD and remote `origin/main` both matched
`903f572334ee9f4f6cb26d4c6102df8c6ba0d959`; branch `main`, clean working tree.
No reset, checkout replacement or concurrent-work overwrite was performed. The
remote was inspected read-only before implementation and will be rechecked before
the authorized push.

The Phase 6B-1C report, detailed JSON checksum, collection/storage models, calendars,
Forex accounting/risk foundation, stock replay and paper execution boundaries,
test conventions and project dependencies were inspected. There was no contradictory
existing requirement requiring a stop. Missing specifications were resolved
conservatively and frozen in the [execution contract](phase6b2_forex_execution_contract.md):
next-close execution, zero assumed publication delay, deterministic cancellation,
fixed holiday exclusions, signed current conversion, explicit concurrent funding
rules and GBP settlement precision.

Before implementation, all **5,216 readable protected artifacts matched the prior
audit's preservation baseline**. The same two older Massive JSON reports remained
unreadable and are explicitly outside content-hash verification. The Phase 6B-1C
details checksum passed; its historical data and risk limitations remain applicable.

Baseline validation: **1,418 tests passed**, 653 existing Starlette/pandas
deprecation warnings, 255.42 seconds. No existing test was removed or skipped to
make the new implementation pass.

## Implementation and compatibility

Six new package files provide contracts, sessions, data/cost/conversion gates,
the state machine, opening-range measurement and package initialization. Two new
test modules implement ordinary execution validation and the separate adversarial
pass. No existing Python source file, dataset reader, quality classifier, stock
research component, dashboard, broker or notification path was modified. No
dependency was added.

The existing single-position `ForexPortfolio` remains backward compatible. The
new engine has a separate immutable multi-position lifecycle because the old
foundation does not implement this contract's unresolved paths, timing, current
conversion sides or concurrent marked-exposure controls. Shared Forex models,
pip metadata, UTC validators, calendar helpers, quality evaluators and canonical
hashing remain reused.

The core replay has no I/O except reading its own implementation fingerprint and
installed tzdata version. Its partition adapter is explicit and bounded, not an
automatic historical data loader. No new CLI or scheduled worker can launch a
strategy run or external action.

## Adversarial review findings and resolutions

This was a distinct code-review and attack-test pass by the same implementation
agent, not a claim of independent human or external peer certification. The
adversarial tests live separately so their assumptions and attacks are reviewable.

| Attack/finding | Initial issue or tested risk | Resolution/evidence |
| --- | --- | --- |
| Stress quote reaches stop | Applying widening only at fill could miss a stop or grant an unreachable target | Shift the exit-side OHLC envelope before threshold checks; apply contingent quote stress once and slippage once; both directions tested |
| Spend unrealized losses | Cash-only new-entry capacity could ignore marked losses | Use conservative liquidation equity plus both original reservations/current exposure; regression attempts a second position funded by an unrealized loss |
| Stop already hit at entry | A stop inside the bid/ask spread could understate risk and survive until a favorable recovery | Reject entry when the stressed liquidation quote has already breached the stop; long/short regressions |
| Recurring FX fractions | Exact cash/trade equality failed under different finite-precision addition orders; reproduced with two positive USD conversions | Eight-decimal conservative GBP settlement and fee rounding; exact multi-position reconciliation regression |
| Future candle suffix | Later prices might change earlier entry decisions | Prefix event equality under adversarial suffix changes; entry is next close, not the future candle open |
| Missing exit followed by target recovery | A later favorable price could erase unknown-path exposure | Sticky invalidation, outstanding risk retained, no booked recovery; only a labeled stress observation |
| Stop/target collision | Profit-first handling could improve outcomes | STOP_FIRST ambiguity flag or explicit INVALIDATE policy; target-first config rejected |
| Missing final flatten price | A simulator could invent a closing price or carry overnight | Invalid unresolved result with no P/L; three session deadlines and a second-day frame rejection tested |
| Stale/future conversion | Reused or later FX could value an earlier decision | Same-frame complete conversion required; both stale and future frames rejected |
| Price-driven exposure/daily loss | Existing holdings could breach limits before another entry | Mark before entries; adverse price exit on exposure or daily-equity breach, without favorable spread deferral |
| Nondeterministic environment/order | Decimal defaults or source order could change fills/hashes | Explicit arithmetic context, sorted frame/intent/annotation order, context perturbation and permutation tests |
| Source mutation/external side effects | Replay or adapters could alter accepted data or construct clients | Frozen inputs, synthetic source-preservation/proof tests, before/after protected file hashes and forbidden credential/client/socket hooks |

All material execution-integrity findings identified in this pass were corrected.
Remaining limitations are explicit contract boundaries, particularly unknown real
publication/quote timing, contingent OHLC exits, static research instrument limits,
unverified outlier authenticity and unsupported overnight/product financing rules.

## Validation and evidence

Final full suite: **1,512 passed, zero failed, zero errors, zero skipped**, with
653 existing deprecation warnings, in **241.81 seconds**. Ruff lint passed;
formatting passed for all 136 Python files; `pip check` found no broken requirements.

The new targeted suite has **94 passing cases**, including parameterized attacks.
It covers all three pairs in both directions; commissions, spread/floor stress,
slippage, gap-through stops, targets and collisions; active and entry gaps;
incomplete/crossed prices; holidays, weekends, US/UK DST and overlap mismatch weeks;
deadlines, maximum holding, current conversions, concurrent position/risk/exposure
limits, deterministic hashes and proof-checked read-only adapters.

Six additional full-session **artificial fixed-intent** scenarios (each pair and
direction) were repeated identically. All six repeated results matched exactly,
including events, costs, position states, trades and SHA-256 result seals. These
are fixed synthetic acceptance scenarios, not historical strategy backtests or
performance evidence. Forbidden credential-loader, provider/client and network
hooks registered zero attempts.

Local-only machine evidence is under `local/verification/phase6b2/`:

- `preservation-before.json`: full protected-artifact baseline, including explicit
  unreadable entries, compared with Phase 6B-1C.
- `pytest-final.xml`: final full-suite JUnit results, including failures/skips counts.
- `synthetic-acceptance.json`: baseline/targeted validation, six repeatability
  checks, implementation hash and zero side-effect attempts.
- `EUR_USD-long.json`, `EUR_USD-short.json`, and corresponding GBP/USD and USD/JPY
  files: complete artificial inputs' configuration, economic decisions and result seals.
- `preservation-after.json` and `acceptance.json`: final preservation comparison,
  frozen-source checks, exact full-suite totals and tool results.

Final preservation verified **5,216 readable files unchanged** out of 5,218
inventoried artifacts. Both frozen Forex database SHA-256 values also match the
Phase 6B-1C detailed audit. This verifies the 2,193-partition / 4,386-page /
2,227,822-candle corpus is unchanged from that fully validated checkpoint; no
historical strategy replay was needed. The two previously unreadable Massive
reports remain explicitly unverified rather than silently counted as preserved
by content hash. No stock or Forex research artifact was replaced or deleted.

Committed evidence consists of the reproducible implementation, tests and these
two documents. Local reports, raw datasets, databases, approval records, credentials
and `.env` are excluded from Git.

Reproduction from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_forex_execution.py tests/test_forex_execution_adversarial.py -q
.venv/Scripts/python.exe -m pytest -q --junitxml=local/verification/phase6b2/pytest-final.xml
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m ruff format --check src tests scripts
.venv/Scripts/python.exe -m pip check
```

## Phase 6B-3 recommendation

Separately authorized **intraday** historical strategy research can be considered
after reviewing the contract and acceptance evidence. Before such a run, freeze
strategy/session configuration, truthful source/code identities, cost provenance,
the zero-delay assumption, exclusion/outlier treatment, complete opening ranges,
and reporting of rejected and unresolved paths. The future harness must use the
bounded verified source adapter and retain all diagnostic/provenance evidence.

There is no unresolved implementation blocker within V1's declared synthetic
execution scope. Real publication latency and actual broker fills remain unverified;
V1 cannot support claims requiring that fidelity. Unknown-data paths remain invalid,
not silently tradable. **Overnight research remains blocked. Phase 6B-3 was not begun.**

No authenticated broker requests, real or paper trading, history downloads,
redownloads, account modifications or Telegram notifications occurred.
