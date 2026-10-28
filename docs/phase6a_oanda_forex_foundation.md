# Phase 6A — OANDA v20 Forex research foundation

Scope: offline implementation and mocked validation, 28 October 2026. All known
user accounts are **live**. No authenticated OANDA requests, historical downloads,
orders, account changes, notifications or subscription changes were performed.
Phase 5E stock execution realism, costs and IEX gap work remains **ON HOLD**.

## Architecture and boundaries

`atlas.forex` is independent of stock strategies, accepted stock datasets, stock
backtest artifacts, paper-account persistence, dashboard and Telegram workers.
The only existing-source integration is a lazy `forex` dispatch in `atlas.main`.
It executes before stock/demo configuration and never interprets OANDA live as
a demo account. Importing the package does not read credentials or open sockets.

| Module | Implemented responsibility |
| --- | --- |
| `config` | Selective root-dotenv loading, process-environment precedence, explicit live/practice selection, secret-safe representation |
| `access` | Scoped, expiring, account-bound local approval; process-shared request ledger, rate limit, retry cooldown and lifetime pilot cap |
| `provider` | GET-only transport, URL/query allowlist, bounded responses/timeouts/retries, redaction, account/instrument projection, candle parsing entry point |
| `models` | Forex and other instrument metadata; separate bid/ask/mid OHLC; exact decimal prices; explicit history plan, origin and availability |
| `history` | Separate SQLite schema, atomic page/checkpoint commits, deterministic identities, immutable accepted datasets, quarantine and integrity inspection |
| `accounting` | Synthetic GBP accounting, long/short P/L, explicit costs/conversion, pip/lot calculations, full-notional collateral and conservative risk checks |
| `sessions` | Explicit London/New York research windows, DST validation, overlap, weekend exclusions and instrument-hours gate |
| `cli` | Offline configuration validation, interactive approval, separately gated discovery and sanitized evidence |

Reuse includes Atlas's `AtlasError`, Decimal/UTC validation and step rounding,
`ResearchSettings` (GBP 1,000; 0.5% planned risk per trade; 1% daily/concurrent
planned-risk budgets), frozen historical base contracts, canonical JSON/SHA-256
helpers and the existing Retry-After parser. OANDA has separate quota and data
schemas because account access, Forex prices and activity counts do not fit the
equity-specific provider and venue contracts. Existing Alpaca/Massive adapters,
ORB thresholds, equity risk rules and accepted stores are unchanged.

## Structural read-only transport

Only these operations can pass the transport allowlist, within their approved scope:

- `GET /v3/accounts`.
- `GET /v3/accounts/{configured-account}/instruments`, filtered to exactly
  `EUR_USD,GBP_USD,USD_JPY`.
- Historical-provider scope only: `GET /v3/instruments/{supported-pair}/candles`
  with bounded explicit M1 time windows, allowed price components, `smooth=false`
  and `includeFirst=true`.

POST, PUT, PATCH, DELETE and all other non-GET methods fail **before** token
attachment, request-budget consumption or network transmission. All order,
trade, position, transfer, transaction, full-account and account-summary routes
are denied even if called with GET. Unknown paths/queries, duplicate query keys,
encoded paths, unapproved ports/hosts, URL credentials, fragments, GET bodies and
caller-supplied authorization headers are rejected. Host-header overrides,
additional headers (including method override) and caller transport extensions
(including SNI/trace overrides) are rejected before token attachment. Redirects
are never followed.
The private underlying HTTP transport is not exposed as a public adapter method.
No order SDK, broker execution interface or environment flag for enabling orders
exists in this package. Offline accounting imports no network provider.

The selected environment fixes one HTTPS host:

| Explicit environment | Allowed host | Meaning |
| --- | --- | --- |
| `live` | `api-fxtrade.oanda.com` | Live account, read-only capability |
| `practice` | `api-fxpractice.oanda.com` | Explicit practice access, still read-only |

There is no environment fallback. TLS verification remains enabled, environment
proxy configuration is ignored, and the transport has no automatic retries or
redirect handling beneath the attempt ledger. Connect/read/write/pool timeouts
are 10 seconds. Response consumption is bounded at 8 MB. Request headers are
scrubbed after transmission; debug logging redacts the token and account IDs;
upstream exception text and error bodies are not surfaced or persisted.
Public discovery output contains ordinal account labels, never full identifiers,
aliases, tags, balances, trades or positions.

