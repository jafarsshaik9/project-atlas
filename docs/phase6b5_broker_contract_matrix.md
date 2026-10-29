# Phase 6B-5 broker and instrument contract matrix

Review date: **29 October 2026**, Europe/London. Public documentation only; no
broker API endpoint, account, credential or token was accessed. A documentation
URL is not an API call or evidence of an accepted order.

**No candidate has a complete executable contract verified in this phase.** The
defensible output is a normalized contract schema with explicit missing fields,
plus a field-by-field evidence register. Real product acceptance remains blocked.

The [machine-readable register](phase6b5_results/broker-findings.json) contains
**192 findings**: 21 fields for each provider/product combination across IG,
OANDA and CMC deliverable spot, rolling FX CFD and FX spread betting, plus three
related-product notes. Every row identifies its URL, retrieval date, publication/
update date when available, product/account scope and classification. The register
does not copy specifications from one product into another.

## Evidence rules

| Classification | Meaning |
| --- | --- |
| VERIFIED_PUBLIC | A public statement was verified in its stated scope; not account entitlement or executable specification confirmation |
| ILLUSTRATIVE | An educational/platform comparison value, or a declared artificial contract input |
| INFERRED | A derived convention or interpretation requiring independent contract confirmation |
| UNRESOLVED | Missing, ambiguous, inaccessible or insufficiently product-specific |
| VERIFIED_ARTIFICIAL | Used only in the separate synthetic fixtures; never a public broker fact |

Generic marketing never promotes a contract to executable. The offline model sets
`executable=false` unconditionally. Essential missing values block numerical
assessment; known but unverified values remain `ILLUSTRATIVE_ONLY`. Margin-based
allocation additionally requires explicit margin and leverage-ceiling evidence.
Artificial verification cannot be attached to a public contract.

## Products and quantity conventions

| Provider/product | Public finding | Important boundary |
| --- | --- | --- |
| IG rolling/spot FX CFDs | Standard and mini point values are published | Contract size is not current minimum order quantity or step |
| IG FX spread bets | Published GBP stakes and point increments below | Exact account, current increments and stop restrictions remain unresolved |
| IG forward FX spread bets | Separate forward tables, expiry/rollover and wider spread terms | Not the rolling spot contract; unsupported by the new model |
| OANDA rolling FX CFDs | Native units; MT4/MT5 lots | A CFD minimum is not a spread-bet minimum |
| OANDA FX spread bets | SB-enabled v20/v20 MT4 subaccounts described | OANDA One/MT5 does not offer SB; exact unit/stake normalization unresolved |
| CMC rolling FX CFDs | Native/MT4 size examples published | Platform examples require actual contract reconfirmation |
| CMC FX spread bets | GBP stake examples and instrument-specific ticket minima | Generic 1p minimum does not establish an FX minimum |
| CMC FX Active | A separate commission-bearing CFD offering | Zero-spread headline cannot be transferred to SB |
| All three: deliverable spot | A borrowing-free currency inventory/settlement contract was not established | Do not call rolling derivatives physical currency ownership |

