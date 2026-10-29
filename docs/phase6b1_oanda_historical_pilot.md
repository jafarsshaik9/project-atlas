# Phase 6B-1 — OANDA historical collection readiness

Subsequent collection and audit evidence is documented in
[Phase 6B-1C](phase6b1c_forex_data_quality_audit.md). The preparation-time status
below is retained as historical context, not the current collection status.

Implementation date: 28 October 2026. This phase prepares collection; **neither
authenticated stage has run**. No Forex strategy or performance results are produced.
Existing stock datasets, paper portfolios and Phase 5C/5D research remain separate.

## Frozen target and offline estimates

Instruments: EUR_USD, GBP_USD, USD_JPY. Provider: OANDA v20, live environment,
M1, `price=MBA`, `smooth=false`, explicit UTC `from`/`to`, `includeFirst=true`.
The route is `GET /v3/instruments/{instrument}/candles` only.

Requested range: **2024-10-28T00:00:00Z to 2026-10-29T00:00:00Z**, end exclusive.
The plans prepared locally freeze the effective end at **2026-10-28T22:50:00Z**,
the latest completed minute when planned. They do not advance on resume. The last
70 minutes of the requested target are outside these plans. A newly generated plan
can include them once completed, but requires fresh approval. Do not extend an
already accepted partial-day partition in place; overlap protection stops that.

Stage A uses the first five complete weekday UTC dates: 28 October–1 November 2024,
ending 2 November 00:00 UTC. These are five weekday **UTC partitions** per pair,
not five New York rollover-to-rollover sessions. Friday closes before UTC midnight.
Stage B includes every UTC date in the frozen range and reuses Stage A partitions.
Weekend dates are requested too: no observations are skipped merely because a
published schedule suggests closure.

| Item | Stage A | Frozen full corpus |
|---|---:|---:|
| Instrument/date partitions | 15 | 2,193 |
| Initial GET requests before reuse | 30 | 4,386 |
| Initial GETs remaining after Stage A | — | 4,356 |
| Lifetime attempt cap, including retries | 90 | 13,158 |
| Estimated candles under published hours | 20,985 | 2,249,736 |
| Estimated stored bytes, 1,200 per candle | 25.2 MB | 2.70 GB |
| Required free space, before execution | 100 MB | 5.40 GB |

The initial request count is deterministic, using two half-day windows per full
UTC date (at most 720 M1 observations per request). This exercises pagination in
Stage A and stays below OANDA's 5,000-candle limit. A final shorter day still needs
two windows here. There are no guessed page tokens or cross-instrument pages.
The plan lists each partition and its page count; exact page bounds are generated
by the existing `history.windows` function.

The existing limiter permits at most 60 HTTP attempts per rolling 61 seconds and
at least one second between attempts. Initial full-range requests therefore take
approximately **74 minutes minimum**; allow roughly **90–150 minutes** with latency,
validation and disk I/O. This is an engineering estimate, not a measured live run.
Retries, cooldowns or stops can take longer. Stage A's request floor is about
31 seconds; allow several minutes including validation. Stage B's cap conservatively
includes three attempts for every planned window even though Stage A is reused.
Reapproval of the same plan does not replenish its attempt budget.

Disk check at preparation: approximately **131.9 GB free**. Storage estimates are
conservative approximations, not a reservation; execution rechecks free space.
An optional JSONL export creates another copy, so retain additional space for it.

## Persistence, recovery and integrity

The existing `ForexStore` remains the source of truth. Each instrument/UTC-date
partition has a canonical CandlePlan and stable content-derived identity. SQLite
stores normalized Decimal-compatible bid, ask and midpoint OHLC, original provider
timestamp text, complete flags and activity counts. Each page has a checksum and
fetch timestamp; source values are never rounded or synthesized. Manifests bind
instrument pip metadata, environment, origin, windows and ordered page hashes.

An outer collection ledger stores checksummed partition results and quality evidence.
Page data and its checkpoint commit together. An interruption before page commit
can repeat that bounded GET; an interruption after source acceptance but before
the outer checkpoint reuses the accepted source without a new GET. Completed
partitions are immutable. Overlapping partitions with different identities require
review instead of overwrite. Resume revalidates source hashes, manifests, quality
checksums and ledger scope before reuse. A global SQLite worker lock permits one
CLI collection worker and releases on process exit.

Processing and export buffer at most one UTC-date partition (at most 1,440 candles),
not the entire corpus. The full database integrity check runs once at collection
start; per-partition integrity checks still run on every reuse. Raw bid/ask/midpoint
observations are stored once in page content, not duplicated into each report.

All real collection artifacts are under ignored `local/oanda/`: plan JSON,
approval JSON, quota ledger, collection ledger, historical database, reports and
optional export. Export refuses overwrite. `dataset.reload_dataset` validates
partition hashes, ordered candles, metadata, recomputed quality and a final archive
hash chain. Consumers must exhaust the iterator to validate the final trailer;
an interrupted export is not a complete corpus.

