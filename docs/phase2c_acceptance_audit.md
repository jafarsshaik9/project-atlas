# Phase 2C final acceptance audit

27 October 2026. **Latest ownership remediation: PASS. Current automated Phase 2C
acceptance suite: PASS (398 tests).** Phase 3 has not started or been authorized.

## Remediation assessment

H-01 is resolved. The original two acceptance regressions are unchanged and now
pass. All original 373 tests remain, plus the two audit regressions and 23 new
ownership cases: **398 passed, 1 existing warning**. Telegram visual redesign has
not resumed. The original failed audit is preserved below as historical evidence.

| Category | Current status | Evidence / scope |
| --- | --- | --- |
| Accounting and portfolio reconciliation | PASS | 342 exact authoritative snapshots across 18 synthetic runs; independent signed-cash/fees/P&L checks; completed real partial-fill history unchanged by stale callbacks. |
| Run identity / duplicate-run isolation | PASS | All 18 repeats reuse the same run/history; no compounded portfolio; event-ID algorithm unchanged. |
| Persistence, restart and ownership | PASS | Both original regressions, schema 2→3 preservation/backfill, restart/takeover/non-reuse, expired-lease rejection and concurrent-terminal tests. |
| Confirmed-fill notification provenance | PASS | Existing unchanged adapter/notification tests pass; no signal-to-trade shortcut added. |
| Partial fills / closures / negative P&L / breaches | PARTIAL | Existing implemented synthetic cases pass. Minimal replay still does not generate partial SELL exits for a complete end-to-end lifecycle test. No visual redesign was requested in this remediation. |
| Notification failure isolation | PASS | Event/outbox/status rollback is tested at both outbox insertion and final status update; original mocked transport/retry tests pass. |
| Authentication / sessions | PASS | Existing auth tests and actual authenticated browser/API smoke pass; security code unchanged. Trusted-LAN HTTP remains unencrypted. |
| Explicit LAN / localhost default | PASS | Existing binding tests pass; smoke server bound only to loopback and was stopped. |
| Secret handling | PARTIAL | No secrets/configuration inspected or printed in remediation; no credential-loading/network code added. Original bounded scan evidence/limitations below remain, not a universal secret-absence certification. |
| Activity / freshness correctness | PASS | H-01 can no longer manufacture a current-run failure/latest error/critical alert; expired owners cannot renew. Existing selected-run/freshness limits remain. |
| Synthetic labels | PASS | Existing labels/tests and actual CLI/browser output remain synthetic research only. |
| No broker execution | PASS | Original broker / Phase 2B regression subset: 66 passed. No adapter changes, broker requests or order placement. |

PASS here is bounded acceptance of implemented behavior, not certification for
live use. The two PARTIAL categories retain the original documented limits and
are not disguised as new features or universally verified properties.

### Root cause and exact reachable path

`recorded_backtest`'s observer calls `HistoryStore.heartbeat`. Once a successor
takes over, heartbeat raises lease loss. The adapter's `except AtlasError` handler
then calls `fail_run` using the obsolete identity. Previously, `fail_run` appended
RESEARCH_FAILED and inserted the critical outbox row **before** its qualified
UPDATE; zero affected rows were ignored. The transaction therefore committed
false history/notifications even when the successor was already COMPLETED.
The monetary ledger itself was not modified by this defect.

### Fencing and transaction boundaries

- The existing adapter generates a UUID for every attempt. Schema 3 adds
  `run_attempts(run_id, owner, claimed_at)` with a composite primary key. The
  claim transaction rejects identity reuse, preventing an old identity from
  regaining authority after restart/takeover. Migration backfills current owners
  and leaves historical event IDs, outbox rows and reports intact.
- Claim, heartbeat, completion and failure all acquire `BEGIN IMMEDIATE` before
  sampling lease time. A terminal write requires matching owner, RUNNING status
  and `lease_until > now`. The validation is inside the same write transaction
  as every resulting mutation; another writer cannot take over between them.
- Completion commits the batch, one consistent completion event, report, status
  and opted-in notification rows together. Cross-run, duplicate or conflicting
  terminal batches roll back. Stale completion raises the existing lease error.
