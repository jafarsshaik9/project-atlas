# Phase 3A-M: Massive implementation and controlled pilot

Integration plan recorded before production changes, 27 October 2026.
Accepted parent: `85422982f2c99bef6dcade322acac0885cffe974`, branch main,
initial working tree clean. Baseline: 692 tests passed, 85 dependency warnings,
64.25s. **Current verdict: remediation PASS pending independent re-audit.**
The first invocation stopped before HTTP. After the owner saved local configuration
and explicitly approved a reviewed retry, two requests succeeded and 390 bars were
stored with WARNING, then verified from a fresh process. Independent audit remains
required; this is not broad-market, point-in-time strategy or Phase 3B acceptance.

## Boundaries and implementation plan

- Add a dedicated US common-stock adapter and minimal dated reference evidence.
  Use only api.massive.com, Bearer headers, explicit one-minute/unadjusted or
  split-only adjustment and original epoch-millisecond timestamps. No invented FX,
  volume, publication time, corporate-action completeness or dividend adjustment.
- Extend the existing sanitized RawPage contract to bounded long continuation
  cursors and add Massive-only normalization/evidence checks. Preserve every
  existing provider and publication/read-boundary regression.
- Use a separate shared SQLite quota database for all Massive endpoints, across
  dataset destinations/processes. Charge attempts conservatively through completion;
  serialize in-flight authority, enforce rolling limits plus spacing, persist
  backoff and fail closed on uncertain interrupted ownership.
- Load only MASSIVE_API_KEY on explicit authorised access, without exporting it,
  modifying .env or persisting credential-bearing URLs. No new dependencies.
- Bind long quota waits to existing import heartbeats. Preserve checkpoint chains,
  terminal publication, immutable datasets and restart verification.
- Finish mocked/offline tests, full pytest, Ruff and pip check before a real pilot.
  The initial AAPL pilot has a durable four-attempt lifetime cap, no automatic
  retries, and stops on every provider/security/data failure.

No broker, strategy, portfolio, risk, dashboard, Telegram or Phase 3B work is
authorised. Real pilot outcomes and final verification will be recorded below;
this plan is not a claim that a request or test has succeeded.

