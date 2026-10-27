# Phase 3A implementation and acceptance evidence

27 October 2026. **Overall verdict: PARTIAL; independent final acceptance required.**
The independent audit rejected the initial offline foundation. F01–F06 and W01–W02
were remediated, and their re-audit returned CONDITIONAL PASS with RA-01/RA-02.
Both remaining findings have now been reproduced and fixed; final evidence follows
the historical verification records below.
Authenticated provider compatibility and real LSE/US datasets remain unverified.
This is not a claim of real-data readiness or profitable strategy validation.

## Scope and safety

Gate 0 verified HEAD `8252b6de92ccb28716813d8e2ed8d726c1a4b6e0`, clean working tree,
505 passing tests, Ruff lint/formatting PASS before production implementation.
The integration plan was recorded in phase3a_architecture.md first. User approved
project-local calendar dependencies and a separate ignored rebuild environment.
User subsequently clarified that no dedicated provider account exists and asked
for offline completion and provider research without purchases/authentication.

No account, licence acceptance, purchase, real historical-data download, broker
request/order, Telegram delivery, Git commit or push occurred. Network use was
public official documentation and approved dependency installation only. Phase
3B/3C are not implemented. Strategies, accounting, risk, worker fencing, broker
and notification source/tests are unchanged. The root CLI adds isolated dispatch.

## Original implementation matrix (superseded by independent audit/remediation)

| Category | Result | Evidence / practical limit |
| --- | --- | --- |
| Baseline and existing regressions | PASS | All 505 original tests retained, with no modifications; complete suite below |
| Provider abstraction/offline fixtures | PASS | Typed provider protocol, descriptors, effective mappings, original deterministic paginated fixtures across XLON/XNYS/XNAS |
| External provider | PARTIAL | Twelve Data US REST adapter, fixed official endpoint and mocked HTTP verified; no account/schema/entitlement test against actual service |
| UK real minute coverage | PARTIAL / BLOCKED | Twelve Data lists XLON EOD; adapter excludes it. LSEG/LSE vendor specification, quote and sample still required |
| Contracts/currencies | PASS | Finite positive Decimal OHLC, nonnegative volume, UTC, interval/completion/metadata validation; explicit GBP/GBX/USD, no inferred FX |
| Availability/look-ahead boundary | PASS with limitation | UNKNOWN denied, explicit ASSUMED opt-in and boundary tests; no evidence of provider revisions/original latency |
| Calendars | PASS within declared coverage | Versioned XLON/XNYS cash sessions, NASDAQ alias explicit; DST, holidays, early/exceptional closures and schedule fingerprints tested |
| Quality and quarantine | PASS | Source order, metadata, malformed OHLCV/times, gaps, duplicates, partial bars, session/window violations; no interpolation/repair |
| Storage/recovery/checksums | PASS | Restart/idempotency, compressed checkpoint integrity, fenced leases, rollback, publication atomicity, manifest/action fingerprints and online backup |
| Corporate actions/universe | PARTIAL | Validated action and effective-symbol contracts; no fetched action history, adjustment, delisted universe or survivorship correction |
| CLI/integration boundaries | PASS | Offline commands and negative gate/quarantine smokes; historical imports do not write research history or notifications |
| Reproducibility | PASS on verified platform | Full-extra exact dependency snapshot, clean local rebuild, matched 37 existing dependency/tool versions, both full suites PASS |
| Security | PASS within review scope | No credential-shaped values found in new text files; ignored runtime/rebuild paths; old execution boundaries unchanged; all HTTP tests mocked |

## Original pre-remediation verification record

