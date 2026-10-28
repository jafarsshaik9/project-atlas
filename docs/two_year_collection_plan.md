# Two-year historical collection plan

Planning checkpoint: `37e7e8e4e7829a22784bfd8507e93297b8257848`, branch `main`.
Planning instant: **2026-10-28 12:00 UTC** (08:00 America/New_York).
This is an explicit reproducible planning assumption, not an authenticated entitlement test.

## Scope and eligible window

Target: AAPL, MSFT, NVDA, AMZN and GOOGL; original one-minute USD OHLCV;
regular cash sessions only; **UNADJUSTED**, with `adjusted=false` explicitly sent
and verified against each response. Split-adjusted and dividend-adjusted histories
are not mixed with raw prices. No extended-hours bars, interpolation, synthetic
gap filling, broker data or subscription upgrades are included.

Massive documents two years of Basic aggregate history and end-of-day recency.
Its Basic FAQ says a trading day's bars become accessible after midnight Eastern,
not immediately after the close. On the frozen planning date, the nominal earliest
date is **2024-10-28**, and the latest completed eligible session is **2026-10-27**.
The exact rolling cutoff's sub-day/inclusive behavior, actual account entitlement,
oldest-day response and provider service availability have **not** been authenticated.
The worker rechecks the calendar-date retention window before dispatch and fails
closed on authorization or entitlement errors. Oldest sessions are requested first.

Public sources checked on 2026-10-28:

