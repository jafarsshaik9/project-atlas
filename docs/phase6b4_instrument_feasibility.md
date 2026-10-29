# Phase 6B-4 instrument, capital and provider feasibility

Public-documentation review: **29 October 2026**. This is a research feasibility
assessment, not a recommendation to open, fund or trade an account. No account
eligibility, executable quote or automation entitlement was authenticated.
Only currency-pair products are assessed; no stock strategy is proposed.

## Product structures and what GBP 1,000 can mean

| Feature | Spot / rolling Forex exposure | GBP currency-pair spread bet |
| --- | --- | --- |
| Structure | Deliverable spot owns currencies; retail rolling FX is commonly a leveraged OTC derivative | OTC cash-settled derivative with money staked per contractual point; no currency ownership |
| Long/short | A deliverable sale needs owned currency or borrowing; rolling derivatives support signed exposure | Long/short on the provider's own bid/ask contract |
| Size/value | Base-currency units; pip value converted from quote currency into GBP | GBP per contract point; point must be mapped explicitly to the underlying pip |
| Capital | A no-borrowing spot ledger needs actual currency inventory and conversion costs; fully collateralised derivatives need a distinct contract | Margin is collateral for a larger exposure, not maximum loss or trading cost |
| Ordinary stop | May gap/slip; neither deposit nor stop is a maximum-loss guarantee | Same; a separately applicable guaranteed-stop contract has its own premium and restrictions |
| Costs | Spread, possible commission, slippage, conversion, holding and account charges | Spread, slippage, possible guarantee premium, financing and account charges; GBP stake avoids some quote-currency P/L conversion, not all possible fees |
| Hours/data | OTC provider hours, breaks and holiday/DST changes; histories are provider-specific | Provider-specific hours/prices; weekend or forward contracts are different products |
| Automation | Product/platform/account licence must be verified | An API for CFDs does not itself prove spread-bet permission |

