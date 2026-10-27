# Massive US historical provider (Phase 3A-M)

The adapter stores historical observations, never fills, portfolio changes or
notifications. No Phase 3B consumer exists. Research capital remains GBP 1,000.

## Identity, credentials and access

Only `https://api.massive.com` is allowed, using a Bearer header as documented in
the [official quickstart](https://massive.com/docs/rest/quickstart). No key goes
in request URLs, JSON configuration, logs, checkpoints or commands. Explicit
authorised requests lazily select MASSIVE_API_KEY from the current environment
or ignored local .env. Only that variable is selected, without exporting other
broker/Telegram settings. Matching quoted/raw values are supported, without
interpolation. Missing, empty, duplicate or invalid values produce static errors.

Initial support is one exchange-local date per request, dated US common stocks,
USD, XNYS/XNAS primary listing and a composite FIGI. Reference evidence establishes
the complete daily mapping; ticker or exchange alone never establishes currency.
The primary listing is not an assertion that every aggregate trade executed there.
ETFs, ADRs, inactive listings and incomplete identities fail closed. LSE is unsupported.
Reference company addresses/branding/account fields are not retained.

## Shared conservative quota

The owner reports Stocks Basic free, five requests/minute. Atlas allows at most
four attempts per rolling 60 seconds, with 16 seconds after each completion.
All endpoints/retries/processes share ignored
`local/market-data/massive-quota.sqlite3`, independent of dataset --db paths.
Other applications must not compete for the same account quota.

Short BEGIN IMMEDIATE transactions claim one in-flight token; no transaction is
held during HTTP or sleeps. The attempt stays charged through completion, covering
a paused dispatch. The four most recent completion charges enforce a conservative
61-second window. Waits recheck within five seconds and renew the import lease.
Rate time uses system-wide monotonic ticks; separate UTC audit stamps are not
historical availability. Normal approved downloads use <=3 attempts per endpoint.
Permanent errors do not retry. Retry-After is persisted for every endpoint; an
unsupported long interval blocks pending review rather than retrying too soon.

A crashed/uncertain request never expires into permission for another attempt.
Further work fails closed after a bounded wait. Monotonic rollback/reboot, uncertain
owners and storage errors require operator review. Do not delete/switch quota files
to evade guards. Confirm old workers have stopped and reconcile account activity
before any recovery; there is no automatic reset command. Existing quota stores are
schema-validated and missing history, columns, indexes or state are never recreated.
For administrative recovery, stop every Atlas/provider process, retain and back up
the damaged database, reconcile account-side request activity, wait at least 61
seconds after the last possible attempt, then create a new quota database at a new
operator-reviewed path. Never overwrite or delete the damaged evidence. This favours
safety over availability. Preserve quota state when backing up; stop clients before
copying it.

## Normalization, pagination and recovery

Native JSON numbers become exact Decimal text. Original epoch-millisecond starts
are retained, with UTC half-open minute intervals. Requests explicitly select raw
or split-adjusted data and validate the response policy; neither means dividend or
total-return adjustment. See [aggregate specification](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
and [dated identity specification](https://massive.com/docs/rest/stocks/tickers/ticker-overview).
Original publication/revision availability remains UNKNOWN. Strict visible_bars
therefore excludes the data even if storage quality is ACCEPTED with WARNING.

Continuation URLs require HTTPS, the exact allowed origin/path, no userinfo,
fragments, credentials, duplicate parameters or changed policy. Only bounded
cursor/adjusted/sort/limit parameters survive validation. A checkpoint contains only
the validated opaque cursor token; Atlas reconstructs the request from the fixed
HTTPS origin, approved aggregate path and fixed policy parameters. Massive's
[pagination specification](https://massive.com/blog/api-pagination-patterns) defines
an opaque `cursor` query parameter but does not publish a complete character grammar.
Atlas therefore deliberately supports only the URL-safe alphanumeric,
underscore/hyphen and optional terminal-padding subset shown by the official example,
bounded to 6,000 characters. A real continuation outside that subset fails closed and
requires review; actual aggregate pagination remains unverified. Base64, hexadecimal
and percent-decoded layers are checked for known-key echoes/private fields, but Atlas
does not claim arbitrary encoded secrets are discoverable by scanning. Structural
allowlisting and rejection of ambiguous/undocumented formats are the primary
boundary. Validation repeats after lazy key loading before an HTTP attempt.
Third-party transport logs redact all query values, not only the bearer credential.
Cycles, overlapping/backwards pages, invalid counts and suspected truncation fail
closed. Missing eligible-trade minutes are never invented; the existing strict
quality policy quarantines gaps. A warning is not proof of strategy suitability.

All JSON objects reject duplicate keys recursively, even when repeated values are
identical. Valid numeric values still use Decimal. Requests advertise identity
encoding; compressed responses are refused before body iteration, avoiding decoder
header stalls and expansion bombs. Raw streamed bytes, response headers and final
completion are checked against a 30-second application deadline in addition to
5-second connection/pool/write bounds and a transport read timeout reduced to the
remaining application budget before each raw read. Raw progress renews the import
lease, and cancellation closes response/client resources before quota completion is
charged.

Retry-After delta-seconds are bounded to one day. HTTP dates use the provider Date
header, never an untrusted skewed client subtraction. More than two minutes of
client/server clock disagreement, malformed server Date, or an excessive delay
creates an indefinite fail-closed review block. Without server Date, only a near-term
date (at most 61 conservatively rounded seconds) is accepted; longer dates require
review. A malformed Retry-After falls back to 60 seconds. The independent four-per-
rolling-minute quota remains an additional constraint.

Existing source proofs, immutable publication, fencing and checksum inspection
remain authoritative. General approved imports can use --resume-job, with exact
request/policy compatibility, reverified metadata and no refetch of saved pages.
Back up historical data with the existing SQLite online backup; verify inspect /
validate after restoring, rather than copying a live WAL database alone.

## Commands and bounded pilot

```powershell
.\.venv\Scripts\python.exe -m atlas.main market-data providers
.\.venv\Scripts\python.exe -m atlas.main market-data massive-pilot --help
# Offline-safe refusal; no key read:
.\.venv\Scripts\python.exe -m atlas.main market-data massive-pilot --json
# Only under explicit owner authorisation for the initial pilot:
.\.venv\Scripts\python.exe -m atlas.main market-data massive-pilot --allow-network --json
```

The initial pilot is AAPL only, one previously completed full regular session,
four lifetime attempts including metadata, and one attempt per request. Any
provider/security/data failure stops it. Its durable fixed identity prevents
rerunning a stopped/completed pilot as a retry mechanism. The committed unapproved
configuration example cannot download data. General downloads need separate
approval and verified identity/configuration. Dashboard startup never downloads.

Pilot request/data/report live in ignored `local/market-data/massive-pilot/`.
Safe reports contain endpoint kind, counts, HTTP status, UTC audit stamps and
redacted categories. Fresh-process inspection precedes successful pilot reporting.
No billing, account or licence change is automated. Check retention/display/
redistribution permissions before sharing observations. This one-session pilot
does not prove broad coverage, original revisions, corporate actions, a historical
universe or LSE access. See [acceptance evidence](phase3a_massive_acceptance_report.md).

### Current pilot result

The first invocation stopped before HTTP due to missing saved configuration. The
owner saved it locally and explicitly approved a reviewed retry. Only the proven
zero-attempt STOPPED record was archived; quota history was retained. The retry
succeeded with **2 total authenticated requests**, HTTP 200 for reference and
aggregates, **390 AAPL USD bars on 2026-10-26**, and no rate-limit response.
The dataset is ACCEPTED with WARNING; fresh-process integrity passed. UNKNOWN
publication availability still excludes it from strict replay. No secret value
was displayed or modified by Atlas. Do not rerun the completed pilot or reset
quota state; further requests need separate approval.

## Independent-audit remediation

The subsequent independent audit found five defects: encoded private cursor content,
duplicate JSON keys, decoded-stream deadline delay, HTTP-date clock-skew shortening,
and quota-history auto-recreation. All five were reproduced with fictional data and
fixed as described above. Thirty-five focused offline regression cases were added.
The complete offline suite now passes 846 tests (692 accepted Phase 3A, 119 original
Massive, 35 remediation), with Ruff and dependency verification passing. No real
request was made and the accepted pilot database/report were not changed. A separate
independent re-audit is still required before another provider request or checkpoint.
