# Historical provider selection and access

Original Phase 3A research checked 27 October 2026 against public primary documentation,
before a provider account existed. Phase 3A-M now adds a Massive adapter and the
owner-authorised Basic connectivity pilot; see its
[current evidence](phase3a_massive_acceptance_report.md) and
[provider guide](massive_provider.md). Atlas creates no accounts, buys no subscriptions
and accepts no licences. The original comparison below predates the pilot.
Prices below are advertised USD prices, not quotes or a guarantee of entitlements.

## Comparison and recommendation

| Source | US minute history | UK LSE minute history | Cost and limits | Suitability / verification |
| --- | --- | --- | --- | --- |
| Massive, formerly Polygon | Basic: rolling 2 years; Starter: 5; Developer: 10; Advanced: archive back to 2003-09-10 | US stock product does not establish XLON coverage | Basic $0, 5 calls/min, EOD-delivered minute aggregates; Starter $29/month; Developer $79; Advanced $199. Paid plans advertise unlimited calls, subject to service policies | Phase 3A-M adapter implemented; reviewed pilot verified 390 AAPL USD minute bars for 2026-10-26 with two HTTP 200 requests. Broader depth, coverage and rights remain unverified |
| Twelve Data | Documented 1-minute API; earliest possible minute date 2020-02-10, actual symbol/plan depth varies | Exchange table currently labels XLON **EOD**. BCXE is a different venue and cannot stand in for XLON | Basic free: 8 credits/min, 800/day; time_series costs 1 credit/symbol. Current displayed individual paid tiers $79/$229/$999 monthly; credit selections/prices can differ | US-only REST adapter implemented and HTTP-mocked. Account access, historical completeness, volume basis and rights unverified. UK minute access explicitly blocked |
| Alpaca | Historical API advertises history since 2016; feed/plan entitlement matters | No XLON coverage established | Basic free 200 calls/min; Plus $99/month, 10,000/min per current table | IEX-only observations are not consolidated-market volume. Older SIP history rules and account entitlements require verification. Not implemented |
| Alpha Vantage | TIME_SERIES_INTRADAY is premium, supports 1-minute and advertises 20+ years | Generic global/EOD symbol support does not verify LSE minute depth | Free 25 calls/day does not establish premium intraday rights. Paid price/rate selected in premium form; exact required tier not verified | Do not select based on generic UK ticker support; obtain XLON sample, units, depth and cost first. Not implemented |
| LSEG Historical Pricing | Intraday summaries include 1-minute buckets | Institutional candidate; exact XLON instrument/field/depth entitlement needs a quote and sample | Account/subscription quotation; no verified retail price or numerical quota | Potential reliable UK source, but access and licence review required; not implemented |
| LSE DataShop / historical products | Not the proposed US source | Exchange offers historical tick trades and other historical products; ready-made 1-minute OHLCV package not verified | Quote/package-specific pricing, delivery and rights | UK alternative: confirm sample, GBX/GBP metadata and permitted aggregation. Tick aggregation is outside this implementation |

