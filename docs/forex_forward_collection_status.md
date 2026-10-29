# OANDA forward collection status

29 October 2026. **Decision: BLOCKED — ENTITLEMENT/COST.** Expanded user data
permission is recorded. The remaining decisive issue is the absence of verified
GBP0 incremental pricing for this existing account/use. No market-data request,
credential loading, collector configuration or activation occurred.

## Checkpoint and frozen scope

Started on `main` at `1f8dfeb118e75cffd13cd61aaf00376b2500e6fc`. The unfinished
untracked `src/atlas/forex/contract_readiness.py` is preserved exactly. No runtime
source, strategy, cost parameter, prior result or accepted dataset was changed.
Frozen manifest SHA-256 remains:

`6f84852343fbeb0971bf94ebb7a2c670c3ae60c7ce04212d8830503b2356a4a9`

Current authorized live host: `api-fxtrade.oanda.com`. Only HTTPS GET candle
requests for EUR_USD and GBP_USD are contemplated; no account/order/position or
transaction endpoints, other instruments, redirects or historical backfills.
EUR/USD: M1 `price=MBA`. The user now permits midpoint and GBP/USD conversion
observations; the earlier instrument/component permission mismatch is resolved.
Additional cost ceiling remains GBP0. No paid service or upgrade is authorized.

Clock checked: **2026-10-29 19:34:41 UTC**. Start is **2026-11-02 00:00
Europe/London** (00:00 UTC that day), so real collection cannot start now even
if the other gates pass. The fixed evaluation end remains 2027-05-03 exclusive.

## Existing successful integration: independently inspected

Read-only inspection of the saved Stage B plan and SQLite ledgers established:

| Evidence | Verified local result |
| --- | --- |
| Environment / instruments | Live; historical EUR_USD, GBP_USD, USD_JPY |
| Source representation | M1, unsmoothed bid/ask/midpoint OHLC; exact decimal schema |
| Historical route | `/v3/instruments/{instrument}/candles`, explicit UTC `from`/`to` bounds; no repeat download |
| Success ledger | 4,388 HTTP200 records, zero unfinished: 4,386 candle pages plus two prior discovery requests |
| Partition ledger | 2,193 accepted records; prior audited corpus 2,227,822 candles |
| Authentication | Existing secure local loader; Bearer token attached only after request validation, removed afterward; secrets redacted |
| Failure controls | GET-only transport, no redirects/proxy environment, explicit 10-second timeouts, maximum three reader attempts, persisted quota/cooldown, 401/403 stop |

These are historical access facts, not a current entitlement or price guarantee.
No account identifier, authorization record signature or token was printed or
read to inspect these aggregate records. The generic historical transport also
allows broader scopes/rates than this pilot and is not a certified narrowed
forward collector. No historical approval is reused as a new licence.

## Current public evidence and precise remaining gate

