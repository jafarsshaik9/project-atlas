# Phase 4A acceptance report

Date: 2026-10-28. Repository: `project-atlas`, branch `main`.
Accepted starting checkpoint: `a612e55979e5b3f607010bac106f4d50028ffc11`.

## 1. Implementation verdict

**PASS for the offline Phase 4A scope.** Immutable current observations, deterministic
mock feeds, fail-closed validation, exchange-aware bounded scans, durable checkpoints,
worker leases/fencing and offline diagnostics are implemented. No strategy dispatch,
simulated execution, real market adapter or Phase 4B implementation is included.

The clean starting tree and accepted commit were verified. The unmodified baseline
was run first: **864 passed**. The final full suite passed **1038 tests**.

## 2. Files changed

Modified:

- `src/atlas/main.py`: early isolated `paper-data` CLI route and command discovery.

Added:

- `src/atlas/paper_data/__init__.py`
- `src/atlas/paper_data/contracts.py`
- `src/atlas/paper_data/gates.py`
- `src/atlas/paper_data/mock.py`
- `src/atlas/paper_data/store.py`
- `src/atlas/paper_data/scheduler.py`
- `src/atlas/paper_data/cli.py`
- `tests/test_paper_data_contracts.py`
- `tests/test_paper_scheduler.py`
- `tests/test_paper_data_cli.py`
- `docs/phase4a_paper_data_architecture.md`
- `docs/phase4a_acceptance_report.md`

No dependency, historical contract/calendar, research engine, risk/accounting engine,
dashboard, broker, notification, credential or existing-test files were changed.
Ignored smoke-test databases were created under `local/phase4a-acceptance/`.

## 3. New tests

**174 new parametrized test cases**, in three modules:

| Module | Evidence |
| --- | --- |
| `test_paper_data_contracts.py` | Three source clocks, required/malformed timestamps, invalid monetary values, unknown units/currency, finality/revisions, immutable payloads, identity/mapping mismatches, age/transport thresholds, delayed/realtime/replay classification, entitlement scope/expiry/delay, policy validation |
| `test_paper_scheduler.py` | All mock fault scripts, opens/closes, both venues, DST, holidays/weekends, early closes, multi-session restart, cadence, expiry and takeover, four competing workers, simultaneous scans, fencing, clock regression, transactional fault injection, process-death recovery, shutdown, schema/config drift and read-only diagnostics |
| `test_paper_data_cli.py` | Every diagnostic command, restart replay, stale/delayed/unverified feeds, closed-session cursor retention, input redaction, missing-file protection, socket/HTTP/credential guards, fresh-process forbidden-import checks and package import audit |

Existing risk/accounting, historical replay, broker-mock, history/ownership, dashboard
and notification regressions passed as part of the full suite.

## 4. Validation results

Executed using the repository virtual environment:

| Check | Result |
| --- | --- |
| Baseline `python -m pytest -q` | 864 passed; 93.05 seconds |
| New Phase 4A test modules | 174 passed; 8.98 seconds |
| Full `python -m pytest -q` | **1038 passed**; 89.61 seconds |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 103 files already formatted |
| `python -m pip check` | No broken requirements found |
| `git diff --check` | Passed; only local LF/CRLF conversion notice |
| Guarded offline CLI subprocess smoke checks | **10 passed** |

The full suite reports 245 dependency deprecation warnings: the existing Starlette/
httpx integration warning and pandas timezone-array warnings from calendar use.
There are no test failures or skipped existing tests. No dependency changes were
made to suppress warnings.

## 5. Scheduler recovery evidence

- A child process acquired a one-second lease and died with exit code 73 after a
  real scheduler scan wrote its transaction, but before commit. Reopening recovered
  cursor 0, no committed result and no seen identity. At exact expiry a new worker
  acquired fence 2, consumed both observations exactly once, and SQLite integrity
  checking returned `ok`.
- SQL triggers injected failures at result insertion and cursor update. Cursor,
  deduplication and audit writes all rolled back. Retrying processed the observation
  successfully without a false duplicate.
- A restarted scanner retained the prior cursor/high-water mark. Redelivery was
  rejected as a duplicate; old ownership tokens could not scan, heartbeat or release
  the successor's ownership.
- Four simultaneous claimants produced one winner. Two simultaneous ticks under
  the valid token produced one eligible result and one `NOT_DUE`, with one audit.
