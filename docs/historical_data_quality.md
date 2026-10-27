# Historical data contracts, quality and storage

Historical data is not synthetic performance, an order, a fill or live broker data.
`atlas.market_data` never imports the ledger, research history or Telegram workers.
The existing GBP 1,000 capital and trading logic are unchanged.

## Identity, units and point-in-time rules

Every request carries a canonical instrument, MIC, trading currency, quote unit,
exchange/instrument timezones and effective-dated provider symbol mapping with
metadata evidence. Venue/ticker never supplies currency implicitly. Multiple
listings can share a symbol. GBX 250 = GBP 2.50 via existing Decimal conversion;
USD remains USD. No FX rate or GBP valuation is invented in this data phase.

Bars use Decimal prices/volume, positive finite OHLC, nonnegative finite volume,
valid OHLC bounds, UTC-aware minute-aligned `[start,end)` intervals and event time
at interval end. Provider original timestamp/convention, ingestion timestamp,
unit/currency, adjustment, completion, quality and dataset version are explicit.
No missing field becomes zero. A future-ending complete bar is invalid.
For historical Twelve Data rows, completion means the interval has elapsed at
ingestion, not vendor-certified finality. Revisions/finalization remain unverified;
UNKNOWN availability prevents automatic strict replay use. `valid_bars` counts
contract-normalized rows; a retained incomplete row still makes quality FAIL.

Original source availability is KNOWN only with actual evidence (the fixture's
known timestamps are explicitly fictional). Historical Twelve Data availability
is UNKNOWN, never replaced with ingestion time. Strict `visible_bars` denies
UNKNOWN and all quarantined datasets. A request can explicitly choose a
bar-end-plus-N-second assumption (0..3600), producing ASSUMED provenance and a
different version. Consumers must also explicitly allow assumed availability;
they cannot select the bar before that boundary. Revised historical data and
future adjustment knowledge are not cured by an assumed delay. No Phase 3B replay
adapter exists, so old `MarketBar` received-time semantics are not silently reused.

Corporate-action metadata accommodates sourced split/dividend/ticker-change/
delisting identifiers and UTC timestamps or **date-only precision**. Unknown
availability remains unknown; dates do not become invented midnight events.
The adapter fetches unadjusted prices only. No action adjustment, FX conversion,
delisted universe or survivorship correction is performed. Future research must
establish point-in-time universe membership and revisions separately.

## Calendar policy

Maintained `exchange_calendars==4.13.2` and `tzdata==2026.5` are the verified
environment. XLON uses XLON; XNYS uses XNYS; XNAS uses the library's shared XNYS
US-cash schedule, explicitly recorded as an alias. Regular cash sessions only:
no auction, extended-hours or halt calendar. Supported dates are 2000..2030,
with <=368-day materializations (padding a <=366-day UTC request). Out-of-range
dates fail rather than silently assume weekdays. Future closures and exceptional
closure completeness are not guaranteed by community calendar rules.