Run from the project directory in PowerShell. All success commands below exited
0; the two deliberately blocked/quarantined commands exited 1 as expected.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# FINAL: 594 passed, 85 warnings, 71.46s
.\local\phase3a-rebuild-venv\Scripts\python.exe -m pytest -q
# FINAL: 594 passed, 85 warnings, 71.28s
.\.venv\Scripts\python.exe -m ruff check .
# All checks passed
.\.venv\Scripts\python.exe -m ruff format --check .
# 77 files already formatted (final check after this report was added)
.\.venv\Scripts\python.exe -m pip check
.\local\phase3a-rebuild-venv\Scripts\python.exe -m pip check
# Both: No broken requirements found
.\.venv\Scripts\python.exe -m atlas.main research-check
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --no-persist
.\local\phase3a-rebuild-venv\Scripts\python.exe -m atlas.main research-check
.\local\phase3a-rebuild-venv\Scripts\python.exe -m atlas.main synthetic-backtest --no-persist
.\.venv\Scripts\python.exe -m atlas.main market-data --help
.\.venv\Scripts\python.exe -m atlas.main market-data providers
.\.venv\Scripts\python.exe -m atlas.main market-data instruments
.\.venv\Scripts\python.exe -m atlas.main market-data download --db local/phase3a-smoke/final.sqlite3
.\.venv\Scripts\python.exe -m atlas.main market-data download --venue XNAS --db local/phase3a-smoke/historical.sqlite3
.\.venv\Scripts\python.exe -m atlas.main market-data download --config examples/market-data.fixture.json --db local/phase3a-smoke/config-example.sqlite3
.\local\phase3a-rebuild-venv\Scripts\python.exe -m atlas.main market-data download --db local/phase3a-smoke/rebuild.sqlite3
```

Final default offline import produced ten artificial XLON GBX bars, ACCEPTED with
three warnings: partial-session request, incomplete actions, unassessed universe.
Main and clean environments independently produced the same dataset ID:
`b29c1aa14ebe3026b25734bf68a0e3d85804bf7186a6b7060a1de7355f407c89`.
The two extra pre-final hardening tests verify calendar coverage/venue and action
coverage declarations before any import job is created; focused total: **89 PASS**.

```powershell
$datasetId = 'b29c1aa14ebe3026b25734bf68a0e3d85804bf7186a6b7060a1de7355f407c89'
.\.venv\Scripts\python.exe -m atlas.main market-data validate $datasetId --json --db local/phase3a-smoke/final.sqlite3
.\.venv\Scripts\python.exe -m atlas.main market-data inspect $datasetId --db local/phase3a-smoke/final.sqlite3 > local/phase3a-smoke/final-inspect.txt
.\.venv\Scripts\python.exe -m atlas.main market-data backup local/phase3a-smoke/final-backup.sqlite3 --db local/phase3a-smoke/final.sqlite3
.\.venv\Scripts\python.exe -m atlas.main market-data coverage --db local/phase3a-smoke/final-backup.sqlite3
# Verified report/schema, same ID, ACCEPTED, 10 rows, artificial origin, execution NONE.
.\.venv\Scripts\python.exe -m atlas.main market-data download --provider twelve-data --config examples/market-data.twelve-data.unapproved.json --allow-network --db local/phase3a-smoke/unapproved.sqlite3
# Expected exit 1: approval/licensing refused; no database created, no HTTP request.
.\.venv\Scripts\python.exe -m atlas.main market-data download --scenario missing --db local/phase3a-smoke/final.sqlite3
# Expected exit 1: QUARANTINED; 9 raw/normalized bars vs 10 expected; missing minute retained.
```

The unchanged synthetic replay (both environments) reconciled starting GBP 1,000,
ending equity GBP 1,004.56, gross GBP 4.90, costs GBP 0.34, net GBP 4.56, one
simulated trade, zero open positions/risk-limit events. **SYNTHETIC RESEARCH ONLY
— NOT LIVE TRADING PERFORMANCE.** These are artificial values, not historical
market returns. No history persistence was requested for these replay smokes.

The 85 warnings are one existing Starlette HTTPX TestClient deprecation and 84
pandas timezone-array deprecations originating from the calendar dependency.
No warning was hidden or test weakened. Compatibility should be rechecked before
a future pandas/calendar upgrade; no unrelated packages were upgraded here.

Security checks used Git status/diff and comparison with the verified baseline;
protected modules and original acceptance/broker tests had no diffs. Credential
pattern review printed counts only, no values. `git check-ignore` confirmed
historical SQLite and rebuild environment paths under ignored `local/`. No real
`.env`, account secrets or private Telegram recipient configuration was opened.
This review is not a new complete historical Git secret-scan certification.
Final AST/text review covered 17 files: no credential-pattern matches, forbidden
trading/ledger/history/notification imports or HTTP mutation calls in the new
namespace. Its only external endpoint is official Twelve Data `/time_series`.

## Files created or modified

Modified: README.md (full extras, exact rebuild and historical CLI);
pyproject.toml (market-data extra); src/atlas/main.py (lazy command dispatch).

Created:

- src/atlas/market_data/__init__.py
- src/atlas/market_data/contracts.py — versioned instrument/bar/action/provenance contracts
- src/atlas/market_data/calendars.py — maintained and recorded schedule adapters
- src/atlas/market_data/providers.py — artificial fixtures and gated Twelve Data adapter
- src/atlas/market_data/quality.py — normalization, findings and human report
- src/atlas/market_data/store.py — isolated SQLite/jobs/checksums/backup
- src/atlas/market_data/service.py — bounded resumable import orchestration
- src/atlas/market_data/cli.py — providers/instruments/download/validate/inspect/coverage/backup
- tests/test_market_data.py — 89 offline acceptance/regression cases
- tests/fixtures/market_data/fixture_specification.json — original generator provenance/sample
- examples/market-data.fixture.json
- examples/market-data.twelve-data.unapproved.json
- requirements/phase3a-windows-py314.txt
- docs/phase3a_architecture.md
- docs/market_data_providers.md
- docs/historical_data_quality.md
- docs/phase3a_acceptance_report.md

Ignored local artifacts: approved rebuild venv, artificial smoke-test databases,
backup and inspection output in local/phase3a-smoke. No downloaded market data is
tracked; no Git staging/commit/push was performed.

## Dependencies and reproducibility

Python 3.14.10, Windows x64. New approved calendar stack: exchange-calendars 4.13.2,
tzdata 2026.5, numpy 2.5.4, pandas 3.1.0, pyluach 2.3.0, toolz 1.2.0,
korean-lunar-calendar 0.4.0, python-dateutil 3.0.0, six 1.17.0. HTTPX/Pydantic and
all earlier application dependencies were retained. See the exact requirements
snapshot for every installed dependency. The fresh rebuild also installs explicit
setuptools 82.0.1 (the verified editable-wheel generator) and uses no build isolation.
Rebuild installation and pip check succeeded, all 37 shared dependency/tool versions
match, and actual full tests/smokes match. This does not claim cross-platform or
bit-for-bit wheel reproducibility; wheel hashes/Python installation are not locked.

## Remaining limitations and next gate

1. **HIGH for real-history use:** no account-tested real provider, sample, verified
   entitlement, licensing/retention/display rights or real-data quality acceptance.
   US adapter is mocked; UK external minute source remains BLOCKED. Provider
   comparison recommends evaluating Massive Basic/Starter for US and separately
   verifying LSEG/LSE source pricing and specifications, with user approval.
2. **HIGH for strategy evidence:** original historical availability/revisions,
   corporate actions and point-in-time universe/delisted coverage are unresolved.
   UNKNOWN is fail-closed, assumptions explicit. No actual strategy evaluation
   against these historical contracts is wired in this phase.
3. **MEDIUM:** regular sessions only; NASDAQ shares XNYS calendar; incomplete
   exceptional closures/halts/auction coverage possible. Strict missing-minute
   quarantine can reject legitimate no-trade minutes until reviewed provider
   semantics justify a separately versioned policy.
4. **LOW:** small-request SQLite/JSON architecture, fixed bounds, no bulk/columnar
   benchmarks; provider quotas in other databases/clients require external
   coordination. Source sanitization deliberately prevents full wire recovery.
5. **LOW:** dependency deprecations and Windows-only exact-version snapshot; no
   supply-chain hash lock, Linux/Python 3.13 rebuild or authenticated download test.

Phase 3B can be considered only after separate approval and acceptance of a real
dataset/provider plus availability, corporate-action and universe policies. The
offline foundation is ready for that evaluation; **Phase 3B is not started or
certified ready for reliable strategy claims.** Broker execution remains disabled.

## Acceptance remediation — F01–F06 and W01–W02

The independent audit FAIL superseded the original matrix above. Before changing
production code, `pytest -q tests/test_phase3a_remediation.py --tb=short` reproduced
all eight findings in **12 failing cases**. Original tracked tests were retained.
No packages, accounts, credentials, provider access, broker endpoints or trading
logic were changed. All destructive simulations used disposable pytest databases.

| Finding | Reproduced root cause / before | Exact correction / after | Principal regressions in tests/test_phase3a_remediation.py |
| --- | --- | --- | --- |
| F01 HIGH | Public publication trusted caller-supplied bars/report/pages; cross-unit and wrong reports committed; missing/nonterminal checkpoints could complete | Rederive results from durable terminal chains and recorded calendar; bind full stored job context; compare exact bars, units/identity, ingestion times and reports. Recheck owner/lease/request/cursor/count/compressed source fingerprints in the publication transaction. Read paths reverify hashes **and** source semantics. Invalid batches cannot commit | test_f01_rejects_inconsistent_publication; test_f01_bar_binding_covers_every_identity_field; test_f01_publication_bound_to_job_and_exact_source; test_f01_read_rejects_semantic_corruption_even_with_valid_hashes |
| F02 HIGH | Only duplicate effective start dates were rejected; nested/open-ended overlaps selected an ambiguous old symbol | Pairwise same-provider half-open interval checks reject all overlaps; adjacency is valid; transition-spanning requests fail explicitly | test_f02_overlap_rejected; test_f02_nested_and_open_ended_overlap; test_f02_adjacent_mappings_allowed_transition_request_refused |
| F03 MEDIUM | SQLite transaction context managers did not close connections; Windows rename failed with WinError 32 | Explicit closing for constructor checks and backup source/target, including exception paths. Read/write connection helper always closes in finally | test_f03_windows_handles_close_without_collection; test_f03_exception_paths_release_connections |
| F04 MEDIUM | Every refresh generated another random job ID; no resumption parameter existed (initial reproduction raised TypeError) | resume_job_id / --resume-job targets an existing SHA-256 job. Exact request/provider/calendar/policy key must match. Fresh owner after interruption/expiry, preserved cursor/pages, active-owner rejection, completed-job cache deduplication. Interrupted CLI reports its safe job ID on stderr | test_f04_refresh_resumes_saved_job; test_f04_resume_context_must_match_exactly; test_f04_restart_resume_deduplicates_completed_job; test_f04_cli_reports_job_and_resumes_after_restart |
| F05 MEDIUM | Same 100→130.00001 prices produced different gap warnings at decimal precision 6/28; exponent case also depended on context | Exact integer-ratio gap comparison; 64 coefficient digits and absolute exponent <=64 bounds; stable uppercase Decimal JSON; isolated fixture arithmetic. Arithmetic policy included in v2 fingerprints | test_f05_quality_independent_of_decimal_context; test_f05_dataset_identity_and_source_do_not_depend_on_context; test_f05_scientific_notation_serialization_is_context_independent; test_f05_unbounded_decimal_values_quarantined |
| F06 MEDIUM | --json stdout began with a human banner and json.loads failed | Supported JSON commands emit exactly one JSON document, including errors; human diagnostics stay on stderr; validation FAIL still exits 1 | test_f06_json_stdout_is_one_document; test_f06_json_warning_and_quarantine_are_parseable; test_f06_json_pass_schema_and_corrupt_error |
| W01 LOW | Manifest ACCEPTED/10 rows appeared in coverage even after a row was deleted | Efficient coverage explicitly returns integrity=NOT_VERIFIED with a metadata-only explanation. Inspect/validate reject physical or semantic corruption | test_w01_metadata_coverage_does_not_certify_corruption |
| W02 LOW | Coverage on a missing path created an empty DB and returned success | Read commands open existing databases in SQLite URI read-only mode, with no directory creation, initialization or migration. Missing DB produces an actionable error/nonzero exit | test_w02_read_does_not_create_missing_database; test_w02_all_read_commands_refuse_missing_paths; test_w02_read_only_store_refuses_writes_and_does_not_migrate |

Additional adversarial regressions cover simultaneous terminal writes, ownership
replacement during validation, modified source bytes with unchanged checksum
columns, changed batches with recomputed valid dataset hashes, corrupt cached
versions, unsupported resume identities and compatible legacy-v1 verification.
PASS-report JSON formatting is tested with an explicit report stub; the default
fixture quality still correctly retains WARNING findings, not a false PASS claim.

### Transaction and compatibility details

The publication proof is computed outside the SQLite write lock, from one source
snapshot. A final BEGIN IMMEDIATE validates current authority and source bytes
before inserting manifest/quality/bars and completing the job in one transaction.
Racing checkpoint changes or replacement workers invalidate the proof. Repeated
terminal writes cannot duplicate datasets. Existing corrupt versions are rejected,
not overwritten by refresh. No network waits occur while holding the write lock.

The SQL schema stays v1; normalization is now historical-normalization-v2. New
manifests identify their original source job and page checksums. Source references
and actual ingestion times remain outside semantic dataset identity but inside
the manifest checksum. Fully compatible v1 records can verify without rewriting;
unbound/ambiguous legacy provenance, changed quality or corrupted records fail
closed and need a reviewed fresh import/restore. Explicit resumption requires the
current exact request/context; obsolete policy jobs are not silently upgraded.
Low-level callers must supply full import_context to begin_job before publication;
the normal service already did this. This safety correction is intentional.

### Remediation verification

Final verification completed successfully in the project .venv (Python 3.14.10):

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_phase3a_remediation.py --tb=short
# 67 passed, 4 calendar-dependency warnings, 6.19s
.\.venv\Scripts\python.exe -m pytest -q
# 661 passed, 85 warnings, 151.55s (594 prior tests + 67 new cases)
.\.venv\Scripts\python.exe -m ruff check .
# All checks passed
.\.venv\Scripts\python.exe -m ruff format --check .
# 78 files already formatted
.\.venv\Scripts\python.exe -m pip check
# No broken requirements found
```