- Exact-expiry and post-expiry mutations were refused. Tests advance time after
  obtaining the SQLite write lock to prove a queued worker cannot use pre-lock time.
- A session-close tick preserved the pending cursor. A new fenced worker resumed
  on the next eligible trading day. Halts and seen identities survived provider
  restarts, and a mismatched-epoch resume could not release a halt.
- Controlled shutdown retained the cursor and committed `STOPPED` with no owner.

These guarantees concern committed read-only evidence on local SQLite. They do not
claim exactly-once future order execution or certify physical power-loss behavior.

## 6. Freshness and entitlement evidence

The immutable source record cannot assert it is fresh. Assessment requires explicit
event/availability/receipt clocks and effective instrument metadata. Missing provider
availability remains missing; it is never replaced by receipt time.

Tests reject unknown units/currency, inconsistent clock order, future receipt,
stale event/receipt/transport, out-of-order events, unknown or mismatched delay,
nonfinal/revised data, closed/unknown sessions and unverified entitlement evidence.

Realtime classification passes only with matching artificial entitlement and actual
delay at most two seconds under the default policy. A three-second actual delay is
refused. Delayed data requires explicit opt-in, verified matching delay and the
delayed age limit; it is never promoted to realtime. The response scope is always
`MOCK_ONLY`, and no real provider entitlement is claimed.

The smoke checks demonstrated:

- `validate-feed` and verified mock `entitlement`: exit 0.
- Unverified mock `entitlement`: exit 1 with `ENTITLEMENT_NOT_VERIFIED`.
- Duplicate replay and its restart: unchanged counts of 2 eligible and 1 rejected.
- Stale replay: 1 rejected observation with `STALE_EVENT`.
- Opted-in delayed replay: 2 eligible observations labelled `PAPER_DELAYED`.
- State/ownership/freshness diagnostics: successful JSON reports; stopped ownership
  was inactive and no new processing occurred during inspection.

## 7. Network and broker isolation evidence

- Every Phase 4A diagnostic was exercised with sockets, HTTP sends, settings credential
  loading and credential environment access guarded against use.
- Fresh-process tests and the 10 smoke subprocesses forbade broker, notification,
  Massive and historical-provider imports while running the CLI.
- AST checks confirm the paper package imports no network, broker, notification,
  strategy, portfolio, risk or execution modules. The scheduler only accepts the
  concrete offline mock provider and has no execution callback.
- The existing test suite retains its external HTTP transport guard and mocked
  provider/broker interactions.
- `.env` was not opened or changed. No credentials, Massive/Trading 212 endpoints,
  Telegram sending, broker orders, simulated fills, subscriptions or billing
  operations were used. No commits or pushes were made.

## 8. Remaining limitations

Only artificial USD NYSE/NASDAQ observations are accepted. The existing calendar's
Nasdaq-to-NYSE alias, community exceptional-closure coverage and 2000–2030 bounds
remain explicit limitations. Calendar/policy/script drift requires a new stream or
a separately reviewed migration.

The scheduler is a bounded tick API with a finite CLI harness, not a deployed daemon.
The CLI uses a two-date calendar window and refuses inter-event gaps beyond its lease
safety limit. A programmatic host must drive heartbeats and supply a reliable clock.
Storage is intended for a single host with local SQLite, not distributed workers on
a network filesystem.

Equal event timestamps are refused after the accepted high-water mark. Provisional
and revised observations are retained but ineligible. Rejected source identities
remain seen. There is no corporate-action handling, bar building, FX sourcing,
retention/compaction, migration tooling or live strategy state in this package.
See the architecture document for the complete policies and operating limits.

## 9. Git status

Branch remains `main`; HEAD remains
`a612e55979e5b3f607010bac106f4d50028ffc11`.

Expected reviewable working tree: one modified tracked file (`src/atlas/main.py`),
seven new package files, three new test files and the two new documents above.
New files are untracked; nothing was staged, committed or pushed. Runtime databases
are ignored. No existing user changes were present at the start.

## 10. Phase 4B readiness

**Ready as an offline data and scheduling foundation.** Phase 4B still needs a
separately specified observation-to-strategy adapter, durable strategy/intent state,
fresh FX and portfolio inputs, mandatory risk gating and an atomic/idempotent
simulated execution/accounting design. Real provider entitlements and connectivity
would require separate authorization and verification. None of those components
was implemented in Phase 4A.