Provenance includes library/tzdata versions, venue, timezone, interval policy and
every effective session/holiday, UTC offset and half-day boundary. Revalidation
uses the saved schedule, preserving historical results across library changes.
Tests include March/October UK-US DST mismatch, Christmas, US Independence Day,
Black Friday/Christmas Eve early closes, LSE 2022-09-19, US Sandy and Carter closure.
[Calendar source/licensing](https://github.com/gerrymanoim/exchange_calendars),
[package metadata](https://pypi.org/project/exchange_calendars/).

## Quality outcomes

| Status | Examples | Store / research policy |
| --- | --- | --- |
| FAIL | Invalid/missing prices or volume, OHLC, timestamps, duplicates, missing expected minutes, outside session/request, incomplete bars, currency/MIC/symbol/timezone mismatch, mixed adjustment, wrong action identity, source-order violation | Preserve available sanitized raw pages and findings; publish QUARANTINED, never selectable |
| WARNING | Unknown/assumed availability, explicitly partial-session request, no bars on a closed-session window, >30% price discontinuity, incomplete actions, unassessed survivorship | Store ACCEPTED with warnings; this does **not** establish strategy suitability; strict availability still enforced |
| PASS | No findings under the current policy | Supported by report schema; current datasets always retain an unassessed-survivorship warning |

Every expected regular-session minute must be present in the requested window.
This strict policy may quarantine legitimate illiquid/no-trade minutes or halts;
it deliberately does not invent bars. Partial full-session coverage is measured
against the requested portion and separately warned. A large discontinuity is
only a warning, not proof of a split or a repaired price. Reports have code,
severity, count and explanation in both JSON and human-readable form.

## Persistence, recovery and reproducibility

Default: ignored `local/market-data/historical.sqlite3`, separate from
`local/atlas-history.sqlite3`. SQLite schema v1 stores jobs, compressed sanitized
raw pages, immutable manifests, quality reports, normalized rows and rate budgets.
A foreign research database or unknown schema is refused. Initial creation is the
v1 migration; no fictional future-version migration is promised.

Each page/checkpoint is a short `BEGIN IMMEDIATE` transaction. Jobs have unique
ownership identities and 90-second leases; heartbeat/checkpoint/publication
validate ownership in the mutation transaction. Expired workers cannot publish
or interrupt replacements. No network waits occur inside write transactions.
Partial jobs publish no dataset. Re-invoking the same request resumes checkpoints;
process death requires lease expiry. Completed duplicate imports use existing data;
`--refresh` creates a new fetch job, not a second portfolio or automatic version.

Atomic publication inserts manifest, quality and all rows and completes the job
in one transaction. A failed insert rolls everything back. SHA-256 fingerprints
cover canonical request, explicit assumptions, descriptor, effective calendar,
normalized content, sanitized raw-page bodies, quality policy/findings and action
metadata. Operational ingestion/fetch times do not change content identity, but
the first actual ingestion times are retained. Raw wire hashes and page checksums
remain audit metadata. Pagination/content differences may create distinct versions.
Changing action metadata or coverage cannot reuse an incompatible cached job.

Provider responses are allowlisted and known-key redacted **before** persistence;
raw request/response headers, error bodies, credentials and account fields are not
stored. Consequently the saved raw page is a sanitized source representation,
not an exact wire archive. Original bytes have a wire checksum only. No source
price/volume is interpolated or corrected. Manifest transformations state this.
Reload verifies manifest, report, bar and dataset checksums; `validate` also checks
raw checkpoints and reruns normalization/quality against the recorded calendar.
Checksums detect corruption, not source truth or maliciously rewritten provenance.

Coverage queries are paged (1..100, offset <=100000); page size <=5000, page JSON
<=8 MB, <=64 pages, <=200000 raw rows. SQLite/JSON+zlib avoids a new columnar stack
for small laptop datasets; larger universe/bulk workloads need later benchmarking.

## Safe backup and reset

Use SQLite's online backup, not a copy of a live `.sqlite3` without its WAL:

```powershell
.\.venv\Scripts\python.exe -m atlas.main market-data backup local/backups/historical-2026-10-27.sqlite3
```

The destination must be a new private path. Each operation builds a private
staging snapshot beside the destination, closes its SQLite connections, and checks
schema/SQLite integrity before publication. Windows uses a no-replace rename;
other platforms use exclusive hard-link creation. Competing publishers cannot
overwrite each other, and failure cleanup removes only the operation's staging
directory. Filesystems without the required atomic operation fail closed. An
abrupt process termination can leave an orphan staging directory, but does not
publish an incomplete final backup. Actual power-loss durability is not verified.

Stop import processes before restoring; verify dataset checksums with
`inspect`/`validate` afterwards. SQLite integrity alone does not establish source
truth or validate every dataset's semantic provenance. Keep provider retention and
redistribution rights in mind. For a fresh test history choose a new `--db` under
`local/`; this safely leaves source and Phase 2C history untouched. To remove old
history, stop importers/readers, back it up and manually delete only the explicitly
identified historical DB and its WAL/SHM files. No automatic deletion is provided.

## Remediated acceptance rules

Provider mappings for the same provider must have disjoint effective half-open
intervals. Adjacent intervals are allowed; a request crossing a symbol transition
is rejected and must be explicitly partitioned. GBP, GBX and USD bindings are
verified against the job, durable source normalization and manifest on write/read.

Quality comparisons use exact integer ratios with a bounded numeric policy:
at most 64 coefficient digits and absolute Decimal exponent <=64, for prices
and volume. Larger values are quarantined without repair. The 30% discontinuity
test is exact, including its strict inequality. Decimal JSON exponents use stable
uppercase notation; fixture arithmetic uses an isolated 64-digit ROUND_HALF_EVEN
context. Caller precision, rounding and exponent-case settings do not change
reports or dataset identities. This policy is included in new fingerprints.

New publication requires fully bound job context and durable, ordered, terminal
source checkpoints. Bars/report are independently rederived outside the write
transaction. Ownership and source snapshots are rechecked atomically on commit.
Inspect/validate verify source checksums, recorded policy, request identity and
rederived results; a self-consistent hash alone is insufficient. A corrupt cached
version cannot be silently reused to complete a refresh. Quarantine remains a
valid diagnostic outcome for fully downloaded bad source data.

Coverage does not scan all normalized/source rows: each item explicitly reports
`integrity: NOT_VERIFIED` and explains its metadata-only meaning. Its ACCEPTED
state records publication-time quality, not proof of current physical integrity.
Use inspect/validate for verification; those commands fail on corruption.

Coverage/inspect/validate refuse missing databases without creating parent paths,
initializing schemas or migrating existing files. Explicit `--json` for
inspect/validate produces exactly one stdout JSON document on success or failure,
including validation FAIL, operational errors and malformed command-line syntax.
Success and WARNING exit 0; validation FAIL and operational errors exit 1; syntax
errors exit 2 with `{"status":"ERROR","error":"INVALID_ARGUMENTS"}`. Diagnostics
go to stderr. Syntax errors withhold raw argument values from both outputs.

JSON mode is selected by the supported subcommand's option token, not by text
inside another option's value (`--db=--json`) or a positional value after `--`.
Other subcommands do not gain JSON support. Ordinary non-JSON argument errors
retain argparse's usage text/exit 2; explicit `--help` remains human-readable.

To resume an interrupted refresh, repeat its exact original request arguments and
database, replacing `--refresh` with `--resume-job <reported-job-sha256>`:

```powershell
.\.venv\Scripts\python.exe -m atlas.main market-data download --resume-job $jobId --db local/market-data/historical.sqlite3
```

The job ID is also available from the local Python store's job records. An
interrupted command reports it on stderr. Config-file requests require the same
`--config` and provider flags; approved external requests still require separate
approval, local credentials and `--allow-network`. Resuming never relaxes access
checks. Completed compatible resumption is idempotent; policy/code changes can
require a fresh import. Existing schemas/manifests are not automatically rewritten.
