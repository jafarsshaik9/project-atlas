# Phase 4B acceptance report

Date: 2026-10-28. Branch: `main`. Scope: entirely offline artificial paper trading.

## 1. Phase 4A checkpoint and SSH trust

Phase 4A was committed as **`e9224d8001fb66521f16c50276234ba168fd6492`**:

`feat(paper): add market data gates and session scheduler`

The 13 explicit reviewed paths were staged, checked for secrets and committed.
The prior 1,038-test validation remained applicable to the unchanged source tree;
Ruff lint/formatting, pip check and diff whitespace verification passed. No environment
file, runtime database or credential file was included.

The SSH failure was missing host trust, not a conflicting known key. The configured
user/system known-host files were absent. Windows' key scanner also hit a key-exchange
compatibility failure; Git's bundled OpenSSH scanner succeeded. Its presented
Ed25519 public key was independently SHA-256 hashed and compared against
[GitHub's official fingerprint documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints):

`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`

Only the matching public key was added to the previously absent
`C:/Users/Dell/.ssh/known_hosts`. No existing trust was removed or replaced. Strict
host checking authenticated as `jafarsshaik9`; private repository access succeeded.
The normal push to the existing `origin/main` succeeded, and an independent
`ls-remote` returned the same full SHA as local HEAD. No force push or insecure
SSH override was used. No private-key or token contents were inspected or printed.

## 2. Implementation verdict

**PASS for the bounded offline mock paper-execution scope**, subject to the operating
limits below. A separate virtual GBP 1,000 account integrates the existing five-minute
ORB, independent risk authorization, conservative accounting, durable intents,
later-observation fills, costs/FX/liquidity, kill control and authenticated read-only
dashboard. Phase 4A's read-only package and all existing tests remain unchanged.

The scheduler audit acts as a durable upstream outbox. A separate trading inbox
acknowledges each ordinal only in the transaction containing its strategy transition,
intent state, fills, public-operation journal and reconciled account projection.
Stable signal/order/fill IDs and SQL unique constraints independently prevent duplicate
side effects. See `phase4b_paper_execution_architecture.md` for exact boundaries.

## 3. Files changed

Modified:

- `src/atlas/main.py`: isolated `paper-session` route and dashboard `--paper-db` argument.
- `src/atlas/risk.py`: independent held-long exit gate; existing entry rules unchanged.
- `src/atlas/dashboard_cli.py`: pass explicit paper database into dashboard construction.
- `src/atlas/web/app.py`: authenticated read-only paper account API and page.
- `src/atlas/web/templates/dashboard.html`: link to the paper account view.

Added:

- `src/atlas/paper_execution/__init__.py`
- `src/atlas/paper_execution/codec.py`
- `src/atlas/paper_execution/ledger.py`
- `src/atlas/paper_execution/engine.py`
- `src/atlas/paper_execution/mock.py`
- `src/atlas/paper_execution/cli.py`
- `src/atlas/web/templates/paper.html`
- `src/atlas/web/static/paper.js`
- `tests/test_paper_execution.py`
- `tests/test_paper_dashboard.py`
- `docs/phase4b_paper_execution_architecture.md`
- `docs/phase4b_acceptance_report.md`

Seventeen Phase 4B files in total. Runtime smoke databases and
`local/phase4b-acceptance/smoke_summary.json` are ignored artifacts, outside staging.
No dependencies, environment files, broker/provider adapters or notification code changed.

## 4. Validation

| Check | Result |
| --- | --- |
| New execution/dashboard tests | **64 passed** |
| Final full pytest suite | **1,102 passed**, 253 dependency warnings; 124.41 seconds |
| Ruff lint | All checks passed |
| Ruff formatting | 113 Python files formatted |
| pip check | No broken requirements found |
| Offline guarded acceptance smoke commands | **6 passed** |
| Git diff whitespace check | Passed; local LF/CRLF conversion notices only |

The existing dependency deprecation warnings remain visible (Starlette/httpx and
pandas timezone array conversion). No tests were removed, skipped or weakened.
The UI was checked through authenticated HTTP tests; no browser visual review is claimed.

## 5. Completed artificial session

The finite original script covers a five-minute opening range, a subsequent completed
five-minute breakout bar, partial entry liquidity, target exit and later exit liquidity.
All timestamps below are UTC on 2026-10-28. No external quotes were used.

| Event | Evidence |
| --- | --- |
| Opening range | 13:30–13:35; sampled high USD 100.10, low USD 99.90 |
| Strategy signal | 13:40:00; entry hypothesis USD 100.50, stop 100.00, target 101.50 |
| Independent entry approval | 13:40:01; 3 shares; planned risk GBP 1.398, stressed risk GBP 6.37296 |
| Simulated buys | 13:40:01, :02 and :03; 1 share each at USD 100.52 |
| Exit trigger | 13:41:00; artificial midpoint USD 101.60 exceeds target |
| Independent exit approval / simulated sell | 13:41:01; 3 shares at USD 101.58 |

The signal observation produced a durable intent and **no fill**. Each buy consumed
at most one available share (10% of ten incremental shares). The sell used its own
later observation. Both approvals are saved; no user-supplied approval was accepted.

| Cost/accounting item | GBP |
| --- | ---: |
| Midpoint-to-midpoint gross P/L | 2.640 |
| Spread embedded in buy/sell prices | 0.096 |
| Gross P/L at simulated execution prices | 2.544 |
| Slippage cash costs | 0.060 |
| FX conversion costs | 0.060 |
| Commission, taxes and other artificial costs | Explicit 0 |
| Net realised P/L | **2.424** |
| Final cash and equity | **1,002.424** |
| Final positions / reservations | None |

FX was explicitly artificial GBP 0.8 per USD on each observation. Fees are penny-rounded
per component; principal and P/L retain Decimal precision. Spread is not charged twice.
The first partial fill is bid-marked at GBP 80.384, giving account equity GBP 999.948
after its costs, rather than claiming the entry price is a realizable exit mark.

The acceptance harness ran the completed scenario, resumed it, read state, ran the
no-opportunity scenario, resumed it, and activated a persistent kill switch. All six
CLI calls returned success under socket and credential-settings guards. Restart
retained **exactly four fills with identical IDs and cash**, without duplicate fills.
The kill check ran after the completed trade and left its economics unchanged.

## 6. Honest no-opportunity session

The separate flat-price script produced **zero signals, zero orders, zero fills**,
GBP 1,000 cash/equity and zero realised P/L. Its restart also produced no trades.
No breakout, volume, exit or opportunity was fabricated to force a demonstration.

## 7. Failure and duplicate-prevention evidence

The new tests cover:

- Duplicate observations, conflicting identity content, repeated signal evaluation,
  repeated order creation, database duplicate IDs and two concurrent trading calls.
- Crash injection before/after commit at signal/intent creation, first buy and sell.
  Pre-commit writes roll back together; post-commit retry reads the trading ack.
- Actual child processes exiting with code 73 before and after a first-fill commit.
  After lease expiry, fenced takeover recovered exactly one fill, retained the
  committed signal/intent and passed SQLite integrity checks. A further retry made
  no duplicate. The predecessor's token could not mutate the account.
- A SQL trigger aborting inbox insertion after risk/reservation/fill work. Every
  trading effect rolled back; retry produced exactly one fill.
- Reopening between durable intent creation and the next actionable price; no
  signal-observation fill, preserved strategy samples and subsequent independent approval.
- Fresh-evidence recovery of a safety-cancelled disposal under its existing stable
  ID, preserving its remainder and waiting for another observation before selling.
- Explicit latency, zero liquidity, partial buys/sells, insufficient cash under the
  existing risk engine, independent entry/exit rejection, adverse-price/FX ceilings,
  partial-fill fee-budget exhaustion and preserved conservative remaining-position risk.
- Stale observations, stale no-new-data ticks, closed markets, actual mock halt events,
  disconnection, missing/future/stale/nonmock FX, unknown/inconsistent costs and
  invalid liquidity conventions. These do not create fills.
- A persistent kill switch cancelling pending risk across engine reopening while
  retaining already-held positions; it cannot silently re-arm.
- Corruption of all six execution tables and upstream acknowledged source evidence,
  plus injected accounting reconciliation failure. Further acknowledgement/fills stop.
- Independent repeated sessions yielding identical reports and fill IDs; completed
  session restart preserving economics; missing minute samples preventing strategy signals.
- Fresh-process import guards against broker, notification and real-provider modules,
  guarded sockets, credential-environment access and `.env` reads.

Each transition verifies the prior journal and exact snapshot/fill/order projections
before processing, then reconciles the resulting account identity and snapshot before
committing its writes and integrity seal together. Subsequent recovery/read checks
independently replay the newly committed operations and compare their projections.
The finished account satisfies `cash + basis = 1000 + realised P/L`: both sides are
GBP **1,002.424**, with zero remaining basis and zero unconfirmed proceeds spent.

## 8. Dashboard integration

`/paper` and GET `/api/paper` use existing dashboard authentication and security headers.
Supply the intended account explicitly with `dashboard --paper-db <path>`. The view
shows status, kill switch, saved valuation time, cash/equity/realised P/L/fees,
positions and recent simulated trades. Tests confirm authentication, completed and
empty accounts, missing-file handling, no account database writes and rejection of
POST order/control requests. The dashboard cannot run the engine or modify orders.

## 9. Limits and safety boundaries

This is one artificial instrument, fixed original session date and one local account
per database. Sampled mock bars are not full-tape OHLC data. Costs, liquidity and FX
are explicit synthetic assumptions, not provider or broker claims. There is no
deployment daemon, market-depth model, settlement, dividends/corporate actions,
multi-account netting, retention/compaction or schema migration.

Full replay/seal validation grows with history and is intended for bounded offline
sessions. Existing calendar bounds/Nasdaq alias limitations remain. Hardware power-loss
and distributed/network-filesystem behavior are not certified. Hash seals are corruption
checks, not a defense against an attacker rewriting all database content.

Session closure, staleness, halt or kill can leave a position held at its last saved
mark; no closing fill is invented. The kill switch freezes exits too and has no
automatic reset. Stops/risk budgets are not guaranteed loss caps. Reports are saved
valuations, not live-freshness claims or evidence of profitability.

No Massive calls, Trading 212 calls/orders, Telegram messages, live-money trading,
provider connectivity, subscription changes or entitlement claims occurred. The only
network actions were the separately authorized GitHub trust/repository operations and
reading official GitHub host-key documentation. No credential contents were accessed.

## 10. Git state

Phase 4A is pushed. HEAD remains `e9224d8001fb66521f16c50276234ba168fd6492`.
Phase 4B consists of five modified tracked files and twelve new files, all unstaged.
**No Phase 4B commit or push was performed.** Runtime artifacts remain ignored.