Massive claims broad US venue coverage, but vendor marketing does not establish
point-in-time universe completeness or original availability. Start by evaluating
its free historical plan after approval; $29/month is a reasonable next candidate
if five years/bulk files are needed. Its end-of-day publication does not mean bars
were historically observable at bar end. [Massive pricing](https://massive.com/pricing?product=stocks),
[history windows](https://massive.com/knowledge-base/article/how-much-historical-stock-data-does-massive-have),
[flat files](https://massive.com/docs/flat-files/stocks/overview).

Twelve Data is useful for testing documented provider mechanics; do not buy Grow
assuming it supplies LSE minute data. Inspect each plan's display/non-display
permissions before using it on a dashboard. [Pricing](https://twelvedata.com/pricing),
[exchange coverage](https://twelvedata.com/exchanges),
[credit accounting](https://support.twelvedata.com/en/articles/5615854-credits),
[historical depth](https://support.twelvedata.com/en/articles/5656039-how-to-get-historical-prices),
[REST documentation](https://twelvedata.com/docs).

Alpaca's free feed can be useful for plumbing but feed choice affects volume and
execution assumptions. No free consolidated history entitlement is claimed here.
[Plan/feed table](https://docs.alpaca.markets/us/docs/about-market-data-api),
[feed distinctions](https://docs.alpaca.markets/us/v1.1/docs/historical-stock-data-1).
Alpha Vantage's premium intraday support does not establish UK coverage.
[API documentation](https://www.alphavantage.co/documentation/),
[premium access](https://www.alphavantage.co/premium/).

For LSE, seek a written sample/specification from LSEG or an authorised exchange
vendor after user approval. **No suitable low-cost LSE one-minute source has yet
been verified**; quotations, depth, GBP/GBX semantics, corporate actions and
retention rights remain blockers. Do not substitute 5-minute/EOD or another venue.
[LSEG intraday summaries](https://developers.lseg.com/en/api-catalog/lseg-data-platform/lseg-data-library-for-python/tutorials/content-tutorials/historical-pricing),
[LSE historical products](https://www.londonstockexchange.com/equities-trading/market-data/historical-analytics-data-products).

The recommended combination is therefore **Massive US evaluation + separately
verified LSE source**. This is a research recommendation, not a purchase or an
account-tested integration. Phase 3A-M now adds Massive to the original adapter list;
real-provider verification is limited to the recorded controlled pilot outcome.

## Implemented adapters

`massive`: dated US common-stock identity, USD minute aggregates, explicit split
policy, secure long continuations and shared persistent quota. The controlled
pilot first stopped locally with zero attempts, then succeeded after the owner
saved configuration and approved a reviewed retry: two HTTP 200 requests and 390
complete AAPL USD bars on 2026-10-26. Broader compatibility remains unverified.
See [setup and recovery](massive_provider.md); no additional request is authorised
by this status entry.

`fixture-v1`: fully offline, original artificial one-minute data for XLON GBP/GBX,
XNYS USD and XNAS USD. No universe is inferred from ticker strings.

`twelve-data`: fixed official HTTPS `/time_series` endpoint, Authorization-header
key, explicit `timezone=UTC`, `adjust=none`, 1-minute bars, maximum 5,000 rows/page,
descending exclusive-upper-bound pagination. Metadata must exactly match the
configured symbol, MIC, price unit and exchange timezone. Missing/contradictory
metadata quarantines data. The adapter supports XNYS/XNAS requests only. Other
adjustment policies fail closed. Corporate actions are supplied metadata, not
automatically fetched. Real earliest timestamps, empty-interval semantics,
listing types, permissions, numeric/schema behavior and entitlements remain
**NOT VERIFIED** without an authorised account.

Provider descriptors expose coverage limitations, auth, pagination, retry/rate
policy, licensing, provenance and activation state. `instruments` lists only the
four artificial identities; it never invents an external symbol catalog.

## Explicit future external configuration

Do not run an external download before separate approval of account, plan, licence
and the request. `examples/market-data.twelve-data.unapproved.json` is deliberately
disabled and contains fictional metadata. Replace it with verified metadata and
an internal non-secret approval reference only after approval. No key goes in JSON.

In a **local** PowerShell session, after approval, load a key without echoing it:

```powershell
$providerKey = Read-Host 'Approved provider API key' -AsSecureString
$providerPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($providerKey)
try { $env:ATLAS_TWELVEDATA_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($providerPtr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($providerPtr) }
# Only after explicit approval and verified metadata:
.\.venv\Scripts\python.exe -m atlas.main market-data download --provider twelve-data --config local/approved-historical-request.json --allow-network
Remove-Item Env:ATLAS_TWELVEDATA_API_KEY
Remove-Variable providerKey, providerPtr
```

Atlas does not load broker secrets or `.env` for historical commands. Never paste
provider credentials into chat, URLs, configuration, logs or Git. Environment
variables are process-local configuration, not encrypted persistent key storage.
Internal access references must not contain account numbers or credentials.

Three attempts/page, 10-second request/5-second connect timeout, no redirects or
environment proxies, bounded exponential transient retries; 401/403 stop. 429
Retry-After seconds/date is respected; long delays stop for later resumption.
SQLite coordinates an eight-second spacing/800-attempt UTC daily budget across
jobs using that store, charging retries. Other databases/clients also consume
the same provider quota and must be coordinated externally. Limits cannot be
raised from the CLI merely by selecting a paid plan. Requests are <=366 days,
64 pages, 200,000 normalized source rows; choose smaller windows for larger jobs.

Rights to persist raw data, back it up, display it, distribute data/derived alerts
to a friend, and retain it after cancellation are **unverified**. Local-only data
is not automatically exempt from provider terms. No downloaded datasets go in Git.
No scraping, undocumented endpoint, account creation or paywall bypass is used.