The 85 warnings remain the known Starlette TestClient deprecation and 84 pandas/
exchange-calendar deprecations. No warnings were suppressed to obtain a pass.
All pre-existing tests were retained; Git comparison confirmed tracked tests and
protected broker, risk, accounting, replay, worker and dashboard sources unchanged.

A subprocess CLI smoke harness used only disposable OS temporary databases, with
Telegram disabled. It executed research-check and synthetic-backtest --no-persist,
then market-data download, inspect/validate --json, coverage, explicit completed
job resumption and online backup/restore. A missing-candle download/validation
exited 1 with a parseable FAIL report. Missing-database inspection exited 1 with
one ERROR JSON document and no created parent directory. Warning/FAIL/ERROR JSON
were parsed with json.loads(stdout), not by removing a banner. Windows backup
rename succeeded. The temporary directories were removed on exit.

The ten-bar default fixture was ARTIFICIAL_OFFLINE_FIXTURE, quality WARNING,
normalization historical-normalization-v2, metadata integrity NOT_VERIFIED.
Its content ID was
`bc97fb870943cd30be53c40bc6d472508cfbdc8cfa029f6c6d6dcd9c8067ec6d`.
Synthetic replay still reconciled GBP 1,000 starting cash, GBP 1,004.56 ending
equity and GBP 4.56 net P/L; no history persistence or notifications were requested.
These are artificial software checks, not real historical performance.