- Failure commits the static error, optional outbox row and ERROR status together
  for a valid owner. Stale/expired/repeated callbacks return false without writes,
  leaving the adapter's original exception intact. Current-owner genuine failures
  still commit and queue their configured alert.
- A terminal commit clears ownership and consumes RUNNING state. Racing completion
  and failure calls have one winning outcome. No Python/global lock, long-running
  research computation or Telegram request was added inside a write transaction.
  Existing notification-delivery claim/finish fencing is unchanged.

Source: [migration, claim and heartbeat](../src/atlas/history.py),
[terminal writes](../src/atlas/history.py),
[unchanged orchestration](../src/atlas/research_history.py).

### Changes and regression evidence

Production change: `src/atlas/history.py` only. Added
`tests/test_worker_ownership.py` (23 cases). Updated this audit,
`docs/phase2c_architecture.md`, `docs/phase2c_report.md` and the stale acceptance
notice in `docs/telegram_setup.md`. No pre-existing tests were edited or removed.

The [two original regressions](../tests/test_phase2c_acceptance.py) cover stale
failure after an actual successor finishes or while it is still active. The
[new cases](../tests/test_worker_ownership.py) additionally cover stale completion,
lease expiry and exact boundary, identity reuse across restart, competing claims,
completion/failure and duplicate-terminal races, repeated failure, legitimate
failure, schema migration, cross-run/contradictory batches, rollback at both write
stages, and reconciliation of actual partial-fill portfolio snapshots.
Barriers coordinate competing threads; clocks are injected rather than slept.
A delayed-transaction test verifies time is sampled after acquiring the lock.
All SQLite files used by these tests are isolated from user history.

### Commands actually executed for remediation

PowerShell, repository root `D:\Jafar's Work\project-atlas`. Every Python command
uses `.\.venv\Scripts\python.exe`; that exact prefix replaces `PY` below.
The browser command was permitted to start temporary loopback server/Chrome
processes and stopped both afterwards. No real Telegram test command was invoked.

| Command | Exit | Result |
| --- | --- | --- |
| `PY -m pytest -q` before fix | 1 | 2 failed, 373 passed, 1 warning in 50.63s; both ownership defects independently reproduced. |
| `PY -m ruff format src/atlas/history.py tests/test_worker_ownership.py` | 0 | Only the two changed Python files formatted. |
| `PY -m pytest -q tests/test_phase2c_acceptance.py tests/test_worker_ownership.py tests/test_activity_history.py tests/test_phase2c_cli.py` | 0 | 59 passed in 14.92s. |
| `PY -m pytest -q` after fix | 0 | **398 passed, 1 warning in 53.13s**. |
| `PY -m pytest -q tests/test_broker.py tests/test_phase2b_acceptance.py tests/test_phase2b_remediation.py` | 0 | 66 passed. |
| `PY -m ruff check .` | 0 | All checks passed. |
| `PY -m ruff format --check .` | 0 | 53 files already formatted. |
| `PY -m atlas.main research-check` | 0 | GBP 1,000 initial capital; GBP 5 / 10 / 10 risk settings; offline checks pass. |
| `PY -m atlas.main synthetic-backtest --history-db local/phase2c-ownership-smoke.sqlite3` | 0 | Reconciled: GBP 1,004.56 equity; gross GBP 4.90, costs GBP 0.34, net GBP 4.56; no open positions. |
| `PY -m atlas.main dashboard-check --history-db local/phase2c-ownership-smoke.sqlite3` | 0 | Schema 3, integrity OK; authentication correctly reports setup required on this isolated DB. |
| `PY -m atlas.main notifications-preview --history-db local/phase2c-ownership-smoke.sqlite3` | 0 | Actual synthetic confirmed SELL preview; explicitly NO MESSAGE SENT. |
| `PY local/phase2c_acceptance_probe.py` | 0 | 18 runs, 342 snapshot matches, 18 each cash/cost, restart and duplicate checks; outboxes empty. |
| `PY local/phase2c_browser_smoke.py` | 0 | Startup/authenticated API/6 pages/trade details pass; 12 cards, 3 charts, no JS exceptions, no mobile overflow at 390px. |