The FCA explicitly includes rolling spot FX and spread betting in the retail CFD
product intervention framework. Its rules limit leverage by asset class, require
account-level margin close-out and negative-balance protection. These are consumer
protections, not an endorsement of the product or its profitability.
[FCA sector guidance](https://www.fca.org.uk/firms/contract-for-differences).

For major FX the handbook specifies 3.33% minimum margin; the FCA describes a
30:1 maximum leverage ceiling. Close-out occurs when account net equity falls
below 50% of required margin, as soon as market conditions permit. Protection
against a negative account balance does not cap a trade at GBP 5 or prevent loss
of the GBP 1,000 account. Firms can require more margin. No maximum leverage level
is recommended here.
[COBS 22.5](https://handbook.fca.org.uk/handbook/cobs22/cobs22s5),
[FCA policy statement](https://www.fca.org.uk/publications/policy-statements/ps19-18-restricting-contract-difference-products).

## Public contract evidence and its limits

OANDA's UK minimum-size FAQ specifies **one unit for FX CFDs on its native
platform**, versus **0.01 lot on MT4/MT5**. This is not evidence that every OANDA
spread-bet contract has the same minimum. The calculations interpret a standard
100,000-base-unit FX lot as a 1,000-unit minimum/step illustration; current
instrument/account metadata must confirm it before use.
[OANDA minimum sizes](https://help.oanda.com/uk/en/faqs/minimum-trade-size.htm).

IG's published spot-FX table lists:

| Pair | GBP minimum stake / pip-point | Point movement | Minimum spread, points | Listed guarantee premium, points |
| --- | ---: | ---: | ---: | ---: |
| EUR/USD | 0.50 | 0.0001 | 0.6 | 1.2 |
| GBP/USD | 1.00 | 0.0001 | 0.9 | 2.0 |
| USD/JPY | 0.50 | 0.01 | 0.7 | 1.5 |

These are public-table terms requiring current ticket reconfirmation, not
executable quotes. The displayed historical averages refer to the 12 weeks
ending January 8, 2021 and are **not current average spreads**. The migrated help
page has confusing spot/forward headings; the official `bts.ig.com` spot table is
the reference used for these illustrations. Do not substitute forward/weekend
contracts or fractional display ticks.
[IG spot contract table](https://bts.ig.com/uk/help-and-support/spread-betting-and-cfds/products-markets-and-trading-hours/what-are-igs-forex-spread-bet-product-details),
[migrated help page](https://www.ig.com/uk/help-and-support/articles/681881-forex-spread-betting-product-details).

CMC's public educational page illustrates GBP 0.30/point for all three pairs,
but its FAQ says the actual minimum is instrument-specific and requires the
order ticket. The general statement that some markets start at 1p is not a
currency-pair specification. Treat 0.30 as **unverified current minimum**, not an
available order size. The educational example also contains inconsistent price/
pip arithmetic; none of its profit or margin calculations is copied into Atlas.
[CMC educational size example](https://www.cmcmarkets.com/en-gb/forex/how-much-do-you-need-to-start-trading-forex),
[CMC spread-bet FAQ](https://www.cmcmarkets.com/en-gb/support/faqs/spread-betting).

CMC's current general UK FX page advertises minimum spreads of 0.5/0.4/0.7
for EUR/USD, GBP/USD and USD/JPY and margin from 3.34%. Its FX Active account is
explicitly a CFD product with commission, so its zero-spread headline is not a
commission-free spread-betting promise. The conservative CMC illustrations below
use declared spreads of 0.6/0.9/0.7, not asserted current minima or averages.
Product-specific executable costs remain unknown.
[CMC UK FX products](https://www.cmcmarkets.com/en-gb/forex).

## Deterministic capital illustrations

Inputs and formulas are versioned in
[the calculation specification](../examples/forex-feasibility-phase6b4.json);
[machine-readable outputs](phase6b4_results/capital-calculations.json) include every
5-, 15- and 20-point case. Prices **EURUSD 1.10, GBPUSD 1.30, USDJPY 150** are
arbitrary illustrations, not newly downloaded observations or current quotes.

For a GBP-per-point stake `b`, quote price `P`, point increment `q` and margin
fraction `r`: `notional GBP = b * P / q`, `margin = notional * r`, and an adverse
`d`-point movement costs `b*d`. The illustrations reserve **one full spread plus
0.5-point slippage on each side**, in addition to a mid-reference stop movement:
`planned loss = b*(d + spread + 1)`.

This is a conservative budgeting convention, not a reconstruction of the Phase
6B-3 strategy or an ordinary-stop guarantee. If distance is instead measured from
an already executable entry to the executable stop, the spread must not be added
again. A provider's stake point might differ from a display tick; a JPY pip here
is 0.01, not 0.001.

| Provider illustration | Pair | Stake GBP/point | Margin GBP | Margin / GBP 1,000 | Spread / GBP 5 risk | Largest mid-reference stop under GBP 5 | Loss at 20 points incl. assumed costs |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| IG published table | EUR/USD | 0.50 | 183.33 | 18.33% | 6.0% | 8.40 | 10.80 |
| IG published table | GBP/USD | 1.00 | 433.33 | 43.33% | 18.0% | 3.10 | 21.90 |
| IG published table | USD/JPY | 0.50 | 250.00 | 25.00% | 7.0% | 8.30 | 10.85 |
| CMC unverified size | EUR/USD | 0.30 | 110.22 | 11.02% | 3.6% | 15.07 | 6.48 |
| CMC unverified size | GBP/USD | 0.30 | 130.26 | 13.03% | 5.4% | 14.77 | 6.57 |
| CMC unverified size | USD/JPY | 0.30 | 150.30 | 15.03% | 4.2% | 14.97 | 6.51 |

IG margins use an illustrative exact 1/30 rate rather than rounding 3.33%; CMC
uses the published 3.34%. No tight-stop strategy is recommended to make a product
fit the account. If a defensible hypothesis needs a 20-point stop, **all six
illustrations are unsuitable at these stakes under the GBP 5 planned-risk cap**.
At 15 points CMC EUR/USD is only GBP 4.98, while GBP/USD and USD/JPY are GBP 5.07
and 5.01; these knife-edge budgets are not robust to widening or extra charges.
Minimum allowed stop/guarantee distances and stake increments remain hard gates.

Two hypothetical IG EUR/USD 5-point positions would budget GBP 6.60 combined risk
and GBP 366.67 margin before any premium. That is an arithmetic illustration, not
permission for two same-pair positions. Correlated USD risks cannot be diversified
by labeling them separate pairs. An extra 50-point adverse gap adds GBP 25 to
each 0.50-point stake; the 20-point EUR/USD example then loses GBP 35.80, not GBP 5.
At GBP 1/point a 1,000-point adverse movement alone consumes GBP 1,000. Account
negative-balance protection and liquidation mechanics do not make that safe.

IG says guarantee premiums are charged when triggered and reserved separately
alongside margin. Its minimum guaranteed-stop distance can increase with
volatility. Our conditional guarantee illustration includes the premium, omits
gap/exit slippage only if an applicable guarantee is honored, and makes no claim
that the desired stop can be placed. For GBP/USD, a 3-point mid-reference stop at
GBP 1/point budgets GBP 4.90 without a guarantee but GBP 6.40 with the listed
2-point premium and entry slippage. Protection can itself make a GBP 5 budget
infeasible.
[IG guaranteed-stop terms](https://www.ig.com/uk/help-and-support/articles/686687-what-is-a-guaranteed-stop).

### Unleveraged Forex

Using the illustrative rates, a 20-pip stop, two-pip spread, 0.5-pip/side slippage
and the original research commission convention gives:

| Pair | Native whole-unit capacity | GBP/pip | Collateral GBP | Planned risk GBP | 1,000-unit lot-step capacity |
| --- | ---: | ---: | ---: | ---: | ---: |
| EUR/USD | 1,181 | 0.09085 | 999.31 | 2.2076 | 1,000 |
| GBP/USD | 999 | 0.07685 | 999.00 | 1.8674 | 0: minimum plus costs infeasible |
| USD/JPY | 1,299 | 0.06662 | 999.23 | 1.6621 | 1,000 |

These are size ceilings with a cost reserve, not recommended allocations.
Leaving essentially no operational cash headroom is a model risk; the Phase
6B-3 dynamic notional cap frequently forced early liquidation. A smaller native
unit position can be funded, at proportionally smaller expected gains and losses.
GBP 5 cannot always be allocated without breaching no-borrowing capital limits.
The table is collateral arithmetic for signed exposure, not a deliverable FX
inventory system. Selling currency that is not owned still requires borrowing or
a derivative. No new funding, short-selling facility or leverage is enabled.

## Ranked public-documentation feasibility

Ranking prioritizes documented product-specific automation, usable size information
and research-data compatibility, not advertised return or a deposit recommendation.
For micro-unit FX alone, OANDA has the strongest size/data continuity; for a new
GBP spread-bet specification, IG is the clearest documented API candidate.

| Overall research rank | Provider / UK legal entity evidence | Public capability | Requires account/product verification | Unsupported or unknown |
| --- | --- | --- | --- | --- |
| 1 | IG: spread bets via IG Index Ltd; CFDs via IG Markets Ltd | GBP default account, currency spread bets, demo, retail REST/streaming and historical-price APIs; spread-bet API coverage explicitly described | Current EPIC, minimum/step, permitted stops, size-aware prices, API licence/quotas, available bid/ask history and retention | Tick-level executable history and actual fill latency for this user |
| 2 | OANDA Europe Ltd, public FRN 542574 | FX CFDs, spread-bet-enabled v20/v20 MT4 subaccounts, demo, native one-unit FX CFDs, v20 REST/streaming and historical pricing | GBP subaccount, exact spread-bet stake/value/step, v20 API entitlement for that product, conversion charges and live/declarative guarantees | Treating a CFD unit minimum as verified SB minimum; API support on OANDA One inferred from v20 |
| 3 | CMC Markets UK plc, public FRN 173730 | FX CFDs and spread betting, demo, GBP stake examples, native charts, institutional/partner API documentation | Current pair-specific stake, GBP account, stop distance/GSLO, historical export rights, retail automation permission | Public evidence does not establish a retail Next Generation spread-bet API; institutional FIX and CFD MetaTrader are insufficient |

IG's official API guide explicitly describes both spread bets and CFDs, REST and
streaming, historical access and quotas. Advertised maximum historical depth is
resolution/market dependent and not evidence of two years of executable M1 bid/ask
quotes for a chosen EPIC. IG's UK page identifies separate product entities.
[IG API guide](https://bts.ig.com/uk/trading-platforms/trading-apis/how-to-use-ig-api),
[IG UK API/product disclosures](https://www.ig.com/uk/trading-platforms/trading-apis),
[GBP account default](https://bts.ig.com/uk/help-and-support/accounts-and-statements/my-accounts/how-do-i-change-my-base-currency-and-currency-conversion-setting).

OANDA publicly distinguishes spread-bet-enabled v20/v20 MT4 from OANDA One/MT5,
where spread betting is unavailable. Its generic v20 API documentation requires
a v20 account and describes historical pricing, but that does not independently
verify the precise spread-bet entitlement or contract of a particular subaccount.
Existing OANDA candles remain research inputs, not proof of spread-bet fills.
[OANDA subaccounts](https://www.oanda.com/uk-en/trading/standard-account/differences/),
[v20 documentation](https://developer.oanda.com/rest-live-v20/introduction/),
[OANDA UK entity](https://www.oanda.com/group/where-we-are/).

CMC publishes an institutional FIX/partner API offering. A general institutional
service is not a public retail spread-bet automation commitment. Do not conclude
that automation is either permitted or impossible without the applicable terms.
[CMC API scope](https://www.cmcmarkets.com/en-gb/institutional/cmc-markets-api),
[CMC UK entity disclosure](https://www.cmcmarkets.com/en-gb/forex).

Current broker disclosures were reviewed; the FCA register search returned a
loading/CSS error, so current detailed register permissions were not independently
retrieved. A successful official entity/permissions check remains a prerequisite
before any future account decision. Public FRNs identify firms, not user accounts.
No broker was contacted and no application or API token was requested.

## Costs, hours, protection and UK tax

IG's spread-bet agreement describes spread-based remuneration without commission,
with other possible charges. This supports a potential commission reduction
relative to Atlas's arbitrary GBP 5/100k/side stress, not a cheaper all-in fill.
[IG spread-bet agreement](https://deal.ig.com/content/files/CustomerAgreement_igi_en_GB.pdf).

OANDA advertises commission-free spread betting and Sunday-Friday dealing. Its
pricing page separately quotes FX minima, including EUR/USD from 0.6 and USD/JPY
from 1.3 pips; those floors do not establish spread-bet-specific average costs.
No foreign-division raw-spread commission schedule was applied to UK accounts.
[OANDA spread betting](https://www.oanda.com/uk-en/trading/spread-betting/),
[OANDA UK pricing](https://www.oanda.com/uk-en/trading/our-pricing/).

OANDA's UK financing documentation describes variable tom-next rates plus admin
fees, and a 0.5% conversion markup in its financing conversion example. That
example is not a verified all-product trade-P/L tariff; applicability must be
checked. Atlas's reciprocal bid/ask conversion alone is therefore not a complete
verified broker cost model.
[OANDA financing and conversion example](https://www.oanda.com/uk-en/trading/financing-costs/).

OANDA documents guaranteed stops for v20/v20 MT4, with instrument-specific premiums
and platform restrictions. They were not part of the frozen research and cannot
be retroactively assumed.
[OANDA guaranteed-stop FAQ](https://help.oanda.com/uk/en/faqs/guaranteed-stop-loss-orders-gslo.htm).

IG describes FX funding at tom-next plus administration, with multi-day Wednesday
carry. CMC describes end-of-day holding charges, instrument-specific GSLO charges
refunded when not triggered, and possible account fees. Flattening before the
provider's applicable cutoff is intended to avoid overnight exposure, but exact
timezone, DST, holiday and charge rules must be checked; a fixed UTC close is not
enough. Financing is not assumed to be an intraday strategy advantage.
[IG funding](https://www.ig.com/uk/help-and-support/articles/681712-what-is-overnight-funding-how-is-it-charged-and-how-is-it-calculated),
[CMC charges](https://www.cmcmarkets.com/en-gb/support/faqs/charges).

Slippage may be adverse or favorable in actual execution; an ordinary stop is not
a guaranteed fill at its trigger. IG publishes execution statistics, but these
are aggregate broker marketing evidence, not calibration for Atlas's signal,
size, period or account. The fixed 0.5-pip assumption must not be replaced by a
favorable historical average from another population.
[IG execution disclosure](https://www.ig.com/uk/about-us/best-execution).

HMRC says financial spread bets generally give no chargeable gains or allowable
capital losses. The absence of deductible capital losses matters alongside any
tax exemption. HMRC also requires attention to the contract/economic substance
for income tax: commercial hedges and corporate contracts can be treated
differently. This is not a blanket personal tax determination, and tax treatment
can change. A tax exemption cannot turn a trading loss into a positive edge.
[HMRC CG56105](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg56105),
[HMRC BIM22020](https://www.gov.uk/hmrc-internal-manuals/business-income-manual/bim22020),
[HMRC BIM56900](https://www.gov.uk/hmrc-internal-manuals/business-income-manual/bim56900).

HMRC generally treats retail CFD outcomes under capital gains rules unless they
are taxable trading income, with relevant debits/credits included in the closed
contract computation. Deliverable currency and corporate situations need their
own tax analysis. No account-specific tax advice or allowance/rate assumption is
used in these feasibility numbers.
[HMRC CG56100](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg56100).

## Feasibility conclusion

**REDESIGN RESEARCH.** Native micro-unit collateral arithmetic is feasible at
small economic exposure, but real spot funding/shorts and broker costs are not
established. Some published spread-bet minima fit margin within GBP 1,000 while
failing GBP 5 planned risk for ordinary stop distances. Unknown current minima,
stop restrictions and API/data rights block product acceptance. More leverage
does not create an edge or solve minimum-stake risk.

No product is accepted for trading, no deposit is recommended and no algorithm
deployment is justified. The [proposed next scope](phase6b4_profitability_diagnostics.md)
is specification and offline contract validation under separate authorization.