**Remediation self-verification: PASS for F01–F06 and W01–W02. Overall Phase 3A:
PARTIAL pending independent re-audit and external-data acceptance.**

Remediation files: src/atlas/market_data/contracts.py, providers.py, quality.py,
store.py, service.py, cli.py; new tests/test_phase3a_remediation.py; README.md;
docs/phase3a_architecture.md, docs/historical_data_quality.md and this report.
No existing Phase 2B/2C code or tests were edited. No commit/push was performed.

Remaining limitations: no authenticated provider request, actual entitlement,
LSE minute source, historical publication/revision evidence or comprehensive
corporate-action/universe acceptance. Community calendars retain documented
coverage limits. Version snapshot is platform-specific without wheel hashes;
dependency deprecation warnings remain. A fresh environment was not rebuilt or
modified during remediation. Real power-loss/physical-disk-full recovery was not
tested; deterministic transactional write-fault/ownership tests remain offline.

An independent re-audit is required to close the prior HIGH findings. Passing
mocked/offline tests does not establish real-data readiness. Phase 3B is not started.

## Final audit remediation: RA-01 and RA-02

This section supersedes the previous remediation's test totals and JSON syntax
limitation. Scope is the two remaining findings only. Branch remains main at
`8252b6de92ccb28716813d8e2ed8d726c1a4b6e0`; no staging, commit or push occurred.
The 661 existing tests were retained without edits. No dependency installation,
external API request, secret/.env access, Massive adapter implementation, broker
operation, Telegram delivery or Phase 3B work occurred.