Official specifications reviewed: [authentication](https://massive.com/docs/rest/quickstart),
[custom aggregates](https://massive.com/docs/rest/stocks/aggregates/custom-bars),
[dated ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview).

## Implemented architecture and corrections during development

New modules: massive.py (HTTP, identity/evidence, normalization and pagination),
massive_credentials.py (lazy single-variable loader), massive_limits.py (shared
quota) and massive_pilot.py (explicit bounded orchestration and restart check).
The CLI adds a provider choice and explicit massive-pilot command. Source evidence
is revalidated by quality.assess at publication/read boundaries, including cross-page
ordering. RawPage permits <=8192-character cursors. Service binds quota waits to
existing ownership heartbeats. HistoricalStore and its fenced atomic publication,
checkpoint checks and backup implementation are unchanged.

The quota uses short SQLite transactions, one non-expiring in-flight token,
completion-time charging, a conservative rolling 61-second/four-attempt window
and >=16-second spacing. All endpoints, retries and processes share one default
quota file. System-wide monotonic ticks govern timing; separate UTC audit stamps
are operational, not provider publication time. Unknown interrupted ownership,
clock rollback and unsupported Retry-After periods fail closed pending review.

HTTP is header-authenticated, fixed-origin GET only, with no redirects/proxies,
explicit timeouts and bounded streamed reads. Permanent failures never retry.
The pilot permits one attempt per request and four lifetime attempts under one
durable identity. Unsafe continuations or known-key echoes cannot enter source
checkpoints or request URLs. The actual credential was used only for the explicitly
approved retry's two direct Massive header-authenticated requests.

Development tests exposed a fixture filename collision with the protected
research-history path; the new historical fixture was moved to its own filename,
preserving the no-side-effects assertion. A Retry-After boundary case now explicitly
tests indefinite fail-closed deferral rather than treating >24 hours as a short
wait. No existing acceptance test was modified, skipped or weakened.

## Offline acceptance before authenticated activity

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_massive.py --tb=short
# 110 passed, 80 calendar dependency warnings, 14.98s
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp <disposable-OS-temp>/cases
# 802 passed, 165 dependency warnings, 76.49s
.\.venv\Scripts\python.exe -B -m ruff check --no-cache .
# All checks passed
.\.venv\Scripts\python.exe -B -m ruff format --check --no-cache .
# 85 files already formatted
.\.venv\Scripts\python.exe -B -m pip check
# No broken requirements found
```

All 692 accepted tests remain intact; 110 new collected cases cover native numeric
precision, policies, USD/identity, epoch/DST normalization, malformed responses,
empty/missing/duplicate/out-of-order bars, permanent/transient errors, timeout,
credential handling/redaction, unsafe/cyclic/long pagination, source evidence,
interruption/restart, WAL backup/restore, and no ledger/notification side effects.
Thread and separately synchronised process tests show one quota owner; fake-clock
tests prove rolling limits, spacing, delayed dispatch, persisted state, Retry-After
and lease renewal over waits exceeding the original lease. The mocked pilot proves
fresh-process inspection, its four-attempt lifetime cap and stop-on-error behavior.

The 165 warnings are 1 known Starlette deprecation and 164 pandas/calendar warnings.
None were suppressed. No dependency was installed or upgraded. An offline subprocess
smoke harness disabled socket networking and used disposable databases for providers,
pilot-without-opt-in refusal, fixture download/inspect/validate/coverage, research-check
and synthetic-backtest --no-persist. All passed. Synthetic accounting still reconciles
GBP 1,000 initial capital / GBP 1,004.56 equity; this is artificial test output.

## Initial configuration stop (historical record, superseded by reviewed retry)

Only after the offline gate passed, the explicitly authorised command ran once:

```powershell
.\.venv\Scripts\python.exe -B -m atlas.main market-data massive-pilot --allow-network --json
# Exit 1; safely stopped before HTTP.
```

| Check | Observed result |
| --- | --- |
| Candidate session | 2026-10-26, selected as a previously completed regular US session |
| Start/finish UTC | 2026-10-27T22:14:41.073755+00:00 / 2026-10-27T22:14:42.057613+00:00 |
| Authentication | NOT ATTEMPTED / NOT VERIFIED |
| AAPL reference metadata | NOT REQUESTED |
| Minute aggregate availability / Basic entitlement | NOT VERIFIED |
| Session completeness, native USD and timestamp normalization | NOT VERIFIED against real data; offline fixtures pass |
| Actual response adjustment policy | NOT VERIFIED |
| Authenticated HTTP attempts | **0** |
| Rate-limit responses | **0** |
| Dataset | **NOT PUBLISHED**; neither accepted nor quarantined |
| Fresh-process actual-data inspection | NOT RUN; no data ingested |
| Safe error | MASSIVE_CREDENTIAL_MISSING_OR_AMBIGUOUS |

A read-only assignment-count diagnostic subsequently established **zero** exact
MASSIVE_API_KEY assignments in the saved local .env. Only the count was printed;
no values were displayed, exported, copied into project artifacts or modified.
Unsaved editor state cannot be used as a credential source. No authenticated API was contacted
and no entitlement inference is possible. This is a configuration blocker, not an
authentication rejection from Massive.

The owner then confirmed saving the assignment locally and explicitly approved a
reviewed retry. A disposable offline operational probe verified that only a STOPPED
zero-attempt record can be archived, ACTIVE/nonzero records are refused and the
four-attempt cap remains. The actual quota transaction verified zero attempts and
zero counters across the pilot prefix before archiving that record. No rate history
or nonzero counter was deleted/reset. The prior report is preserved at ignored
local/market-data/massive-pilot/report-zero-attempt-config-stop-20261027.json.
The successful retry and current report follow below. No broad integration test ran.

## Remaining limitations and review gate

Offline adapter and bounded-pilot self-verification: PASS. A genuine Basic response,
one AAPL regular session, metadata/adjustment compatibility and actual restart
validation passed. Broader instruments/history, real pagination and provider
revision completeness remain unverified. LSE is unsupported. Corporate actions,
revisions, point-in-time universes and original availability remain incomplete.

Unknown in-flight quota ownership is intentionally not auto-recovered. Reboot/
clock rollback and excessively long deferral may require manual review. Other
account clients must coordinate externally. Physical power loss/disk-full and
hostile local database edits were not tested. The quota database must not be
deleted/switched as a retry workaround. No clean environment rebuild was performed;
the accepted dependencies are unchanged.

Independent Phase 3A-M audit can begin. Real-data acceptance is limited to this
one-session pilot. Phase 3B is not ready or authorised. No broker, Trading 212,
Telegram, strategy/risk/accounting, billing, subscription or Git history changes
were made. No commit/push occurred.

Changed/new files: .env.example; README.md; docs/historical_data_quality.md;
docs/market_data_providers.md; docs/massive_provider.md; this report;
examples/market-data.massive.unapproved.json; src/atlas/market_data/cli.py,
contracts.py, quality.py, service.py, massive.py, massive_credentials.py,
massive_limits.py, massive_pilot.py; tests/test_massive.py.

## Final review additions

The final security review added four regressions: Massive source flags cannot
substitute fixture-known availability or descending order; oversized base-query
counts are rechecked during quality validation; and server-error Retry-After
defers the shared quota. These tighten new Massive behavior only. The actual pilot
was not repeated after its zero-attempt configuration stop.

The expanded focused suite passed: **114 cases, 84 calendar warnings, 15.62s**.
The final full-suite result is recorded below. No new dependency
or broker/strategy/accounting/notification code was introduced by this review.

A separate offline adversarial probe then demonstrated that base64-encoded
credential content in an opaque cursor could pass the initial raw-value check.
It used only a fictional key and made no request. The adapter now checks bounded
decoded layers for known-key echoes and private authentication fields before
checkpointing and rechecks after lazy key loading before any HTTP attempt. Five
new regressions cover raw-key encoding, JSON encoding, nested encoding, a foreign
private field and the lazy-load boundary. No real credential was exposed, and the
pilot was not retried after its successful, separately approved run.

## Reviewed retry: authoritative current pilot evidence

The same explicit CLI command ran after the 811-test security gate passed and
the owner approved recovery of the zero-attempt configuration stop. Exit code 0.

| Check | Observed result |
| --- | --- |
| Provider/instrument | Massive, AAPL, US common stock, XNAS dated primary listing |
| Session | 2026-10-26, 13:30:00Z inclusive to 20:00:00Z exclusive |
| Authentication / reference | SUCCESS; HTTP 200; native USD and canonical identity verified |
| Minute aggregates | SUCCESS; HTTP 200; 390 returned, 390 expected, 390 stored |
| Precision and timestamps | Exact Decimal normalization; original epoch milliseconds retained; UTC minute/session checks pass |
| Adjustment | Request adjusted=false; response adjusted=false; UNADJUSTED, no dividend adjustment |
| Dataset | ACCEPTED with quality WARNING, no FAIL findings |
| Original historical availability | UNKNOWN for every bar; zero strict-visible bars |
| Fresh-process integrity | PASS inside pilot; standalone validate and read-only inspection also PASS afterwards |
| Authenticated HTTP attempts | **2 total** across the original zero-attempt stop and retry; no automatic retries |
| Rate-limit responses | **0** |
| Start/finish UTC | 2026-10-27T22:24:23.043971+00:00 / 2026-10-27T22:24:41.826376+00:00 |

The metadata attempt was recorded at 22:24:24.033Z and aggregates at 22:24:40.459Z.
Read-only quota inspection verified **16.014 seconds** between first completion
and next claim, both statuses 200, two total counters and two recorded attempts.
The archived configuration-stop record remains STOPPED/0; the completed pilot is
DONE/2. The remaining budget is not permission for additional requests.

Dataset SHA-256:
`fdad08c5f11ca28385b43d19291676c3963687c0ac210e71d03061df69f64cb3`.
Job: `81a7a1b796636e130049937f47b32ba392850e6b75b2fa2c84b8e740d1dc634f`.
Ignored storage: local/market-data/massive-pilot/historical.sqlite3.
Current safe runtime report: local/market-data/massive-pilot/report.json.

Warnings are AVAILABILITY_UNKNOWN (390), CORPORATE_ACTION_COVERAGE_INCOMPLETE (1)
and SURVIVORSHIP_UNASSESSED (1). These were not waived or converted to PASS.
The account provided the required one-minute history for this session only;
this does not prove other depths, entitlements or full-universe coverage.
No further authenticated requests were made after completion.

All 119 new offline cases pass (84 calendar warnings, 15.68s). Final security-gate
suite: **811 passed, 169 warnings, 78.00s** (692 original + 119 new).
Final Ruff lint PASS, Ruff formatting PASS (86 files), pip check PASS. The post-pilot
full-suite result and final Git/security evidence are below.

## Final verification and Git/security evidence

Post-pilot full suite completed successfully: **811 passed, 169 warnings, 76.95s**.
All 692 accepted cases are retained, with 119 new offline Massive cases. No warnings
were suppressed. Ruff check PASS, Ruff format --check PASS (86 files), pip check
PASS. No dependency was installed/upgraded and no clean rebuild is claimed.

This fresh-process command exited 0 with WARNING and no FAIL after the live run:

```powershell
.\.venv\Scripts\python.exe -B -m atlas.main market-data validate fdad08c5f11ca28385b43d19291676c3963687c0ac210e71d03061df69f64cb3 --json --db local/market-data/massive-pilot/historical.sqlite3
```

Read-only inspection independently confirmed 390 expected/stored USD bars, UNKNOWN
original availability and zero strict-visible bars. The genuine dataset was also
backed up to a disposable OS temporary directory: restored manifest/report/bars
matched exactly, quality/checksums revalidated, immediate Windows rename/delete
succeeded and the temporary copy was removed. No further provider request or
credential loading was involved. Offline research CLI smoke and synthetic
accounting pass without persistence or notifications.

Branch main remains at `85422982f2c99bef6dcade322acac0885cffe974`, with 8 modified
tracked files and 8 new untracked files (the 16-file manifest above), none staged.
No commit/push occurred. Protected broker, config, risk, ledger, replay, history,
dashboard/notification and existing regression tests match that checkpoint.
.env remains ignored/untracked; Atlas did not modify the user's saved key. Runtime
quota/history/pilot reports and backups remain ignored. Token/private-key pattern
scans of changed public text found no candidates; real credential values were
never opened for those scans. The .env.example addition is a placeholder only.

**The pre-audit implementation verdict was PASS for the bounded pilot, but the
independent audit subsequently returned FAIL. The remediation record below is now
authoritative; a new independent audit is required.** Broader provider acceptance,
further live-data requests, and Phase 3B require separate review/authorisation. The
source, quota, point-in-time and corporate-action limitations above remain in force.

## Independent audit findings and remediation

All reproduction used fictional credentials, mocked HTTP and disposable databases.
No `.env` value was opened, no external request was made, and the genuine AAPL pilot
evidence was not modified.

| Finding | Original reproduction | Root cause | Resolution / regression evidence |
| --- | --- | --- | --- |
| M01 HIGH | Hex-encoded private JSON passed continuation validation. | Cursor privacy decoded Base64 only; a full untrusted URL was canonicalised into the checkpoint. | Checkpoints now contain a strictly validated token only; request URL/policy are reconstructed from constants. URL-safe grammar, query/host/userinfo allowlists, bounded Base64/hex/percent inspection, lazy-key recheck and query-log redaction fail closed. Regressions cover hex, Base64, nested/mixed encoding, credential fields, malformed/userinfo URLs, public download/checkpoint state and HTTPX logs. |
| M02 HIGH | Contradictory duplicate `adjusted` keys resolved to the last value. | Standard `json.loads` silently overwrote earlier object keys. | A recursive object-pairs hook rejects every duplicate key, identical or conflicting, while preserving Decimal parsing. Regressions cover status, adjustment, identity, nested OHLCV/pagination and high precision. |
| M03 MEDIUM | A gzip header delayed the 30-second check until 60 simulated seconds. | Deadline/heartbeat ran only when decoded `iter_bytes` yielded. | Atlas requests identity encoding, rejects compressed responses before iteration, uses raw chunks, checks headers/raw progress/final completion against the application deadline, bounds raw bytes and reduces each transport read timeout to the remaining budget. Regressions cover slow headers/body, compressed expansion input, cancellation/resource close and heartbeat/lease renewal. |
| M04 MEDIUM | A client clock 300 seconds ahead reduced a requested 90-second delay to one second. | HTTP-date subtraction used only the client clock. | Valid server Date is authoritative; significant client/server skew and malformed/missing evidence fail conservatively. Delta-seconds and bounded fallback policy remain explicit. Regressions cover 429/503, ahead/behind clocks, missing/malformed Date/Retry-After and persisted review blocks. |
| M05 MEDIUM | Dropping the attempt table caused startup to recreate empty history. | `CREATE ... IF NOT EXISTS` ran for existing stores. | Schema creation occurs only for a genuinely new path. Existing tables, columns, indexes and state are validated atomically; missing/damaged history requires review. Regressions cover new/valid stores, missing tables/columns/indexes, state corruption, interrupted empty creation, restart, concurrency and clock rollback. |

Before/after results: all five original probes changed from unsafe acceptance/delay
or recreation to deterministic rejection/fail-closed behaviour. The focused Massive
suite is **154 passed** (119 prior + 35 remediation). The complete suite is **846
passed, 169 warnings**; the warnings remain one known Starlette deprecation plus 168
calendar/pandas notices. Ruff lint and formatting and `pip check` pass. Offline CLI,
pagination/resumption, quota concurrency/restart, accounting and risk smoke checks
also pass. No existing test was deleted, skipped or weakened.

Remaining limitations are unchanged: actual provider pagination was not exercised by
the one-page pilot; original historical publication availability is UNKNOWN; strict
replay exposes zero pilot bars; corporate actions, revisions, point-in-time universes,
broad symbols/dates and LSE remain unverified. The application deadline combines an
application elapsed-time check with HTTPX transport timeouts; Python cannot forcibly
terminate a hostile custom synchronous transport, so only approved HTTPX transports
are supported. Damaged quota recovery is intentionally manual and conservative.

**Remediation verdict: PASS and ready for independent re-audit, not for Phase 3B.**
No commit/push or authenticated request was performed.