The CLI smoke process explicitly set `ATLAS_TELEGRAM_ENABLED=false`. Research
commands require no credentials and do not contact a broker. Tests retain the
autouse prohibition on external HTTP; Telegram transports use mocks. Browser
screenshots/results are ignored local artifacts under `local/phase2c-preview`.
The existing warning is Starlette's HTTPX TestClient deprecation; no test or
dependency was weakened to hide it. Artificial results above do not establish
profitability and are not live account performance.

### Remaining limits, upgrade procedure and final decision

Stop all old Atlas research/dashboard/notification processes before deployment,
back up the database with the existing online backup command, then restart with
this revision. Migration cannot patch code already loaded in an old interpreter.
Verification did not alter the user's default database, passwords, sessions or
outbox. Historical false events/alerts are not automatically removed or resent.

Schema 3 preserves existing records and current owners; pre-migration discarded
attempt identities cannot be reconstructed. The adapter always issues fresh UUIDs.
This fence protects cooperating current-code workers, not arbitrary local Python
or direct SQL access to the database. Leases use system UTC; clock corrections
can cause fail-closed expiry or delayed takeover. There is still no durable
in-flight ledger reconstruction, production calendar/data guarantee, exactly-once
Telegram delivery, public hosting or broker execution. No new physical-phone,
TLS, Wi-Fi or real-message delivery verification was performed.

- Ownership defect H-01: **PASS / remediated**.
- Complete current automated Phase 2C acceptance suite: **PASS**.
- Freeze the bounded offline Phase 2C foundation: **YES**, with these documented
  limits and the restart procedure. The visual-upgrade deliverable is still pending.
- Telegram visual redesign can safely resume under a new explicit request: **YES**;
  no styling, HTML/image cards or delivery changes were made in this task.
- Phase 3: **NOT STARTED**. Proceed only after the visual upgrade passes its own
  acceptance gate and the user separately authorizes Phase 3. This report grants
  no trading or broker-execution permission.

## Original independent audit — pre-remediation evidence

The remaining sections record the initial FAIL and commands/results from that
earlier audit. They describe the pre-fix revision, not current remediation status.

27 October 2026. **Original overall: FAIL — not ready for Phase 3.**

A gate-blocking **HIGH persistence/audit-integrity defect** was reproduced.
The Telegram visual redesign was stopped under Part A's explicit safety gate.
No production fix, HTML redesign, image-card implementation, dependency/font
installation or real notification test was performed.

The user reports that dashboard and Telegram delivery work. That operational
confirmation is recorded; this audit did not independently repeat a real send.
Successful nominal operation and the original 373 passing tests do not cover
the ownership failure demonstrated below.

## Scope and method

Inspected the actual event/store/replay adapter, dashboard read models/API,
authentication/binding controls, Telegram delivery/setup/CLI, tests, templates,
JavaScript and Phase 2C documentation. Used temporary SQLite databases, the real
replay and ledger, simulated lease takeovers and existing mocked transport tests.
Read the default user database only for a bounded credential-pattern scan;
no auth/session values, environment secrets or private .env contents were read.

An initial code/test digest covers 53 production/assets/configuration and
pre-existing test files. Those protected files remain unchanged during this
audit. Only the new regression file and requested documentation were changed.

## Category assessments

