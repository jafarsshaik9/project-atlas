# OANDA forward collection status

29 October 2026. **Decision: BLOCKED — ENTITLEMENT OR COST UNVERIFIED.**
The user has authorized a specific read-only market-data scope. The mandatory
pre-request evidence gate did not pass, so no token was loaded, transport
constructed, request submitted or collector enabled. No strategy/code change was
necessary to reach this fail-closed decision.

## Authorization and checkpoint

Started on `main` at `b34db4058794c84d6662ac55943fb5f3e9b8fef6`, matching the
local `origin/main` checkpoint. The only pre-existing working-tree item was the
unfinished untracked `src/atlas/forex/contract_readiness.py`; it is unchanged and
excluded from the status-report commit.

Allowed market request: **HTTPS GET**
`https://api-fxtrade.oanda.com/v3/instruments/EUR_USD/candles`,
`granularity=M1`, `price=BA`, live environment, no more than once per completed
minute, maximum additional cost **GBP0**, existing local credentials only.
No other instrument, component, host, endpoint, method, redirect, account/order
query or historical backfill is authorized. Authorization begins **2 November
2026, Europe/London**, which is 2026-11-02 00:00 UTC on that date.

Clock observed at preflight: **2026-10-29 19:09:54 UTC**. The date gate is closed.
No future-start scheduler or background process was installed. This is not a
claim of active or continuously scheduled collection.

Frozen preregistration SHA-256 verified unchanged:

`6f84852343fbeb0971bf94ebb7a2c670c3ae60c7ce04212d8830503b2356a4a9`

## Mandatory gate findings

| Gate | Evidence / result |
| --- | --- |
| Provider entitlement | **Unverified.** Existing discovery/collection shows prior access, not current research/storage/data rights for this forward use. Prior approval artifacts authorize user actions, not provider licensing. |
| Additional charge | **Unverified.** No reviewed local source establishes GBP0 additional cost for this specific usage/account entitlement. User's GBP0 ceiling is a limit, not evidence of the provider's charging terms. |
| Credentials | **Not loaded or checked.** Existing secure `atlas.forex.config.load_config` mechanism inspected as source only. Gate failure makes secret access unnecessary. No assertion that the current token is present, valid or absent. |
| Host/path | Requested live URL matches the repository's live-host mapping and candle route. No live connection was tested. |
| Strict collector boundary | No activated collector exists. The inherited general read-only transport permits broader approved scopes, three instruments and multiple components; it must not be used unchanged as proof of this new single-endpoint authorization. |
| Start date | **Not reached.** No real candles may be requested now, even if cost/entitlement were established. |
| Shadow input compatibility | **Blocked.** Frozen signals use provider midpoint OHLC; price=BA does not supply that component. The frozen cost filter, GBP sizing and P&L also require current GBP/USD observations, outside this authorization. |

The [prior data authorization investigation](forex_forward_data_authorization.md)
explicitly recorded entitlements and charges as unknown. The
[historical collection contract](phase6b1_oanda_historical_pilot.md) distinguishes
user approval from a provider licence. No private-account evidence, credential
store, broker endpoint or unrelated service was accessed to try to close these
gaps. Public/token possession claims were not substituted for verified rights.

The authorization mismatch is independent of entitlement: EUR/USD BA could
eventually support a bounded data-only collector, but cannot evaluate the frozen
hypothesis by itself. Do not request `MBA`, add GBP/USD, fabricate/stale-fill
conversion rates, derive substitute midpoint OHLC, or change the experiment
without a separate explicit decision. No scope expansion occurred.

## Actual collection and research status

| Item | Actual status |
| --- | --- |
| First actual collection timestamp | None |
| Latest collected forward candle | None |
| Real completed forward candles stored | **0** |
| Missing-minute count | **Not applicable yet:** prospective period has not started; no actual coverage denominator |
| Duplicate observations | **0 observed**; no real response received |
| Forward data integrity | NOT EVALUATED; no real observations |
| Collector process | Not started by this task; no persistent forward process/scheduler configured or verified |
| Persistence across restarts | Existing synthetic continuity checks pass; **real collector persistence NOT VERIFIED** |
| Shadow trading | Synthetic-only implementation retained; real-data guard remains closed |
| P&L, expectancy, profit factor, drawdown, capital utilization | **NOT EVALUATED** |

No prospective observation is backdated. If a later approved activation misses
the start or subsequent minutes, record that gap; do not backfill or shift the
fixed 2026-11-02 to 2027-05-03 evaluation window. No simulated fixture is reported
as market performance. GBP1,000/GBP5 risk rules remain unchanged.

## Technical verification and independent review

Existing OANDA read-only and pilot tests run exclusively with synthetic responses
and invented credentials. They exercise GET/method/host/path/header restrictions,
redirect refusal, redaction, bounded timeout retries, rate-limit persistence,
candle precision/BA components, duplicates/gaps, synthetic ledger continuity and
frozen-hash protection. They do **not** certify an unimplemented strict forward
collector or a 60-second persisted polling policy. No new tests were added solely
for a documentation-only blocked preflight.

Results: **114 passed**, no failures/errors/skips, in 13.64 seconds. Ruff lint
and formatting pass for all **154 tracked Python files**; `pip check` passes.
The unfinished untracked Phase 6B-6 file is excluded from lint and left untouched.
Relevant results and dependency checks are recorded in local-only
`local/verification/oanda-forward-activation/`. No runtime source, manifest,
credential configuration, approval or historical artifact was modified. The
preservation inventory matches **5,320 readable protected/prior-evidence files**;
two previously unreadable legacy Massive reports remain unverified. The original
5,216 readable source artifacts and the unfinished Phase 6B-6 file are unchanged.

A separate critical pass by the same agent, not external certification, confirmed:
prior access is not entitlement/cost proof; the date gate is closed; BA-only
EUR/USD is insufficient for the frozen signal/GBP ledger; and a generic GET-only
transport does not enforce the new narrower scope or polling limit. The safe
resolution is to stop before credential/network activation, not weaken a gate.

## Required next decision

1. Establish applicable OANDA market-data research/storage rights and GBP0
   incremental charging from appropriate existing terms or nonsecret evidence;
   a probe request/account query is not authorized as a substitute.
2. Decide whether to authorize the additional midpoint and GBP/USD market inputs
   required by the unchanged frozen pilot, or retain EUR/USD BA for data-only
   collection with shadow evaluation blocked. Neither expansion is inferred.
3. Only after those gates and the start date: implement/test the exact narrowed
   request guard, persisted once-per-minute ceiling, bounded failures, forward-only
   interval and storage; explicitly verify a persistent process before claiming
   continuous operation. No such process is armed now.

No trades were placed; no order or account endpoints were called; no account
changes, historical backfills, paid requests or Telegram notifications occurred.
There were **zero broker API requests and no real credential access**. Only the
separately permitted private Git backup may use network access. No subsequent
phase was started.