### Reproductions and root causes

Before production changes, the six initial cases in
`tests/test_phase3a_final_remediation.py` all failed (0.65s):

| Finding | Reproduced behavior | Root cause |
| --- | --- | --- |
| RA-01 MEDIUM | Two synchronized workers both returned success for one destination; a competing creator's file was overwritten; an injected backup failure left a partial final file | `exists()` followed by `sqlite3.connect(destination)` was a check-then-open race. SQLite could open an already created destination. Work occurred directly at the final path, without publication/cleanup isolation |
| RA-02 LOW | Missing `--db` value, unknown option and missing dataset ID all exited 2 with empty stdout, so whole-stdout `json.loads` failed | Argument parsing happened before structured error handling. Argparse exited before JSON mode could be honored |

### Exact fixes and transaction/filesystem boundaries

**RA-01:** `HistoricalStore.backup` builds a unique private staging directory on
the destination filesystem. SQLite's online backup captures the committed source
snapshot, including committed WAL contents. The staged target is made standalone
with DELETE journal mode, checked against the historical schema and checked with
`PRAGMA integrity_check`. Source and target connections close explicitly before
publication. Windows `os.rename` refuses an existing destination; the POSIX branch
uses atomic exclusive `os.link`, since POSIX rename would replace a file. The
initial existence check is only a convenience; publication is protected by the
filesystem operation itself. There is no overwrite/copy fallback.