Public documentation was inspected without calling broker market/account APIs.
The [current UK legal hub](https://www.oanda.com/uk-en/legal/) links an
[April 2026 OANDA Europe Limited API licence](https://legal.oanda.com/?code=api_licence_agreement&language=en).
Clauses 3/4 describe conditional use permissions; clause 18.6 includes internal
research/analysis and restricts redistribution. This materially improves the
public evidence for the intended private research purpose. It does not verify
this account's accepted agreement or specific current rights.

The same licence's clause 18.4 permits charges for use exceeding its authorization;
it does not provide an affirmative GBP0 schedule for this use. The
[v20 introduction](https://developer.oanda.com/rest-live-v20/introduction/)
links API access to an eligible v20 account and licence acceptance. The
[UK charges page](https://www.oanda.com/uk-en/trading/our-charges/) does not establish
a zero incremental tariff for these candle requests. Absence of an advertised
per-call fee is not equivalent to verified GBP0 pricing.

The user's funded-account/API-access confirmation plus historical successes are
recorded and accepted as access evidence. They do not close the explicit current
entitlement/cost gate. No assertion is made that OANDA necessarily charges for
these requests: **the applicable incremental charge remains unverified**. Generic
third-party claims, Japan-specific free API terms, and the separately priced
Exchange Rates API cannot establish UK v20 pricing. No new service was selected.
The old individual candle documentation URL returned 404; existing implementation
and saved successful request metadata establish the historical route only.

**Needed to unblock:** applicable OANDA terms or nonsecret confirmation that this
existing account's private internal EUR/USD/GBP/USD v20 candle polling/storage
falls within its permission and incurs no incremental subscription, data or
per-request fee. Do not send tokens or account identifiers. No broker probe,
account query, new licence purchase or support contact was performed.

## Minimum GBP/USD inputs and technical readiness

The frozen filter reads the signal minute's GBP/USD ask close. USD income and
obligations convert through current ask/bid closes; funding and mark-to-market
need synchronized observations through the management interval. Thus the
mathematical minimum is **completed M1 bid/ask close prices, UTC minute timestamp
and completeness**, synchronized with EUR/USD. Coarser candles, stale conversion,
mid-only conversion and a tick stream are unnecessary or unsuitable.

The OANDA candle endpoint supplies OHLC per selected side: M1 `price=BA` is the
smallest suitable component request, retaining its supplied OHLC and provenance;
no GBP/USD midpoint need be requested for the arithmetic. Signal-time GBP/USD is
required at 09:00 London, then entry/management conversions only while exposure
exists. No USD/JPY prices or tick-level collection is needed.

**Compatibility caveat:** the unchanged shared `market.reason` currently requires
all bid/ask/mid components, including conversion observations. A later minimal
conversion-only BA eligibility adjustment needs focused review/tests before real
shadow processing; do not manufacture midpoint OHLC or silently request extra
components to bypass it. This is separate from the now-resolved user permission
gap. No compatibility change was implemented because the required cost gate failed.

## Actual operational status

| Item | Status |
| --- | --- |
| Collector / scheduler | Not configured, enabled or started by this task |
| Persistent operation across restarts | Not verified for real collection; no OS service/task installed |
| First actual forward candle / collection timestamp | None |
| Stored real forward candles | **0** |
| Missing minutes | Not applicable: prospective window has not begun |
| Duplicates | **0 observed**; no real response received |
| Real-data integrity / profitability | **NOT EVALUATED** |
| Shadow ledger | Existing synthetic-only guard retained; GBP1,000/GBP5 limits unchanged |
| Credentials | Secure loader inspected as source; actual secrets not loaded or validated |

No backdating, late-start backfill, substitution of old holdouts or synthetic
fixture performance occurred. Later missed dates/minutes must remain visible;
do not move the fixed window. No claim of continuous collection is made.

## Acceptance and next action

Existing deterministic OANDA/pilot tests: **114 passed**, no failures/errors/skips.
Ruff lint/formatting pass for **154 tracked Python files**; `pip check` passes.
No new test or collector implementation was necessary for a mandatory gate stop.
Tests validate existing generic guards and synthetic accounting; they do not
certify an unimplemented narrowed collector, new BA conversion gate or scheduler.

Preservation matched **5,330 readable protected/prior-evidence files**, including
the original 5,216 source artifacts. Two legacy Massive reports remain unreadable
and unverified. The original preregistration and unfinished Phase 6B-6 file are
unchanged. Local receipts: `local/verification/oanda-forward-unblock/`.

Separate critical review by the same agent confirmed the distinction between
user permission, historical access, public licence scope and verified current
cost; no unsupported free-access claim was introduced. Only this status report
is committed. No runtime or infrastructure work is expanded while the gate is open.

Next action: obtain the nonsecret applicable entitlement/GBP0 evidence above.
After that, complete the narrowly scoped request/conversion checks and verify
persistent scheduling before activation, never before 2 November. Any Windows
Task Scheduler/service installation would require a separately reviewed exact
command and user permission; none was attempted or silently installed.

**External market-data requests this task: zero.** Public OANDA documentation
pages were accessed. No broker orders/trades, account changes, historical
backfills, additional paid services or Telegram notifications occurred. No
subsequent phase was started.
