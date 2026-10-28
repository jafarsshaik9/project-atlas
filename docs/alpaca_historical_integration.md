# Alpaca historical SIP integration

Alpaca is the preferred historical planner. Massive remains available with
`research-history plan --provider massive`; it is never an automatic fallback.
Existing strategies, risk rules, accounting, dashboard and notification paths are unchanged.

## Public documentation and eligibility

Reviewed 28 October 2026:

- [Alpaca market-data subscriptions](https://docs.alpaca.markets/us/docs/about-market-data-api):
  Basic is free, provides history since 2016, excludes the latest 15 minutes,
  and allows 200 historical calls/minute. Real-time Basic equities coverage is IEX;
  historical SIP success does not establish real-time SIP entitlement.
- [Historical bars contract](https://docs.alpaca.markets/us/reference/stockbars):
  `GET https://data.alpaca.markets/v2/stocks/bars`, two authentication headers,
  symbol-first ordering and opaque pagination. The 10,000-bar limit applies across
  all symbols. Even short pages can have continuations.

Atlas pins `feed=sip`, `timeframe=1Min`, `adjustment=raw`, `sort=asc`,
`limit=10000`, `currency=USD`, and `asof=-` (disable automatic historical renaming).
Each request explicitly spans one exchange regular session. Alpaca's inclusive
end is the exchange close minus one microsecond; Atlas retains half-open intervals.
No account API, paper endpoint, order endpoint, IEX fallback or subscription change is used.
The existing `ALPACA_ENDPOINT` is neither read nor changed.

## Dry-run collection manifest

Generated without credentials or network using:

```powershell
.venv/Scripts/python.exe -m atlas.main research-history plan --as-of 2026-10-28T12:00:00+00:00 --first 2024-10-28 --last 2026-10-27 --output local/research/two-year/alpaca-dry-run-manifest.json --json
```

The ignored local manifest lists each proposed URL, query, exchange-calendar proof
and symbol/session checkpoint. It is an estimate, not collection authorization.

| Measure | Alpaca SIP daily batches | Previous Massive plan |
|---|---:|---:|
| Symbols | AAPL, MSFT, NVDA, AMZN, GOOGL | Same |
| Sessions per symbol | 501 | 501 |
| Expected bars total | 972,450 | 972,450 |
| Initial HTTP requests | 501 | 5,008 |
| Network duration estimate | 5–20 minutes | 23–26 hours |

Manifest ID: `50c303bcf9e4cb3fbbc14bce1718d839e3479430798184bb6f3ee9a590e624dd`.
There are 496 full sessions (1,950 bars per five-symbol batch) and five early
closes (1,050 bars per batch). Each batch should fit one page, but pagination and
retries can increase the request count. Spacing alone requires about 175 seconds;
the network estimate assumes ordinary request latency. Normalization, immutable
publication and reinspection add unbenchmarked time. Expected local storage is
approximately 0.78–1.75 GB including source evidence and normalized records;
SQLite/WAL/index overhead and provider revisions may change this estimate.

## Reuse and recovery boundaries

- Separate persistent Alpaca quota: `local/market-data/alpaca-quota.sqlite3`.
  Every HTTP attempt is reserved transactionally before transmission, including
  retries and continuation pages. Atlas uses a conservative 180 attempts/61 seconds
  and 0.35-second completion spacing. Workers share a single in-flight reservation.
  Unfinished reservations, clock regression or inconsistent history stop collection.
  Other applications using the same account are outside Atlas's limiter.
- The connection pilot has a persistent two-attempt lifetime budget. It cannot
  be reset by restarting the worker. Authentication/access errors are terminal.
  429 and server responses have persistent backoff; transport failures are not
  automatically retried. Requests have timeouts, a 30-second body deadline and
  an 8 MB response bound. Redirects and environment HTTP proxies are disabled.
- Daily native multi-symbol pages are allowlisted and checksummed in a durable
  SQLite cache. Each symbol is projected separately, including empty projections,
  and every continuation is followed. Missing symbols are not assumed complete.
- Existing `download`, historical job leases/raw checkpoints, calendar validator,
  immutable publication, quarantine and checksum reinspection remain authoritative.
  A crash after batch-page commit but before a symbol checkpoint reuses the cached
  page without HTTP. A crash during a request leaves a reservation requiring review.
  Per-symbol/session request identities resume jobs and reuse accepted datasets.
  One OS-locked collection worker runs initially; takeover obeys existing job leases.
- `alpaca-sip` has distinct instrument IDs and provider provenance. No Massive
  datasets are modified or reused as Alpaca data. Multi-session runs reject mixed
  providers before evaluation.

## Quality and research limits

Raw OHLCV values are parsed as exact decimals; volume must be integral. UTC RFC3339
timestamps are source bar starts. No bars or publication timestamps are fabricated.
Missing, duplicated, unordered, invalid or out-of-session bars are quarantined,
without interpolation. Source-wire hashes and per-symbol projection evidence are retained.

Historical listing metadata is explicitly declared research metadata, not a dated
reference verification. Corporate-action coverage remains UNKNOWN; raw prices
avoid silent retrospective adjustment but do not supply dividends, splits or
delisting/rename evidence. These limits are visible quality warnings. Backtests
remain session-scoped with no overnight holdings; total-return claims require
separate corporate-action evidence. The fixed present-day universe has survivorship bias.

Publication availability remains UNKNOWN. VERIFIED replay exposes no such bars;
explicitly labelled ASSUMED exploratory research uses the existing replay and FX
assumptions. USD prices are not GBP; existing backtesting requires explicit FX and
cost policies. No point-in-time verification is claimed from a successful download.

The full collection command is now implemented, with dry-run as the default.
Execution remains blocked until separately confirmed account permissions are
recorded and the user explicitly approves execution. See
[full collection readiness](alpaca_full_collection_readiness.md) for exact commands,
immutable reuse, checkpoint recovery and the independent permission gate. No
subscription purchase or redistribution permission is inferred.

## Validation and pilot

Offline coverage includes headers without disclosure, sparse multi-symbol pages,
continuations, shared durable quotas, 429/server backoff, a persistent two-attempt
cap, interrupted pagination, cached-page recovery, duplicate prevention, source
tampering, raw-price policy, calendars, quarantine, provider separation and
VERIFIED/ASSUMED backtest compatibility.

Final verification: **1,165 tests passed** (28 new), with Ruff lint, Ruff formatting
and `pip check` passing. No existing tests were removed or weakened; the existing
Massive CLI test now selects its provider explicitly.

The initial credential-blocked attempt sent no HTTP requests. On the subsequent
user-authorized retry, both named credentials were available in the saved root
`.env`. The loader follows the existing lazy, allowlisted credential convention:
process assignments take precedence, including explicit empty values; fallback
uses the repository root rather than the working directory. It does not populate
global environment variables or read account settings. Duplicate assignments and
oversized/unreadable credential files fail closed. Values are never reported.

Targeted follow-up verification: **34 environment-loading/adapter tests passed**,
including six new environment cases. Targeted Ruff lint and formatting passed.
The preceding 1,165-test full-suite result above predates these six added cases;
the full suite was not rerun for that focused follow-up. At the subsequent Git
checkpoint review, the complete suite passed **1,171 tests**, with Ruff lint,
formatting and `pip check` passing. Runtime data and credentials remain excluded.

The AAPL **27 October 2026** pilot ran from outside the repository and succeeded:
**one authenticated historical GET**, **HTTP 200**, **390 bars**, **ACCEPTED**.
The first/last UTC starts were 13:30 and 19:59, matching the regular exchange
session. All 390 starts were unique and volumes integral. Quality is WARNING:
publication availability UNKNOWN, listing metadata declared, corporate-action
coverage incomplete and survivorship unassessed. This is observed historical
SIP access for this request, not a real-time SIP entitlement claim.

Dataset ID: `31059db0b3252dbd15168065aaae011ac75364ba2fee21cb2ead5b964b1d5f3a`.
The separate store is `local/market-data/alpaca-pilot/historical.sqlite3`.
A cached restart returned the same dataset without another HTTP attempt, and a
fresh read-only inspection verified its persisted source and normalized records.
The canonical quota retains pilot `atlas-alpaca-aapl-20261027-v1` with one used
attempt, one remaining and no uncertain in-flight reservation. It was not reset.
Report: `local/research/two-year/alpaca-pilot-report.json` (ignored runtime file).
The existing Massive AAPL database's SHA-256 was unchanged.

No credential/authentication blocker remains for this pilot. Full collection
still requires separate account-permission confirmation and execution approval.
No two-year download, account/broker call, order, commit or push occurred.