Only one publisher succeeds. Losing/failing workers remove only their own staging
directory; no cleanup path deletes the final destination. A source/destination
connection failure, partial SQLite write, failed integrity check or publication
failure cannot publish an incomplete final file or remove a competitor's file.
No database write transaction spans network work. Dataset publication and research
ownership code are unchanged.

**RA-02:** The selected inspect/validate subparser detects explicit JSON option
tokens before normal parsing, sharing a per-invocation flag with the parent parser.
Parser errors in that mode produce exactly one JSON stdout document:
`{"status":"ERROR","error":"INVALID_ARGUMENTS"}`, exit 2 and a static stderr
diagnostic. Raw parser messages/argument values are withheld. Valid JSON commands,
WARNING results and operational/validation errors retain their existing schemas
and exit codes (0 or 1). No banner or traceback surrounds the JSON document.

JSON text inside another option value (`--db=--json`) or a positional value after
`--` does not select this mode. Unsupported subcommands gain no JSON option.
Non-JSON usage/errors keep ordinary argparse behavior, help stays human-readable,
and reusing the parser does not retain JSON mode from a prior invocation.

### New regression coverage

The new file contains **31 collected cases**:

- Backup races: a barrier synchronizes two workers using different marker databases;
  exactly one succeeds and the resulting marker belongs to that winner. Separate
  probes create a competitor between existence check/open and at atomic publication.
- Existing destination/source rejection; source/destination connection failures;
  partial-backup, integrity and publication failures; cleanup while a competing
  destination exists. A failed operation leaves no final file of its own or ordinary
  staging debris, and preserves competing content.
- Restore of an actual artificial GBX dataset and committed WAL content; semantic
  dataset equality, standalone journal mode and SQLite integrity. Immediate Windows
  rename/delete and exceptional-path handle checks run with garbage collection
  disabled, so collection cannot mask a connection leak.
- Whole-stdout JSON parsing for inspect/validate, missing databases, missing option
  values, missing positional arguments, unknown/extra arguments and malformed JSON
  flags; fictional sensitive sentinel redaction in both output streams. Also tested:
  non-JSON usage, option-value/terminator boundaries, help and parser reuse.