- [Custom bars: Basic history, recency, adjustment, paging and timestamp fields](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
- [Basic previous-day availability](https://massive.com/knowledge-base/article/on-the-free-basic-plan-when-can-i-pull-the-previous-trading-days-data)
- [Dated ticker identity and reference coverage](https://massive.com/docs/rest/stocks/tickers/ticker-overview)
- [Published request limits](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis)
- [Market-data usage terms](https://massive.com/legal/market-data-terms-of-service)

Basic's advertised allowance is five REST requests/minute; Atlas remains more
conservative at **four total HTTP attempts per rolling 60 seconds**, including
retries, metadata and pagination. No actual request was made for this expansion.

## Calendar and manifest

The existing maintained, versioned calendar yields **501 sessions per symbol**:
496 full sessions of 390 minutes and five short sessions of 210 minutes.
That is **194,490 minute slots per symbol**, **972,450 total**, across 2,505
symbol/session partitions. These are expected calendar slots, not a claim that
Massive necessarily reports a qualifying trade in every minute. Missing source
intervals remain missing and are quarantined under the existing strict quality policy.

All five planned listings use XNAS's existing XNYS schedule alias. Actual daily
listing venue, currency, active common-stock type and composite FIGI must be proved
by dated reference evidence. A venue mismatch stops collection for reviewed replanning.
Calendar materialization is chunked within the existing bound, then each exact daily
schedule, timezone version and exceptional-closure limitations are frozen in the manifest.

Full machine-readable dry-run manifest:

`local/research/two-year/dry-run-manifest.json`

Manifest ID:

`fa237778624dc82d0b53ac65ea9fc10ccc48448e82511511aea385c90c5fdc29`

It is approximately 4.62 MB and is intentionally ignored by Git. It enumerates every
initial proposed method/path/parameter set, symbol/session checkpoint identity,
UTC open/close, expected minutes, adjustment policy, daily calendar proof and reuse
reference. It contains no key or authenticated URL. The frozen file is created
exclusively; it is not overwritten when replanning.

| Symbol | Sessions | Expected minute slots | Reusable partitions | New initial requests |
| --- | ---: | ---: | ---: | ---: |
| AAPL | 501 | 194,490 | 1 | 1,000 |
| MSFT | 501 | 194,490 | 0 | 1,002 |
| NVDA | 501 | 194,490 | 0 | 1,002 |
| AMZN | 501 | 194,490 | 0 | 1,002 |
| GOOGL | 501 | 194,490 | 0 | 1,002 |
| Total | 2,505 | 972,450 | 1 | **5,008** |

Each uncached partition proposes:

1. `GET /v3/reference/tickers/{symbol}?date={session-date}`.
2. `GET /v2/aggs/ticker/{symbol}/range/1/minute/{open-ms}/{close-ms-minus-one}`
   with `adjusted=false`, `sort=asc`, `limit=5000`.

The existing adapter accepts one local date per request and daily effective symbol
identity. Month-sized requests or fabricated multi-day mappings would bypass those
guards and are deliberately not introduced. The public endpoint permits up to
50,000 base aggregates, but Atlas retains its reviewed **5,000** limit and 64-page
safety bound. A regular session has at most 390 expected source rows, so one aggregate
page is the baseline estimate.

Any provider-required continuation is conditional: its opaque cursor is unknowable
before the preceding response. The manifest explicitly declares this bounded request
class. The existing adapter allows only validated same-origin/same-path continuations,
checks policy/cursor privacy and loops, and reconstructs safe requests. Each page and
retry consumes the same shared quota. Do not interpret 5,008 as a guaranteed final
HTTP-attempt count or treat arbitrary unlisted endpoints as approved.

## Existing local evidence

Read-only inspection verified:

- `local/market-data/massive-pilot/historical.sqlite3`: one accepted AAPL partition,
  2026-10-26, 390 unadjusted regular-session bars, quality WARNING.
- Dataset ID `fdad08c5f11ca28385b43d19291676c3963687c0ac210e71d03061df69f64cb3`.
- One completed historical import, one saved page; no interrupted pilot job to resume.
- `local/market-data/massive-quota.sqlite3`: two recorded completed HTTP attempts
  from the earlier authorized pilot, no uncertain in-flight token. No quota mutation
  or recovery/reset was performed by planning.
- Original publication availability and corporate-action coverage remain UNKNOWN.
  Reuse does not promote the partition to VERIFIED research evidence.

The reusable partition avoids two initial calls and 390 source rows of recollection.
It is referenced read-only, not republished, edited or copied into a mutable dataset.
Every reuse is re-inspected for manifest, source and bar integrity. New action evidence
requires a separately validated import rather than silently attaching it to old data.

## Request, duration and disk estimates

Baseline: **5,008** new initial requests, versus 5,010 without reuse.
The existing limiter spaces attempts by at least 16 seconds after completion, with
an additional conservative rolling-window check and persistent Retry-After state.

- Spacing-only lower-bound estimate: `(5008 - 1) * 16 = 80,112 seconds`,
  **22 hours 15 minutes 12 seconds**.
- Budget roughly **23–26 hours** for an uninterrupted low-latency pass with modest
  overhead; this is an engineering estimate, not a provider SLA.
- Three attempts per initial request would produce 15,024 attempts and roughly
  **66.8 hours of spacing**. This is a one-pass retry scenario, not a lifetime campaign
  cap. Extra pages, resumed uncertain responses, rate-limit backoff and manual safety
  reviews can add time or stop the run indefinitely.
- The actual pilot database uses 413,696 bytes for 390 bars. Linear scaling gives
  approximately **1.03 GB** for the target slots. Expect roughly **1–2 GB active**,
  with **3–4 GB free** recommended for metadata, staging/quarantine, journals and a
  backup. Compression, response sizes and SQLite overhead make this approximate.

No two-year data transfer or full-scale performance benchmark has occurred.

## Resumable workflow and safeguards

`atlas.research_collection` uses the existing `MassiveProvider`, `MassiveQuota`,
`download`, `HistoricalStore`, calendar and quality assessment. It does not replace
or weaken their validation. `CollectionState` records each symbol/session's definition,
dated identity, request, job ID, dataset ID, status and sanitized failure category.
Checkpoints also bind the provider descriptor/approval reference and target historical
store; resume under changed configuration is refused before any new HTTP attempt.

An OS-owned file lock on the single canonical coordinator permits one active real
collection worker. A paused live process retains the lock; process death releases it.
The existing quota independently permits only one in-flight HTTP attempt across
processes. Real collection is refused with an alternate quota/coordinator path.
Only mocked HTTP tests can use disposable paths without authorization.

Saved raw pages resume through the existing historical cursor/checksum chain. A crash
after immutable dataset publication but before the collection checkpoint recovers the
existing completed import without refetching bars. Accepted and quarantined partitions
are revalidated and not automatically refreshed. Failed source normalization retains
sanitized failure/checkpoint evidence without publishing invalid accepted data.

An uncertain in-flight quota token or monotonic rollback does **not** expire into
permission for another HTTP request. Restart stops pending operator review; do not
delete/reset/switch quota files to get around it. Historical job lease checks remain
mandatory. Safe recovery may require waiting for a dead worker's historical lease;
there is no forced takeover of a live process.

Dry-run command (use a new output filename if it already exists):

```powershell
.venv/Scripts/python.exe -m atlas.main research-history plan --as-of 2026-10-28T12:00:00Z --store local/market-data/massive-pilot/historical.sqlite3 --output local/research/two-year/dry-run-manifest.json --json
```

There is deliberately **no network collection CLI switch** in this change. The tested
programmatic `collect` workflow accepts a frozen manifest and existing adapter; a real
transport additionally requires a separate authorization reference and the canonical
shared coordinator/quota. No such authorization was supplied or exercised.

## Quality, provenance and corporate actions

Required: validated daily instrument/FIGI mapping, USD units, regular-session calendar,
ordered unique one-minute intervals, valid OHLC relationships, nonnegative finite
volume, explicit raw adjustment response, complete safe pagination, source checksums,
wire hashes, ingestion timestamp and immutable normalization/calendar manifests.
Unknown original publication time remains UNKNOWN; download time is never substituted.
Missing/duplicate/out-of-session/malformed bars fail the existing quality policy.

The collector accepts an explicitly reviewed public `CorporateAction` JSON pack,
fingerprints it and passes matching split/dividend/ticker/delisting evidence through
the existing immutable import boundary. **It does not fetch or certify complete action
history**: the existing adapter's endpoint/quota allowlist contains only reference and
minute aggregates, and this implementation does not silently add new endpoints.
Action coverage therefore remains UNKNOWN even when partial evidence is supplied.
Obtaining a complete dated action inventory requires a reviewed source/endpoint plan
and separate approval; those extra requests are not included in the baseline manifest.

Research is intraday-flat, so raw prices are not used to carry unconverted shares
across a split, dividend or identity change. Known intraday split/ticker/delisting
events, and date-only structural-action days, require review and are refused. No
dividend total-return performance, historically complete universe or action coverage
is claimed. The five chosen present-day symbols introduce selection/survivorship bias.

## Permissions and current status

Only local implementation, mocked testing and dry-run planning were authorized.
Before real collection: separately approve the frozen request manifest and collection
duration, verify the Basic account's actual history/endpoint access, confirm permitted
non-display historical research usage under applicable provider/exchange terms,
approve any action-data acquisition, and authorize credential use through the existing
lazy loader. User approval of a run does not itself establish provider licensing.

No authenticated Massive calls, credential/.env reads, Trading 212 calls, orders,
Telegram messages, subscription changes, commits or pushes occurred in this expansion.
