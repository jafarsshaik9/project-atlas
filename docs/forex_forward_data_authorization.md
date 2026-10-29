# Forward market-data authorization checklist

**No external data request is authorized or implemented.** No network call was
made to research or test a provider in this task. The statements below reuse the
29 October 2026 repository evidence; they are not new live verification. An API
being documented does not establish access, licensing, suitability or zero cost.

## Candidate access paths

| Path | Exact endpoint or file | Scope and blockers |
| --- | --- | --- |
| OANDA Europe v20 market candles | `GET https://api-fxtrade.oanda.com/v3/instruments/EUR_USD/candles` and the same path with `GBP_USD` | Existing Atlas provider conventions support M1 `price=MBA`, `smooth=false`; start-of-minute UTC timestamps, completion flag and indicative BA/mid OHLC. Authenticated access/token and applicable account/API/data rights are required; anonymous access is not supported by the inherited transport. Forbidden in this task. |
| Externally prepared, licensed forward file | Proposed `local/forex-forward-pilot/inbox/YYYY-MM-DD.json` | User-supplied `Batch` schema: EUR/USD plus GBP/USD, M1, MBA, actual acquisition times, source identity, unmodified raw inputs and checksums. File handoff involves no Atlas network/credential access, but original provider, acquisition permission, fees, licensing and unexposed provenance must be confirmed first. Real ingestion is currently hard-blocked. |
| IG / CMC | No exact suitable anonymous endpoint verified | Archived IG API descriptions require account/product rights and an exact instrument identifier; CMC retail programmatic entitlement remains unresolved. Not proposed as callable anonymous data sources. Do not guess endpoints or obtain tokens. |

OANDA is a continuity candidate, not a selected provider. Its price endpoint is
in the broker API family, so using it would require explicitly revisiting the
broker-request/credential prohibitions; no permission is implied here. A bounded
later proposal could request only newly completed bars after each London minute,
never historical `from`/`to` ranges or earlier-period backfills. Even a latest-bar
response must exclude pre-authorization data from fresh validation.

Archived references, not accessed in this task:
[OANDA v20 introduction](https://developer.oanda.com/rest-live-v20/introduction/),
[candle documentation](https://developer.oanda.com/rest-live-v20/instrument-ep/),
[IG API guide](https://bts.ig.com/uk/trading-platforms/trading-apis/how-to-use-ig-api),
[existing evidence matrix](phase6b5_broker_contract_matrix.md).
Current quotas, license/retention terms, any subscription/data charges and precise
account-specific market-data entitlement remain **UNKNOWN**. Do not represent
them as verified or free. No account/private documents were accessed.

## Required provider contract before a request

- Name the legal provider and UK account/product context; confirm permitted
  offline research, storage and retention rights, no redistribution assumption.
- Approve exact market-data endpoint(s) or exact file handoff, **EUR/USD and
  GBP/USD conversion only**, M1 matched bid/ask and midpoint OHLC. No balances,
  positions, discovery, orders or third-pair price calls.
- Define the query, cadence, request ceiling, rate-limit/backoff behavior and
  expected maximum charge. Those remain unresolved; no requests may begin yet.
- Confirm whether authentication/subscription is required. Do not supply tokens
  in a report or chat. Any future credential access needs separate explicit scope;
  the current code has no credential reader or provider client.
- Cover London 08:00 through mandatory flattening (08:00–17:00 local is sufficient
  nominal coverage), DST-aware. Preserve actual response receipt and market
  timestamps; do not infer publication availability from candle completion alone.
- Require unique chronological complete M1 MBA observations, exact decimal
  prices, valid OHLC, uncrossed matched endpoints and current conversion data.
  Record all missing, incomplete, late and rejected observations without repair.
- Approve a start no earlier than the fixed 2 November 2026 prospective boundary
  and after data authorization; log late starts/gaps without shortening the window.
- Review the nonnetwork import guard and evidence receipt anchoring before
  enabling **any real-data offline processing**. There is no runtime unlock flag.

**Exact proposed next action:** choose a licensed forward-file handoff or request
an explicitly scoped provider plan with endpoint, parameters, limits and maximum
cost filled in. Unknown fees or entitlements must be resolved before approval.
This document is a checklist for that decision, not a permission request to trade
or an authorization to collect now. No account creation/contact/funding is proposed.