After the source changes, the initial six cases passed. The expanded focused run
and final complete suite passed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_phase3a_final_remediation.py --tb=short
# 31 passed, 4 calendar-dependency warnings, 1.82s
.\.venv\Scripts\python.exe -m pytest -q
# 692 passed, 85 warnings, 64.14s (661 existing + 31 new)
.\.venv\Scripts\python.exe -m ruff check .
# All checks passed
.\.venv\Scripts\python.exe -m ruff format --check .
# 79 files already formatted
.\.venv\Scripts\python.exe -m pip check
# No broken requirements found
```

The complete suite still reports the known 1 Starlette and 84 pandas/calendar
deprecation warnings; none were suppressed. No old tests were weakened or deleted.
Tracked tests and protected broker/risk/ledger/replay/worker/dashboard/notification
sources still match the Phase 2C checkpoint. New fixes are isolated to market-data
backup and argument parsing.

An in-memory PowerShell here-string piped to `.\.venv\Scripts\python.exe -` ran
the following exact subprocess command forms inside a disposable OS temporary
directory; `$db`, `$backup`, `$missing` and `$id` below denote those temporary
paths and the fixture's returned dataset identifier, not persistent project data:

```powershell
.\.venv\Scripts\python.exe -m atlas.main market-data download --db $db
.\.venv\Scripts\python.exe -m atlas.main market-data inspect $id --json --db $db
.\.venv\Scripts\python.exe -m atlas.main market-data validate $id --json --db $db
.\.venv\Scripts\python.exe -m atlas.main market-data backup $backup --db $db
.\.venv\Scripts\python.exe -m atlas.main market-data inspect $id --json --db $backup
# All exit 0; original/restored inspect results and full stored datasets match.
.\.venv\Scripts\python.exe -m atlas.main market-data backup $backup --db $db
# Expected exit 1; existing backup bytes unchanged.
.\.venv\Scripts\python.exe -m atlas.main market-data validate $id --json --db
.\.venv\Scripts\python.exe -m atlas.main market-data inspect $id --json --unknown=fictional-sensitive-input
.\.venv\Scripts\python.exe -m atlas.main market-data validate --json
# Expected exit 2; exactly one INVALID_ARGUMENTS JSON document in each case.
.\.venv\Scripts\python.exe -m atlas.main market-data inspect $id --json --db $missing
# Expected exit 1; one ERROR JSON document and no created parent directory.
.\.venv\Scripts\python.exe -m atlas.main research-check
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --no-persist
# Both exit 0; reconciliation True; no research-history persistence requested.
```

Smoke checks also renamed/deleted the restored backup immediately. All temporary
databases were removed. The artificial ten-bar dataset remained
`bc97fb870943cd30be53c40bc6d472508cfbdc8cfa029f6c6d6dcd9c8067ec6d`,
origin ARTIFICIAL_OFFLINE_FIXTURE, quality WARNING. The separately labelled
synthetic replay remained GBP 1,000.00 starting capital, GBP 1,004.56 ending equity,
GBP 4.90 gross / GBP 0.34 costs / GBP 4.56 net P/L, with zero open positions and
reconciliation True. These figures are software checks, not market performance.

### Files and residual limitations

Changed for this remediation: `src/atlas/market_data/store.py`,
`src/atlas/market_data/cli.py`, new `tests/test_phase3a_final_remediation.py`,
`docs/historical_data_quality.md` and this report. Existing unrelated uncommitted
Phase 3A work was preserved. No additional production defect was established by
these focused probes; this is not an independent comprehensive re-audit.

The Windows path is exercised on this laptop; the POSIX hard-link branch has not
been runtime-verified here. Unsupported filesystem operations fail closed. Abrupt
termination can leave an orphan private staging directory. Power loss, physical
disk-full behavior, remote filesystem guarantees and hostile local filesystem
administrators are not verified. SQLite physical integrity is checked at backup;
semantic/source integrity must still be verified using inspect/validate on restore.
No fresh environment rebuild was performed or dependency version changed.

### Massive prerequisites and final assessment

Massive remains **unimplemented and unverified**. No key was read, request sent,
entitlement inferred or provider price/plan rechecked during this offline task.
A separately approved offline adapter can be designed next, but requires:

- Provider-specific documented request/response fixtures: exact numeric parsing,
  epoch/timestamp interval semantics, unadjusted/adjusted policy, pagination URL
  validation/cursor bounds and provider identity/metadata checks.
- Provider-specific shared rate budgeting and bounded retries for the eventual
  approved plan. The existing Twelve Data pacing/budget helper is not evidence
  of Massive quota compliance and must not be blindly reused.
- Explicit unknown historical availability/revision provenance, missing/no-trade
  interval policy and tests against current quality/quarantine boundaries. No
  Phase 3B consumer may silently assume original publication or complete actions.
- Separate owner approval of account/plan, licence/retention/display rights,
  instrument metadata, authorised request scope and a small real-data acceptance
  test before authenticated access. US coverage does not establish LSE minute data.

**RA-01/RA-02 remediation self-verification: PASS. Ready for independent final
offline-foundation acceptance: YES. Overall Phase 3A: PARTIAL until that review
and the separately scoped external-data acceptance.** A separate offline Massive
adapter is technically feasible after further approval; it has not started.
Phase 3B remains out of scope. Wait for the owner's next instruction.