## Quality policy

Critical failures stop collection: invalid timestamps/schema/OHLC, non-finite or
non-positive prices, wrong components/instrument, duplicate or unordered timestamps,
out-of-window observations, incomplete candles, corrupted checkpoints/hashes, or
an entirely empty partition during assumed open time. Invalid provider candle
contracts quarantine the source job; the collector records that quarantined status.
Untrusted raw invalid payloads are not retained by the existing reader. A quarantine
is not silently retried, discarded or converted to an accepted partition.

Reports include accepted, quarantined, pending and observed-empty-expected-closed
partition counts; candle counts; missing intervals; spread distributions by instrument
and UTC hour; and source/quality proofs. Missing minutes are never filled. OANDA's
currently published New York schedule (Sunday 17:05 through Friday 16:59, daily
16:59–17:05 break) is applied with `America/New_York` DST rules. Classification is
explicitly **ASSUMED historical schedule**, not verified account/holiday history.
Missing minutes outside those closures remain unexplained, including possible
holidays or periods without price updates. Observations inside assumed closures
are retained and flagged, not rejected.

An entirely empty UTC day is recorded as observed-empty-expected-closed only when
that whole day falls inside the documented closure assumption. The empty response
is hashed and persisted; it is not a fabricated candle or proof that no provider
outage occurred. Default Phase 6A empty-data quarantine behavior is preserved.

Indicative spread = `(ask.close - bid.close) / instrument.pip`. Reports retain
count, sum, min, max and fixed-bin histograms, enabling mean and approximate
distribution review. Negative endpoint spreads, midpoint outside component envelope,
precision beyond display metadata, spreads over 25 pips, ranges over 100 pips and
price jumps over 50 pips are review warnings. Discontinuities within and across
partitions are covered. These diagnostic cutoffs do not change strategy thresholds
or reject unusual legitimate volatility. Bid/ask open/close timestamps need not
coincide; highs/lows definitely cannot be assumed simultaneous executable quotes.

Original publication times are unavailable: source availability remains **UNKNOWN**.
A later exploratory backtester must explicitly choose an ASSUMED availability
model and must not label results point-in-time VERIFIED. `volume` is a price-update
activity count, not consolidated traded volume. Midpoint candles are not fills.
Instrument-level historical pricing is not proof of the precise prices executable
for the user's live account or order size.

## Approval and security boundary

Existing local Phase 6A evidence reports successful configured-account discovery
and metadata for the three instruments. This is not a historical-download pilot,
and historical candle access remains **untested**. No account identifier or credential
is copied into these docs or plans. The scoped root environment loader is used only
for approval signing or explicitly authorized execution; planning and dry run need
neither credentials nor transport construction.

Stage A and Stage B approvals are distinct HMAC-bound records with a 24-hour expiry,
plan hash, environment, instrument list, effective window, MBA components, request
cap and destination. The account and token bind the signature without being stored
in the record. Stage B additionally binds a freshly recomputed successful Stage A
report hash. A completed pilot still requires the user to inspect warnings and make
a new explicit decision. Discovery approvals cannot authorize either collection.

`approve` requires typing the full displayed plan-specific confirmation phrase.
That action confirms the proposed research/storage use and permits only that stage;
it is not an independent claim about provider licensing. `collect` defaults to dry
run and also requires `--execute` and a valid approval for authenticated work.
No approval artifacts were generated during this implementation.

The transport reuses the Phase 6A GET-only host/path/query guard, credential redaction,
10-second timeouts, response-size bounds, disabled environment proxies, no redirects,
and no broker methods. It additionally rejects any date window or instrument outside
the approved plan before attaching credentials or reserving an attempt. Persistent
quotas count retries across workers and restarts. HTTP 401/403 stop immediately;
429/5xx retries are bounded and Retry-After is persisted. Long cooldowns stop for a
later resume. A request with uncertain completion after a process crash remains a
fail-closed quota blocker for explicit review; do not delete/reset that history.

## Commands (PowerShell, repository root)

The following plans already exist locally and both dry runs succeeded. Do not rerun
`plan` over those files; output creation is exclusive. To deliberately prepare new
plans, use new filenames and review their effective end before approval.

```powershell
Set-Location "D:/Jafar's Work/project-atlas"
.venv/Scripts/python.exe -m atlas.main forex history plan --stage A --output local/oanda/phase6b1/stage-a-plan.json
.venv/Scripts/python.exe -m atlas.main forex history plan --stage B --pilot-plan local/oanda/phase6b1/stage-a-plan.json --output local/oanda/phase6b1/stage-b-plan.json
.venv/Scripts/python.exe -m atlas.main forex history collect --plan local/oanda/phase6b1/stage-a-plan.json
.venv/Scripts/python.exe -m atlas.main forex history collect --plan local/oanda/phase6b1/stage-b-plan.json
```

**Future Stage A approval and execution — not run in this phase:**