| # | Acceptance category | Status | Evidence and limits |
| --- | --- | --- | --- |
| 1 | Equity, cash, realized/unrealized P&L and costs reconcile with ledger | PASS | Independent observer comparison matched **342 persisted valuations** with actual PortfolioLedger snapshots over 18 runs. Independently summed signed fill notionals/FX and fees matched cash; source realized P&L and total costs matched. [Adapter](../src/atlas/research_history.py#L43), [validated valuations](../src/atlas/activity.py#L92), [existing reconciliation tests](../tests/test_dashboard.py#L127). |
| 2 | Repeated research runs cannot accidentally compound trades/performance | PASS | All 18 identical-input/code repeats reused run ID/history with unchanged event counts; portfolios remain selected-run scoped. [Run identity](../src/atlas/research_history.py#L99), [idempotency tests](../tests/test_activity_history.py#L36), [notification deduplication](../tests/test_notifications.py#L206). Explicit new-run experiments remain separate. |
| 3 | Persistent history remains consistent through restart/recovery | **FAIL** | Ordinary reopen/migration/rollback checks pass, but a superseded worker can append RESEARCH_FAILED and queue an alert for the successor's RUNNING or COMPLETED run. **H-01** violates the recovery ownership boundary. |
| 4 | Trade notifications come from confirmed simulated fills, not signals/intents | PASS | Adapter consumes confirmed ledger FillRecords; NOTIFIABLE excludes signals and intents. Critical alerts, summaries and explicit connection tests are legitimate non-trade notification types. [Fill adapter](../src/atlas/research_history.py), [allowlist/provider](../src/atlas/notifications.py), [notification tests](../tests/test_notifications.py#L183). H-01 separately undermines system-error notification validity. |
| 5 | Partial fills, closures, losses and risk breaches display correctly | PARTIAL | Generated partial BUY/full-close, gap-loss and unit cases pass source checks and probes. [Partial-fill test](../tests/test_activity_history.py#L74), [currency tests](../tests/test_dashboard.py#L158), [gap alert test](../tests/test_phase2c_cli.py). The minimal runner does not generate partial SELL exits, so that complete adapter→UI→notification lifecycle was not independently exercised. Rich missing-P&L/HTML presentation belongs to the blocked redesign. |
| 6 | Delivery failure cannot change portfolio or trading state | PASS | Provider/worker update only outbox and delivery audit records. Existing success/failure/retry tests and source inspection show no ledger/risk/broker mutation path. [Delivery tests](../tests/test_notifications.py#L77), [bounded uncertain-response retries](../tests/test_notifications.py#L113). H-01 concerns a research-worker failure handler, not monetary effects of message retries. |
| 7 | Dashboard authentication and sessions are secure within documented scope | PASS | Salted scrypt, random opaque cookies with hashed-at-rest sessions, expiry, local password reset/session revocation, persisted login limits, strict origins/hosts, request bounds and no sensitive validation echoes. [Auth](../src/atlas/dashboard_security.py#L59), [API boundaries](../src/atlas/web/app.py), [auth tests](../tests/test_dashboard.py#L68). Trusted-LAN HTTP is explicitly unencrypted; this is not public-service security certification. |
| 8 | Localhost default and explicit LAN enablement | PASS | Default 127.0.0.1:8000; specific RFC1918 IP plus --lan required; insecure LAN needs an extra acknowledgment; authentication remains required. Wildcard/public binds rejected. [Settings](../src/atlas/dashboard_security.py#L26), [binding tests](../tests/test_dashboard.py#L182). No firewall/router/tunnel changes. |
| 9 | Secrets absent from inspected logs/events/tracked files | PARTIAL | At scan time: 40 tracked files, 68 public candidates, zero selected credential-pattern matches; default DB: 61 events / 64 research/activity rows, zero matches; zero application log files found in inspected locations. Auth/session values intentionally excluded. Private paths are Git-ignored. Arbitrary bare secrets, unobserved console/external logs and custom DB paths cannot be universally certified by pattern scans. |
| 10 | Bot activity and freshness accurately represented | **FAIL** | Stale leases expire and historical data stays marked stale, but H-01 creates an unqualified RESEARCH_FAILED/latest error and critical outbox entry for a healthy successor/completed run. [Health view](../src/atlas/dashboard_models.py#L336) consumes that false run-level error. Health is also selected-run scoped and should not be read as a global process census. |
| 11 | Synthetic results never labelled live performance | PASS | Storage accepts only SYNTHETIC; HTML banners, KPI notes, historical freshness, fills and current notification text retain simulation labels. No fabricated chart samples are added. [Contracts](../src/atlas/activity.py), [storage guard](../src/atlas/history.py#L176), [dashboard template](../src/atlas/web/templates/dashboard.html). |
| 12 | No broker order execution | PASS | Trading212Client retains the fixed demo account-summary GET only. Dashboard has no order/risk-policy/activation endpoint. Original broker/Phase 2B acceptance subset: 66 passed. [Broker](../src/atlas/broker.py), [route safety tests](../tests/test_dashboard.py#L198). No broker request or order was made by this audit. |

PASS means the bounded implemented path was verified; it does not erase scope
limits. The FAIL categories block final acceptance despite the monetary checks
passing. No evidence of cash/equity corruption was found in the reproduced defect.

## H-01 — HIGH: failure path is not fenced by current run ownership

Source: [HistoryStore.fail_run](../src/atlas/history.py#L237),
[claim_run / heartbeat](../src/atlas/history.py#L138),
[adapter failure handlers](../src/atlas/research_history.py).

**Root cause:** fail_run appends the system.error event and inserts its optional
outbox entry before executing the owner-qualified UPDATE. That UPDATE does not
check status and its affected-row count is ignored. When the owner no longer
matches, the update affects zero rows but the earlier writes still commit.

The adapter calls fail_run when heartbeat raises a lease-loss error. Therefore
this is reachable through the actual recording workflow, not merely hypothetical
direct misuse of an internal accounting primitive.

### Reproduction A: replacement still RUNNING

1. Start an opted-in synthetic run with worker A.
2. Simulate A being paused past its lease; worker B successfully takes ownership.
3. Resume A. Its heartbeat correctly rejects the obsolete owner.
4. A's exception handler calls fail_run.

Observed: B still owns a RUNNING row, but **one RESEARCH_FAILED event and one
PENDING critical notification** have been committed by A. There is no attempt/
owner context distinguishing an obsolete worker's cancellation from a failure
of the run B currently owns.

Regression:
[test_replaced_worker_cannot_publish_failure_or_enqueue_alerts_for_current_owner](../tests/test_phase2c_acceptance.py#L29).
Fails because expected error count 0 is actually 1.

### Reproduction B: replacement already COMPLETED

The second regression resumes A only after B has completed the same actual
fixture successfully. Observed:

| State | Before stale failure | After stale failure |
| --- | --- | --- |
| Current run status | COMPLETED | COMPLETED |
| Event count | 29 | **30** |
| Critical outbox entry | None | **1 PENDING** |
| Authoritative report / equity | Valid | Unchanged |

Regression:
[test_late_failure_callback_cannot_contaminate_a_completed_run](../tests/test_phase2c_acceptance.py#L61).
Fails because expected event count 29 is actually 30.

Both tests drive recorded_backtest's real observer/error-handler path. They use
temporary databases, no token/chat ID, no real network, no broker operations and
no skips/xfails. A separate direct-state probe confirmed the PENDING rows.

### Impact and required remediation

An obsolete process can publish false run-level failure history, contaminate
system-health/error presentation and cause a later explicitly enabled sender to
deliver a misleading critical alert. The completed portfolio report remained
correct; this finding must not be described as proven monetary corruption.

Required, **not implemented here**:

- Validate current owner and RUNNING state inside the same write transaction
  **before** appending an error or adding an outbox entry.
- Reject or safely ignore failure callbacks from superseded/completed attempts;
  zero-row ownership updates must not commit their side effects.
- Preserve atomic legitimate failure/event/outbox transitions, original lease-loss
  diagnostics and idempotency.
- If obsolete-attempt cancellation is retained for observability, design it as a
  distinct attempt-scoped record that cannot masquerade as current run failure.
- Make both regressions and the complete suite pass before redesign/Phase 3.

This is the requested stop condition: persistence/audit integrity is a safety
boundary for reliable alerts. The audit did not patch production code to hide it.

## Commands and actual results

Working directory: `D:\Jafar's Work\project-atlas`. All commands use the existing
`.\.venv\Scripts\python.exe`; no software installation occurred.

| Command | Exit | Observed result |
| --- | --- | --- |
| `-m pytest -q`, initial baseline | 0 | **373 passed, 1 warning in 29.02s** |
| `-m ruff check .`, initial | 0 | All checks passed |
| `-m ruff format --check .`, initial | 0 | 50 files already formatted |
| `-m pytest -q tests/test_phase2c_acceptance.py` | 1 | **2 failed in 0.72s** after finalizing the two end-to-end ownership regressions |
| `-m pytest -q`, after regressions | 1 | **2 failed, 373 passed, 1 warning in 43.75s** |
| `-m pytest -q tests/test_broker.py tests/test_phase2b_acceptance.py tests/test_phase2b_remediation.py` | 0 | **66 passed in 0.62s** |
| `-m ruff check .`, final | 0 | All checks passed |
| `-m ruff format --check .`, final | 0 | 52 files already formatted, including this report/new regression file |
| `-m atlas.main research-check` | 0 | GBP 1,000 virtual capital; GBP 5 / 10 / 10 provisional budgets |
| `-m atlas.main synthetic-backtest --no-persist` | 0 | Synthetic XLON ending equity GBP 1,004.56; net GBP 4.56; costs GBP 0.34; reconciled |
| `local/phase2c_acceptance_probe.py` | 0 | 18 scenarios/runs; 342 exact ledger-snapshot comparisons; 18 cash/cost, restart and duplicate checks |
| Temporary-DB invocation of `notifications-preview` | 0 | Synthetic confirmed-fill preview; outbox remained empty; no send |
| Temporary-DB invocation of `dashboard-check` | 0 | Integrity OK; no password was installed/changed |

The existing warning is Starlette's HTTPX TestClient compatibility deprecation,
not a new acceptance defect. The formatter was applied only to the new test file.
Protected production files/original tests retain their initial digest.

The independent probe and privacy scan were run with Python local here-strings/
an ignored local probe file. The former used only temporary original artificial
datasets across XLON/XNYS/XNAS: orb, gap, partial_fill, no_fill, sideways, time_exit.
The privacy scan used read-only SQLite and never displayed matched values or
read authentication/session records. Git check-ignore confirmed .env, virtualenv,
logs, database/WAL and local-key exclusions.

## Telegram visual upgrade disposition

**Stopped.** Existing plaintext formatting, provider requests, outbox, deduplication,
retry policy, redaction and explicit test command are unchanged.

- Supported Telegram HTML/emoji styling: not implemented because acceptance failed.
- Image cards: not implemented/investigated after the stop condition; no Pillow,
  browser renderer, proprietary font or cosmetic dependency was installed.
- Existing plaintext remains; no new HTML/caption/image-fallback claims are made.
- New HTML escaping, format-validity and card tests await the unblocked redesign.
- No real test notification was automatically sent.

Before, current plaintext excerpt using entirely fictional values:

```text
PROJECT ATLAS
SYNTHETIC RESEARCH ONLY
Confirmed simulated entry
Instrument: FICTIONAL-AAPL / XNAS
Side: BUY | Filled: 2
Fill: 245 USD (USD)
GBP reporting value: N/A - validated FX unavailable
Fees: GBP 0.10
Strategy: fictional-orb-v1
```

This is an abbreviated fictional example, not a fee/FX assumption or investment.
**After: unavailable — no redesigned notification was produced.** Providing a
fictional premium after-message as if implemented would misrepresent the gate.

## Changes, limitations and Phase 3 readiness

Created `tests/test_phase2c_acceptance.py` and this audit. Updated
`docs/telegram_setup.md` and `docs/phase2c_report.md` with the failed acceptance
status/user confirmation. Production, strategy, risk limits, trading logic and
existing tests were not changed. No defects were fixed in this audit.

Existing limits remain: synthetic fixtures/costs/FX, one instrument/session,
no continuous scanner, no in-flight ledger reconstruction, best-effort message
delivery with possible duplicates after uncertain responses, and unencrypted
trusted-LAN HTTP unless user-configured TLS is used. No new physical-phone/TLS/
real Telegram connectivity test was performed.

**Phase 3 readiness: FAIL.** Complete a bounded H-01 remediation and rerun the
375 current tests/quality checks first; then resume the explicitly requested
notification redesign and audit its formatting/fallback behavior. This audit
does not start or authorize Phase 3.

