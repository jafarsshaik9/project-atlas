# Alpaca full historical collection readiness

Engineering preparation only. Account-specific market-data usage permissions are
**UNVERIFIED**. No authenticated provider requests or collection execution are
authorized by this document. No permission-confirmation file has been created.

## Commands

Run from `D:/Jafar's Work/project-atlas`. Relative runtime paths resolve against
the installed repository root, independently of the terminal working directory.

Dry run (safe default; repeatable, no credentials or provider activation):

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --manifest local/research/two-year/alpaca-dry-run-manifest.json --run-plan local/research/two-year/alpaca-run-plan.json
```

Future execution, **only after separate account-permission confirmation and user
approval to execute**:

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --manifest local/research/two-year/alpaca-dry-run-manifest.json --run-plan local/research/two-year/alpaca-run-plan.json --permission-record local/research/two-year/alpaca-account-permissions.json --execute
```

Future resume, subject to the same permission gate:

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --manifest local/research/two-year/alpaca-dry-run-manifest.json --run-plan local/research/two-year/alpaca-run-plan.json --permission-record local/research/two-year/alpaca-account-permissions.json --execute --resume
```

`--resume` alone remains a dry run. A first execution cannot silently resume an
existing collection state; `--resume` is required. Resume without existing state
is rejected. The frozen manifest and run plan must remain identical across runs.
There is no permission-approval flag or automatic record-generation command.

## Separate account confirmation gate

The permission record must be created only after the user separately confirms
that applicable account/data-use permissions have been checked. Successful API
authentication and published general documentation do not establish those rights.
Until then, execution stops with `ACCOUNT_PERMISSIONS_UNVERIFIED`, before provider
construction or credential loading. This task leaves that record absent.

The bounded JSON record has exactly these fields:

| Field | Requirement |
|---|---|
| `schema` | Integer 1 |
| `repository` | Exact resolved repository root |
| `manifest_id` | Original frozen manifest ID |
| `run_id` | Exact reviewed run plan ID |
| `feed`, `adjustment` | `sip`, `raw` |
| `account_permissions` | `USER_CONFIRMED_CHECKED`, only after separate confirmation |
| `confirmation` | `I separately confirm applicable account permissions for automated historical research.` |
| `confirmed_at` | Explicit timezone-aware confirmation time; not future and at most 30 days old |
| `evidence_reference` | Nonempty reference identifying the checked permission basis; no credentials |

This is a durable user attestation, not independent verification by Atlas or
Alpaca. A mismatched, stale, incomplete or unconfirmed record fails closed. No
tests or examples install a valid record in the real repository. Test records
use disposable synthetic repositories only.

## Frozen plan and reuse

Original manifest:
`50c303bcf9e4cb3fbbc14bce1718d839e3479430798184bb6f3ee9a590e624dd`.
It remains unchanged. The separate run plan records its ID, repository, target
store/cache/coordinator paths and accepted source references, then hashes those
bindings. Existing plans are checked rather than overwritten.

The dry run detected AAPL 27 October 2026, **390 bars**, dataset:
`31059db0b3252dbd15168065aaae011ac75364ba2fee21cb2ead5b964b1d5f3a`.
Its accepted manifest, checksums, provider descriptor, exact raw USD request,
instrument identity and calendar agree with the full manifest. The pilot store
remains read-only. A `REUSED` partition points to that immutable dataset; it is
not copied, relabelled or overwritten. Consumers can use the returned store and
dataset ID in existing research bundles.

The default source is `local/market-data/alpaca-pilot/historical.sqlite3` when
present. Additional sources can be selected with repeatable `--reuse-store` only
while creating a new dry-run plan. Sources must contain accepted, compatible
Alpaca partitions. Other providers, ambiguous duplicates and incompatible metadata
are rejected. Source changes after planning invalidate the plan.

Runtime files, all ignored by Git:

- `local/market-data/alpaca-full/historical.sqlite3`: existing immutable historical store.
- `local/market-data/alpaca-full/pages.sqlite3`: durable shared multi-symbol pages.
- `local/market-data/alpaca-full/collection.sqlite3`: existing CollectionState coordinator.
- `local/market-data/alpaca-quota.sqlite3`: unchanged canonical account-wide limiter.
- `local/market-data/alpaca-collection.lock`: single supported collection worker.

Runtime databases cannot alias one another, source stores or the quota database.
Foreign cache/coordinator/store schemas fail closed. All runtime paths stay inside
repository `local/`; repository markers and the package project name are checked.

## Estimates and preflight

| Item | Current plan |
|---|---:|
| Period | 28 October 2024 through 27 October 2026 |
| Universe | AAPL, MSFT, NVDA, AMZN, GOOGL |
| Sessions per symbol | 501 |
| Total partitions | 2,505 |
| Target bars | 972,450 |
| Accepted bars reused | 390 |
| New normalized bars needed | 972,060 |
| Initial expected HTTP requests | 501 |
| Normal full-session batch | 1,950 bars |
| Early-close batch | 1,050 bars |
| Expected disk usage | Approximately 0.78–1.75 GB, plus transient WAL/overhead |

The AAPL reuse does not reduce the expected HTTP count: the other four symbols
still need that date's batch. A fetched multi-symbol page can include AAPL rows
that are not imported because its accepted partition is reused. Entire sessions
already covered by accepted sources need no HTTP.

**501 is an estimate, not a request cap or completeness guarantee.** Alpaca may
return short or empty pages with a continuation, and symbols need not appear on
every page. Pagination and retries add attempts. The requested response limit is
10,000 total bars across symbols; oversized responses are rejected. Existing
64-page-per-partition recovery bounds remain in force and stop excessive chains.

Network time is estimated at **5–20 minutes**, with about 175 seconds of baseline
spacing. End-to-end normalization, publication and reinspection time remains
unbenchmarked; there is no guaranteed overall duration. Live ETA becomes available
after completed partitions and remains approximate, especially after resume.

Preflight checks frozen calendars and requests, the historical 15-minute gate,
source compatibility, target-provider separation, worker ownership and quota
integrity. Free disk must cover twice the high storage estimate plus 1 GB:
approximately **4.50 GB** for this plan. The last local check had approximately
124 GiB free and no holder of the collection lock. Checks repeat at execution.

## Quota, progress and recovery

Only fixed `GET https://data.alpaca.markets/v2/stocks/bars` requests are available:
five symbols, `1Min`, `sip`, `raw`, USD, ascending, explicit regular-session UTC
boundaries. Inclusive API end is close minus one microsecond. There are no broker,
account, real-time, order or IEX fallback endpoints in this command.