```powershell
.venv/Scripts/python.exe -m atlas.main forex history approve --plan local/oanda/phase6b1/stage-a-plan.json --output local/oanda/phase6b1/stage-a-approval.json
.venv/Scripts/python.exe -m atlas.main forex history collect --plan local/oanda/phase6b1/stage-a-plan.json --approval local/oanda/phase6b1/stage-a-approval.json --execute
.venv/Scripts/python.exe -m atlas.main forex history report --plan local/oanda/phase6b1/stage-a-plan.json --output local/oanda/phase6b1/stage-a-quality.json
```

Read `stage-a-quality.json`, including unexplained gaps, cross-component warnings
and spread distributions. Stop on quarantine or unexplained critical problems.
Only after a separate decision to proceed:

```powershell
.venv/Scripts/python.exe -m atlas.main forex history approve --plan local/oanda/phase6b1/stage-b-plan.json --pilot-plan local/oanda/phase6b1/stage-a-plan.json --output local/oanda/phase6b1/stage-b-approval.json
.venv/Scripts/python.exe -m atlas.main forex history collect --plan local/oanda/phase6b1/stage-b-plan.json --pilot-plan local/oanda/phase6b1/stage-a-plan.json --approval local/oanda/phase6b1/stage-b-approval.json --execute
```

**Resume:** rerun the identical `collect ... --execute` command for the interrupted
stage. Accepted partitions and committed pages are reused. An expired approval
requires the same `approve` command with a new output filename and another explicit
confirmation; reference the new file when resuming. Its stable plan budget remains
consumed. Do not edit a frozen plan, erase quotas, delete checkpoints or bypass a
quarantine. Quarantine/uncertain-request resolution is intentionally manual review;
there is no blanket override command.

Final report and optional verified export:

```powershell
.venv/Scripts/python.exe -m atlas.main forex history report --plan local/oanda/phase6b1/stage-b-plan.json --output local/oanda/phase6b1/final-quality.json
.venv/Scripts/python.exe -m atlas.main forex history export --plan local/oanda/phase6b1/stage-b-plan.json --output local/oanda/phase6b1/forex-mba.jsonl
```

Python interfaces for Phase 6B-2: `collection.iter_dataset(plan)` yields candles with
provenance, metadata, dataset/quality hashes and missing intervals;
`dataset.partitions(plan)` yields validated partition bundles; `reload_dataset(path)`
reloads a verified export. Order is UTC date then instrument then minute, **not a
globally merged cross-instrument event stream**. The future portfolio replay must
merge each day's instruments chronologically before making shared-capital decisions.
No Forex strategy, backtest performance or live execution is implemented here.

## Validation and remaining gates

Offline tests cover capped/frozen boundaries, all three instruments, two-page
partition retrieval, approval isolation/tampering/expiry, persistent request budgets,
GET-only exact-window guards, interruption before/after commits, pilot reuse, duplicate
timestamps, overlapping identities, database/checkpoint corruption, worker locking,
disk exhaustion, DST/weekend assumptions, pip/precision diagnostics and export/reload.
Existing Phase 6A tests continue covering redaction, retry/429/timeout behavior and
unsafe account routes. Validation: **1,407 tests passed**, including **56 new tests**;
Ruff lint and formatting checks passed (163 Python files); `pip check` passed.
The full suite emitted existing pandas/Starlette deprecation warnings. Both real-plan
offline CLI dry runs passed with zero authenticated attempts. Hash verification
confirmed five protected stock/paper databases and 5,037 Phase 5C/5D research artifacts
unchanged. The existing OANDA quota history still contains only the two earlier
successful discovery requests. All mocks use invented fixture values, not account
credentials.

Readiness gates for Phase 6B-2: separately authorized successful Stage A; human review
of its quality warnings; separately authorized Stage B; no unresolved quarantines;
revalidated source hashes; documented treatment of all missing intervals; explicit
ASSUMED availability, spread/fill and FX conversion policies. These data collection
checks alone do not establish an executable trading model or strategy profitability.

## Official references

- [OANDA v20 instrument contracts](https://developer.oanda.com/rest-live-v20/instrument-ep/)
  and [official OpenAPI specification](https://github.com/oanda/v20-openapi/blob/master/yaml/separate/v20_instrument.yaml):
  candle components, unsmoothed settings, time bounds and 5,000-candle maximum.
- [OANDA development guide](https://developer.oanda.com/rest-live-v20/development-guide/):
  live/practice hosts, 120 REST requests/second and two new connections/second;
  Atlas intentionally uses a much lower local rate.
- [OANDA UK trading hours](https://www.oanda.com/uk-en/trading/hours-of-operation/):
  current Forex hours and daylight-saving caveats, not a verified historical holiday calendar.
- [OANDA account pricing](https://developer.oanda.com/rest-live-v20/pricing-ep/):
  account-scoped pricing is distinct from generic instrument history. This collector
  does not call account pricing, orders, positions or transaction endpoints.