The official development guide identifies the two REST hosts and publishes
120 requests/second and two new connections/second limits. Atlas deliberately
uses **at most 60 attempts per rolling 61 seconds**, at least one second apart,
shared across local workers and environments through one OANDA ledger. Every
retry consumes an attempt. A maximum of three attempts per operation is further
constrained by the approval's smaller lifetime cap. Numeric and HTTP-date
Retry-After values persist; delays over 30 seconds stop for later resume rather
than shortening the provider cooldown. 401/403 stop immediately. An uncertain
pending request after a crash blocks further requests for explicit review.
[OANDA development guide](https://developer.oanda.com/rest-live-v20/development-guide/).

This local ledger cannot count requests made by unrelated software outside Atlas.
It is not a replacement for provider limits or account permissions.

## Configuration and separate approvals

Supported existing variables are `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` and
`OANDA_ENVIRONMENT=live`. Values are read lazily from process environment and,
only for absent keys, repository-root `.env`, regardless of current directory.
Explicit empty process values remain authoritative and fail validation; no
placeholder or automatic rewrite is performed. Parsed values stay local to the
dedicated command and are never assigned into `os.environ`. Nothing loads OANDA
credentials into dashboard, Telegram or stock configuration.

Approval records are non-sensitive JSON stored only under repository `local/`.
They expire after 24 hours, bind environment, configured account, token, scope
and attempt cap through HMAC, and include a fresh identity. HMAC is a local
integrity/binding mechanism, not provider permission or protection against a
hostile user who already controls the local token. Reusing/copying the same
record cannot reset its request count in the canonical quota database. Files
are created exclusively and cannot overwrite existing evidence.

The following remain separate:

1. Offline configuration validation: credentials are syntactically present.
2. Explicit user approval for a narrowly scoped read-only request.
3. Actual observed v20 account access and listed instruments, after a pilot.
4. Historical access/data-use permission and product/account suitability, still
   unverified and required before future historical collection.
5. Broker execution: unsupported, regardless of any of the above.

Actual offline smoke tests report live configuration and credentials present,
including from outside the repository directory. **Zero authenticated attempts**
were made. No real approval record was created by the implementation agent.

## Account discovery: what can be established

The list endpoint establishes only which v20 account identifiers are accessible
to the token. Atlas checks whether the configured identifier occurs in that
list; it does not select an account based on `PRIMARY`, infer ownership of MT4/
MT5 accounts, or declare an account suitable for future automated execution.
Instrument discovery reports the returned subset and separately lists requested
symbols not returned. A listing is not current market-open status or proof of
historical access, jurisdictional suitability or permission to trade.

The list response does not establish account currency. Full account and summary
responses include financial information unnecessary for this limited pilot, so
both routes are deliberately excluded. Currency is reported as
`UNKNOWN_NOT_REQUESTED`; the GBP 1,000 synthetic account is not inferred from
the live account. Account currency and exact product type require a later
user-confirmed record or separately justified metadata mechanism. Spread betting,
Forex CFDs, MT4, MT5 and v20 are not treated as equivalent capabilities.
[OANDA account endpoint contracts](https://developer.oanda.com/rest-live-v20/account-ep/),
[OANDA API/account compatibility](https://developer.oanda.com/).

Instrument metadata preserves name/type, display precision, pip location,
minimum/maximum units, unit precision, margin rate and documented numeric
restrictions. `CURRENCY`, `METAL` and `CFD` metadata can be represented, including
positive or negative pip locations. Only the three planned currency pairs are
currently network-allowlisted. Non-Forex contract valuation is explicitly
unsupported; neither common contract sizes nor trading hours are invented.
[OANDA instrument primitives](https://developer.oanda.com/rest-live-v20/primitives-df/).

## Historical candle foundation

The provider supports `MBA`, `BA`, `M`, `B` and `A` component selections. Prices
are positive finite Decimals sourced from decimal strings, with OHLC relationship
checks and **no penny-grid coercion or rounding**. Bid, ask and midpoint retain
separate OHLC values. Component extrema are not assumed to be synchronous quotes;
midpoint or candle OHLC objects cannot serve as executable `FxQuote` inputs.

M1 source timestamps are retained verbatim, including zero nanosecond precision,
and validated against normalized UTC minute starts. Complete/incomplete flags
are preserved. `volume` is stored as `activity_count` with semantics
`NUMBER_OF_PRICES_NOT_TRADED_VOLUME`. Original publication time stays null and
availability stays `UNKNOWN`; ingestion time never becomes publication evidence.
No historical dataset can claim point-in-time verification merely because it was
downloaded successfully.
[OANDA official instrument/candle API schema](https://github.com/oanda/v20-openapi/blob/master/yaml/separate/v20_instrument.yaml).

The official schema permits up to 5,000 candles and says not to combine `count`
with both range bounds. Atlas instead divides a frozen interval into adjacent
windows of at most 5,000 minutes, sending `from` and `to` without `count`.
Returned candles must be within the requested half-open window; unexpected
boundary behaviour fails validation rather than being silently trimmed. Empty
windows still advance by their planned bounds. Duplicate, reversed, out-of-range,
wrong-symbol or missing-component responses are rejected.

`ForexStore.collect(plan, reader)` is implemented for an explicitly supplied
reader; it does not create a transport or load credentials. Each page and its
checkpoint commit atomically under a SQLite writer lock. Only one collection
worker can own a write transaction. Interrupted incomplete jobs resume at the
next committed window; a crash before commit can repeat a read-only GET but
cannot duplicate a page or accepted partition. A transport request left uncertain
still requires quota review before real-network recovery.

Accepted identity covers the frozen plan, provider/environment/origin, complete
metadata, ordered page hashes, bar count and warnings. Fetch timestamps do not
make identical content produce different dataset IDs. Inspection rechecks stored
page hashes, normalized contracts, plan/checkpoint identity and the manifest.
Accepted jobs are reused without requests or overwrites. Invalid responses,
empty entire partitions and incomplete-candle partitions are quarantined;
quarantine does not automatically retry or convert to acceptance.

Gaps are preserved. Until instrument trading hours/holidays are established,
unobserved minutes are explicitly **unclassified**, not automatically market
closures or errors. Accepted data carries schedule/availability warnings and is
not automatically eligible for a Forex strategy. No missing prices are fabricated
or interpolated. Mock and external origins are distinct, and mocked HTTP candle
responses cannot claim an external origin.

Historical retrieval/storage is offline-tested, but **there is no historical
execution command or historical approval generator in Phase 6A**. Discovery
approval cannot authorize candle requests. Phase 6B must add a separately reviewed
historical pilot scope after actual access and usage terms are established.

## Accounting and strategy readiness

Implemented synthetic calculations include:

- Long entries at ask and exits at bid; short entries at bid and exits at ask,
  with explicitly supplied adverse slippage and per-unit GBP commissions.
- Pip size from metadata, quote-currency pip value, explicit quote-to-GBP
  conversion, and lot equivalents only when a units-per-lot convention is given.
- Positive finite, timestamped conversion evidence with explicit ASSUMED/VERIFIED
  input labels, no future evidence, five-minute conversion freshness and
  one-minute executable-quote freshness. An input label is not external verification.
- Exact unit stepping/minimum/maximum checks, directional stop/target plans,
  size calculation and independent opening cash/risk checks.
- GBP 1,000 initial synthetic cash, full quote-notional collateral in both
  directions, no short-sale proceeds, no borrowed cash and **one open position**
  in this initial foundation. Broker margin rates calculate informational estimates
  only and cannot reduce research collateral or enable leverage.
- Realised/unrealised P/L, charged costs, explicit financing charges, monotonic
  event time, duplicate-entry refusal and closed-account cash reconciliation.

This is an in-memory, cash-collateralized P/L foundation, not a durable Forex
execution engine or a replica of any live CFD/spread-bet account. Stops are plans,
not guaranteed gap-loss caps. An exit across the conservative New York 17:00
rollover boundary requires an explicit financing charge; actual division-specific
rates, credits, triple financing, taxes and product-specific margin/valuation
rules remain unsupported. Equity commission assumptions are not inherited.

The stock ORB engine uses equity instrument/unit/calendar contracts and the
existing long-only equity ledger. It is **not wired directly to Forex**. London
(08:00–17:00 local), New York (08:00–17:00 local) and their intersection are
research-window definitions, not provider trading-hour claims. ZoneInfo handles
different UK/US DST change weeks; ambiguous/nonexistent boundaries are rejected.
Weekends are excluded from these weekday research windows. Explicit confirmed
instrument windows must contain a proposed session, so holidays, maintenance,
weekend reopenings and unknown hours cannot silently inherit a US equity calendar.
No ORB thresholds were changed and no Forex performance backtest was run.

## Later read-only pilot commands

Run from the repository root in PowerShell. These commands are prepared, **not
executed as part of Phase 6A** except the offline validation/dry-run commands.

Offline checks, safe to repeat without consuming requests:

```powershell
.venv/Scripts/python.exe -m atlas.main forex validate
.venv/Scripts/python.exe -m atlas.main forex discover
```

Approve account listing plus narrowly filtered instrument discovery:

```powershell
.venv/Scripts/python.exe -m atlas.main forex approve --scope accounts-instruments --output local/oanda/phase6a-discovery-approval.json
```

The interactive prompt requires this exact phrase:

```text
APPROVE OANDA LIVE READ ONLY ACCOUNTS-INSTRUMENTS
```

After that separate approval, execute at most **two total authenticated attempts**
(including retries) using the configured live account:

```powershell
.venv/Scripts/python.exe -m atlas.main forex discover --execute --approval local/oanda/phase6a-discovery-approval.json --output local/oanda/phase6a-discovery-evidence.json
```

For an accounts-only pilot, use scope `accounts`, a separate approval filename,
and confirmation `APPROVE OANDA LIVE READ ONLY ACCOUNTS`; its lifetime cap is one
attempt. The execution command otherwise has the same form. Instruments are not
requested under accounts-only approval. An inaccessible configured account stops
before instrument discovery. A retry can consume the budget and prevent the
second discovery operation; the command must not obtain a fresh approval
automatically. Existing evidence is never overwritten. Stale approval, uncertain
request state or exhausted cap requires explicit review, not quota deletion.

## Verification and Phase 6B criteria

The test suite uses invented fixture credentials and mocked responses only.
It covers host/method/path/query boundaries, redirects, preexisting authorization,
redaction, live/practice separation, approval integrity/expiry, caps including
retries, shared rolling limits, cooldown persistence, uncertain requests,
account/instrument parsing, separate pricing components, bad precision/OHLC,
time semantics, gaps, incomplete candles, pagination, committed/uncommitted crash
recovery, worker exclusion, immutable reuse, hash corruption, Forex P/L and
conversion, funding restrictions, financing and DST/session gates.

Phase 6B remains blocked on:

1. Separate user approval and execution of the read-only discovery pilot.
2. Confirmed configured v20 account access, available pairs, account currency and
   exact product/jurisdiction; no account alias or platform label substitutes.
3. Applicable market-data usage/storage terms and actual historical candle access.
4. A separately scoped historical pilot approval/command with immutable session
   manifests and validated trading-hour/holiday assumptions.
5. Agreed Forex execution, conversion, cost, financing and risk specifications,
   followed by a dedicated strategy bridge and durable simulated Forex ledger.

No new token or credential disclosure is required: existing configuration passes
offline validation. No approval for orders is requested or implemented.

Final validation: **1,351 tests passed**, including **90 new Forex/OANDA tests**
and all 1,261 existing tests; 653 existing warnings. Ruff lint, Ruff formatting
and `pip check` pass. All five protected historical/paper database file hashes
and all 5,037 Phase 5C/5D research artifact hashes match their prior records.
Staged source/tests/documentation were checked against the actual configured
OANDA token and account identifier without printing either; neither is present.

The Git checkpoint is reported in the task response. Local verification logs and
source-artifact integrity evidence are kept under ignored
`local/verification/phase6a/`. `.env`, databases, downloaded data, local approvals/
evidence and logs remain excluded from Git.