The existing account-wide persistent limiter reserves every attempt before HTTP:
180 attempts per 61 seconds, 0.35-second completion spacing, shared with other
Atlas Alpaca workers. External clients using the same account are outside Atlas's
limiter. Page retries are bounded to three attempts. HTTP 401/403, malformed
responses, quota corruption, clock regression and uncertain in-flight requests stop.

HTTP 429 supports numeric and HTTP-date `Retry-After`, without shortening a valid
server delay. Past HTTP dates imply zero remaining delay. Missing headers use 60
seconds; malformed headers stop with a conservative persisted cooldown. Delays
over 300 seconds are committed and stop the worker for later resume, rather than
holding the terminal or resetting the limiter. Restart before a long cooldown has
elapsed remains blocked. Transport failures are not automatically retried.

JSON terminal progress reports completed/remaining sessions, fully completed
symbols, completed partitions, actual source bars downloaded, accepted/reused and
quarantined partitions, reserved HTTP attempts, automatic retries, cumulative
active elapsed time and approximate remaining time. Account lifetime attempts
are reported separately. Reused rows are not counted as new downloaded bars.
Quota waits refresh progress through heartbeats. No headers or credential values
are printed. Counters recover from the coordinator and checksum-verified cache.

Per-symbol/session checkpoints use the existing coordinator. The historical store
retains authoritative raw-page checkpoints and deterministic import job identities.
A crash after a page commit reuses that page. A crash after dataset publication
but before coordinator acknowledgement finds the same completed historical job;
it cannot create a duplicate accepted dataset. Source references are revalidated
on resume. A process death releases the OS lock; unexpired historical job leases
can require up to 90 seconds before takeover. Uncertain HTTP reservations require
review, not automatic deletion or budget reset.

Ctrl+C retains checkpoints and returns an interruption result. Quarantine stops
the run; a resume does not silently skip or repair the quarantined partition.
Missing, duplicate, malformed, out-of-session or incomplete rows are never filled
or interpolated. Raw-price corporate-action warnings and UNKNOWN publication
availability remain unchanged; VERIFIED backtests cannot treat these as observed
point-in-time availability.

## Offline verification

Tests cover all five symbols on uneven/empty pages, continuation cycles, missing
symbols, partial responses, duplicates, response limits, partial-pagination resume,
HTTP 401/403 and 429, numeric/date/long delays, read-only reuse, checkpoint recovery
after dataset commit, provider separation, worker exclusion, disk checks and the
account-confirmation gate. CLI smoke checks use dry-run only.

Measured verification: **34 new tests**, **68 focused tests passed**, **1,205 total
tests passed** in the complete suite. Ruff lint, Ruff formatting and `pip check`
passed. The real dry-run CLI confirmed the pilot reference above and the worker
lock. The canonical provider ledger remains at its original **one** HTTP attempt,
with no uncertain request. The pilot manifest checksum remains
`be327cbb3867295cf3efc772eda1e5b2931156f389e0b4f6e0dbc068a95d3fcf`.
No permission record or full-collection state database exists. No collection,
authenticated API requests, commit or push were performed during this readiness task.