For IG, the spot spread-bet table lists EUR/USD GBP 0.50/point, GBP/USD GBP 1/point
and USD/JPY GBP 0.50/point, with price increments 0.0001, 0.0001 and 0.01.
Advertised minimum spreads are 0.6, 0.9 and 0.7 points. Listed guarantee premiums
are 1.2, 2 and 1.5 points. Stake steps and current guaranteed minimum distances
are not established. Historical average-spread footnotes refer to 2021, not current
typical execution. These public-table facts do not establish current ticket terms.
[IG spread-bet product details](https://bts.ig.com/uk/help-and-support/spread-betting-and-cfds/products-markets-and-trading-hours/what-are-igs-forex-spread-bet-product-details).

IG's CFD page separately describes standard/mini contract values. We do not copy
SB guarantee premiums or order-size assumptions into CFD contracts.
[IG CFD product details](https://bts.ig.com/uk/help-and-support/spread-betting-and-cfds/products-markets-and-trading-hours/what-are-igs-forex-cfd-product-details).

OANDA's native minimum is **one unit for FX CFDs**, versus **0.01 lot** on MT4/5.
The reviewed minimum-size page does not establish every instrument's step or an
SB stake. Its subaccount documentation distinguishes unit/lot interfaces and
last-digit MT5 points from pip conventions. SB is available on enabled v20/v20
MT4 subaccounts, not OANDA One/MT5. A standard-lot multiplier used in artificial
normalization tests is not a new broker-specific confirmation.
[OANDA minimum sizes](https://help.oanda.com/uk/en/faqs/minimum-trade-size.htm),
[OANDA subaccount differences](https://www.oanda.com/uk-en/trading/standard-account/differences/).

CMC's platform comparison gives EUR/USD native **400 EUR minimum, 1 EUR step**
and MT4 **0.01 lot minimum/step**, equated to 1,000 EUR. These narrow comparison
examples improve understanding of units versus lots; they do not establish SB
stake mapping or binding current terms. The same page describes MT4 spread bets/
CFDs and algorithms, but not an account-specific automation entitlement.
[CMC platform comparison](https://www.cmcmarkets.com/en-gb/trading-platforms/metatrader-4/next-generation-vs-metatrader-4).

CMC's GBP 0.30/point FX educational example remains illustrative. Its FAQ directs
users to the instrument ticket for the actual minimum. Neither generic small
stakes nor an inconsistent educational pip-profit example is accepted as a
contract specification.
[CMC educational example](https://www.cmcmarkets.com/en-gb/forex/how-much-do-you-need-to-start-trading-forex),
[CMC SB FAQ](https://www.cmcmarkets.com/en-gb/support/faqs/spread-betting).

## Pip value, funding and protection

For base-unit FX, GBP pip value is `base units * pip price increment / quote
currency per GBP`, using the applicable conversion side/charges. For GBP/point
stakes it is `stake * pip increment / contract point increment`: quote-currency
conversion must not be applied again. A display tick need not be a pip. GBP base
principal in GBP/USD is already GBP and must not be reconverted through itself.
These are mathematical normalization rules, not executable price evidence.

Retail major-FX margin and close-out protections fall under FCA COBS 22.5. Margin
is collateral, not maximum loss; the 30:1 ceiling is not a recommended leverage
level. The synthetic model reserves at least the greater of the stated margin
rate and reciprocal leverage ceiling, avoiding underfunding from rounding 3.33%.
Actual tier, account, minimum-margin and discretionary changes still need proof.
[FCA COBS 22.5](https://handbook.fca.org.uk/handbook/cobs22/cobs22s5).

IG's retail tables and OANDA's UK major-FX page publish 3.33%, while CMC's mixed
UK FX page advertises margin from 3.34%. Those are scoped public statements, not
confirmation of a particular stake, tier or account requirement.
[OANDA margin description](https://www.oanda.com/uk-en/trading/spreads-margin/),
[CMC UK FX page](https://www.cmcmarkets.com/en-gb/forex).

| Field | IG | OANDA | CMC |
| --- | --- | --- | --- |
| Ordinary stop | Slippage/gap exposure; no GBP 5 guarantee | Same | Same |
| Guarantee | Premium reserved alongside margin; charged if triggered | v20/v20 MT4 conditions; instrument-specific premium/distance | Premium charged/reserved; refunded if not triggered |
| Current GSLO minimum distance | Unresolved, can change | Unresolved, can change | Unresolved |
| Long/short | Derivative directionality described; actual restrictions unknown | Product/subaccount restrictions require confirmation | Product/platform restrictions require confirmation |
| Required initial cash | Product margin, costs and any premium | Same; platform accounting differs | Same |

Guarantees protect a contractual trigger price only under their applicable terms;
they do not establish a universal GBP maximum after entry costs, commissions or
variable FX conversion. The model separates ordinary gap loss from conditional
guarantee calculations. No guarantee is retroactively applied to old results.
[IG guarantee terms](https://www.ig.com/uk/help-and-support/articles/686687-what-is-a-guaranteed-stop),
[OANDA guarantee FAQ](https://help.oanda.com/uk/en/faqs/guaranteed-stop-loss-orders-gslo.htm),
[CMC charges/guarantee terms](https://www.cmcmarkets.com/en-gb/support/faqs/charges).

## Costs and settlement

Spreads are variable and published minima are not realistic average cost estimates.
Exact pair/size/time-specific spreads remain unresolved. IG's SB agreement
describes spread-based remuneration without commission, with other possible
charges. CFD and SB tariffs must remain separate. OANDA/CMC product-specific
charges are not replaced by another jurisdiction's raw-spread schedule.
[IG SB agreement](https://deal.ig.com/content/files/CustomerAgreement_igi_en_GB.pdf),
[OANDA UK pricing](https://www.oanda.com/uk-en/trading/our-pricing/),
[CMC UK FX offerings](https://www.cmcmarkets.com/en-gb/forex).

IG cash/DFB funding applies at its stated UK cutoff; OANDA uses New York end of
day and tom-next/admin rates. CMC describes instrument-specific holding charges.
The new model computes only an artificial adverse annual charge and day count
to expose costs; it rejects admission of overnight scenarios. It does not claim
to implement provider swaps, holiday settlement or financing calendars.
[IG funding](https://www.ig.com/uk/help-and-support/articles/681712-what-is-overnight-funding-how-is-it-charged-and-how-is-it-calculated),
[OANDA funding](https://www.oanda.com/uk-en/trading/financing-costs/),
[CMC holding charges](https://www.cmcmarkets.com/en-gb/support/faqs/charges).

Broker-specific GBP conversion of P/L, fees, premiums and financing remains
unresolved. OANDA's financing conversion example includes a markup; it is not
automatically a tariff for all trade P/L. GBP stakes remove one conversion step
only when the contract actually settles that stake/P&L in GBP. Illustrative
markups in synthetic fixtures are never described as verified account pricing.

## APIs, history, licences and UK eligibility

| Provider | Public capability | Unresolved or unavailable |
| --- | --- | --- |
| IG | Retail REST/streaming guide explicitly discusses CFDs and spread bets, historical prices and quotas | Exact EPIC, account eligibility, current API/automation terms, retention/reuse rights and required bid/ask history |
| OANDA | Generic v20 API and history described; requires a v20 account | Specific SB entitlement, precision, licence and account acceptance; not transferable to OANDA One |
| CMC | Platform comparison describes MT4 algorithms | Specific SB/MT4 entitlement and binding terms; no verified retail REST SB interface or suitable historical export licence |

No API capability was tested. Reading developer documentation is not permission to
submit even an unauthenticated broker API request. The previously referenced CMC
institutional API URL returned 404 on this review; it is recorded as unavailable,
not used to infer current retail capability. Advertised chart/history depth does
not establish storage, redistribution or automated use rights.
[IG API guide](https://bts.ig.com/uk/trading-platforms/trading-apis/how-to-use-ig-api),
[OANDA v20 introduction](https://developer.oanda.com/rest-live-v20/introduction/),
[CMC platform description](https://www.cmcmarkets.com/en-gb/trading-platforms/metatrader-4/next-generation-vs-metatrader-4).

UK retail marketing does not verify the user's residency, appropriateness,
contracting entity, account subtype or permissions. No such private information
was requested or inspected. All three real product paths therefore remain blocked
for execution. The current task ends with artificial validation and an explicit
contract-evidence backlog, not an account-opening or funding recommendation.
