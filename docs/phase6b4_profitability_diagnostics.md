# Phase 6B-4 profitability diagnostics and research roadmap

Date: 29 October 2026. **Research recommendation: REDESIGN RESEARCH.**
The evidence supports neither deployment nor a claim that profitable intraday
Forex/spread betting is achievable with GBP 1,000. Small contracts make a limited
research path technically plausible; execution economics and the funding model
must be specified before another hypothesis is tested.

Active Atlas trading research is exclusively Forex and financial spread betting.
Stock code, data, tests and previous results remain preserved legacy infrastructure.
No stock strategy was developed or newly evaluated in this phase.

## Checkpoint and permitted evidence

`main` started clean at `1e132746897af186d420a43ea40706f7895638b0`, matching
`origin/main`. The Phase 6B-1C audit, Phase 6B-2 contract/acceptance, frozen
Phase 6B-3 manifest, all 180 cell statuses and sealed local trade records were
inspected. The starting suite had 1,664 passing tests. No phase checkpoint was
reset or rewritten.

The diagnostic utility reads the saved research SQLite database with `mode=ro`,
checks its exact Phase 6B-3 file hash and summary/row/execution seals, and obtains
entry/exit candle endpoints through the existing read-only, proof-checking source
reader. It buffers one date of source candles and 144 saved session records.
It does **not** call the signal generator, execution engine or historical runner.
All 58,447 saved trades are accounting inputs, not newly generated trades.

The complete comparisons are in [cell/split attribution](phase6b4_results/attribution.csv)
and [exit attribution](phase6b4_results/exits.csv). Each cell identifies pair,
session, rule and cost scenario. All development/validation/holdout periods and
C0-C4 statuses remain present; C4 values remain unavailable. Refer to the
[original scalar performance](phase6b3_results/performance.csv) for every drawdown,
losing streak, holding interval and exclusion denominator. Overlapping cohorts
are separate experiments and must never be added into a portfolio return.

## What the P/L components mean

Recorded amounts are synthetic ledger values, not broker invoices. Recorded
price P/L already includes the assumed fills, bid/ask direction, spread stress
and slippage; subtracting booked entry/exit commissions yields recorded net P/L.

For the same frozen trades, let `u` be units, `q` the pip size, `a` adverse
slippage pips per side, `m` the spread multiplier, and `se`, `sx` the indicative
entry/exit close spreads in price units. The reference accounting decomposition is:

```
base endpoint spread in quote currency = u * (se + sx) / 2
additional spread stress              = base spread * (m - 1)
assumed slippage                      = 2 * u * a * q
neutral reference quote P/L           = recorded quote P/L + the three terms
gross reference GBP                   = floor8(neutral quote P/L * exit midpoint FX rate)
directional conversion effect         = recorded quote P/L * (midpoint rate - booked side rate)
net GBP = gross reference - spread - stress - slippage - conversion
          - settlement/rounding residual - recorded commission
```

USD quote income/obligations use reciprocal GBP/USD ask/bid respectively. JPY
conversion also uses same-frame USD/JPY ask/bid. No historical GBP conversion fee
schedule was available; the engine's bid/ask conversion is not proof of an actual
provider tariff. The residual preserves finite-precision and eight-decimal
settlement rounding instead of disguising it as a spread charge.

Spread/slippage attribution is an **assumed endpoint reference**, particularly
for stop/target exits whose intraminute quote is unknown. It is not a measured
simultaneous intrabar transaction cost. The gross reference retains original
selection, size, exits, costs' effects on barriers/eligibility and capital policy.
It is not an independently observed cost-free market strategy, and does not
answer what trades would have occurred under different costs or timing.

### A fully reconciled example

EUR_USD / London / ORB / C0, final holdout, 108 trades:

| Component | GBP |
| --- | ---: |
| Same-trade gross reference | +3.74419846 |
| Indicative endpoint spread attribution | 12.95096067 |
| Additional spread stress | 0 |
| Assumed adverse slippage | 8.16872843 |
| Directional FX conversion effect | 0.00353818 |
| Settlement/rounding residual | -0.00000004 |
| Recorded assumed commission | 10.93890000 |
| Recorded net P/L | **-28.31792878** |

This example's total reference costs are GBP 32.06212724. Before commission but
after modeled prices/costs, P/L was already -17.37902878. Tiny displayed rounding
differences are reconciled at full Decimal precision in machine-readable output.

## Why the tested rules lost money

The results combine weak/inconsistent same-trade price movement, repeated
transaction friction and a dominant conservative capital restriction. They do
not establish that every conceivable Forex strategy lacks an edge.

| Rule | Development gross-reference GBP range | Validation gross-reference GBP range | Holdout gross-reference GBP range | Positive gross-reference holdout cohorts / 9 |
| --- | ---: | ---: | ---: | ---: |
| ORB | -23.59 to +25.33 | -9.90 to +3.54 | -10.61 to +11.74 | 3 |
| Momentum | -8.82 to +11.47 | -8.94 to +4.92 | -5.54 to +9.34 | 4 |
| Mean reversion | -11.20 to +4.06 | -13.79 to +2.89 | -1.23 to +4.74 | 5 |
| Directional baseline | -13.07 to +16.69 | -8.75 to +8.22 | -4.30 to +5.83 | 5 |

These are C0 ranges across nine independent pair/session cohorts. Only 17 of 36
holdout gross references are positive; 19 are negative even under this limited
reference. Before commission, 35 of 36 holdout cohorts are negative. Removing
commission from saved trades alone would therefore not establish broad
profitability. All 36 net outcomes are negative, and the inherited classifications
remain 27 REJECTED and nine sparse INCONCLUSIVE, with no PROMISING rule.

| C0 holdout grouping | Recorded net GBP range | Positive gross references / 12 |
| --- | ---: | ---: |
| EUR_USD | -31.39 to -6.73 | 5 |
| GBP_USD | -39.70 to -3.01 | 3 |
| USD_JPY | -30.13 to -3.62 | 9 |
| London | -31.74 to -3.62 | 5 |
| New York | -39.68 to -6.82 | 6 |
| Overlap | -39.70 to -3.01 | 6 |

The apparent gross advantage in some JPY cells is descriptive, not an invitation
to select a new strategy/pair using the examined holdout. Original holdout C0
drawdowns range from GBP 5.86 to 39.70, profit factors from 0.21 to 0.79 and
maximum losing streaks from 4 to 22. Negative net expectancy persists across all
original C1-C3 holdout cohorts. Stress changes eligibility, brackets, position
sizes and exits, so cross-scenario terminal differences are not causal estimates
of a single friction's effect. Exact individual components remain separated.

### Capital policy is a material interpretation limit

Across 2,779 C0 holdout trade observations in the separate experiments, mean
reserved capital was **99.9103% of starting session cash** and mean planned risk
was **GBP 1.2125**. 2,775 observations had planned risk below GBP 2.50. The GBP 5
risk budget is a maximum, not the realized allocation. Notional funding is usually
binding before the risk budget.

| Recorded exit reason | C0 holdout count |
| --- | ---: |
| Marked exposure cap | 1,231 |
| Maximum holding duration | 946 |
| Stop | 374 |
| Take profit | 194 |
| Session flatten deadline | 32 |
| Stop-first collision | 2 |

The 1,231 exposure exits are 44.30% of these observations and their mean recorded
net P/L is approximately -GBP 0.548. Near-total reservation plus the requirement
that current notional remain below liquidation equity gives very little
headroom. Price/conversion changes and liquidation costs can force a close before
the strategy's stop, target or holding deadline. This is intentional inherited
policy, not a verified broker margin model or a newly discovered source defect.
No restriction was relaxed, and no counterfactual trade path was generated.

Stop/target/deadline buckets describe which existing trades contributed losses;
they do not prove that changing exit rules improves performance. Mean reversion
also remains too sparse for the frozen rejection/promising sample thresholds.

## Assessment of the execution assumptions

| Assumption | Assessment | Required treatment |
| --- | --- | --- |
| Completed-candle causal signals and subsequent execution | Necessary information safety | Preserve separate cutoff, eligibility and fill timestamps |
| Next eligible candle close | Defensible coarse model; may help or hurt, not uniformly conservative | Actual signal/publication/order timestamps required for a different timing model; C4 remains blocked |
| Zero publication delay | Unsupported historical availability assumption; potentially optimistic | Obtain timestamp semantics before latency-sensitive claims |
| Long ask/bid, short bid/ask | Necessary directionality | Preserve; midpoint fills remain prohibited |
| Candle-close spreads applied to intrabar barriers | Unsupported executable-spread assumption | Matched bid/ask quotes or an independently justified conservative bound needed |
| Fixed adverse slippage and assumed GBP 5/100k/side commission | Conservative stress conventions, uncalibrated | Verify product tariffs and execution distribution; do not remove costs to rescue results |
| Stop-first collisions; no favorable target gap improvement | Conservative and defensible | Retain ambiguity flags; alternative ordering needs finer data, not profitable hindsight |
| Missing-data invalidation | Necessary protection against unknown fills | Never forward-fill or assign zero P/L to unresolved positions |
| Strict flatten/rollover limits | Necessary intraday scope, conservatively early | Provider-specific hours/holiday/DST and executable flat quotes still required |
| Current directional GBP conversion | Sound internal accounting convention, incomplete real cost model | Verify broker conversion markup and settlement; GBP stake products differ |
| Almost fully reserved notional with dynamic exposure liquidation | Severe, measurable funding-policy effect | Distinguish deliverable currency inventory, fully collateralised derivatives and retail margin; independently specify headroom before any future test |
| No depth, quote-size, rejection or fill history | Can be insufficiently conservative | Do not equate small-unit arithmetic with available liquidity or accepted execution |

No exact causal P/L attribution to the one-minute delay is possible from recorded
outcomes. There are no actual order acknowledgments or alternative timing runs.
The two C0 holdout collision observations are too few to explain all losses, but
the count alone cannot establish an alternative fill path. No holdout trade
touches the seven known extreme observations; their absence does not remove the
corpus's missing-minute or executable-price uncertainties.

## Prospective research design, not an implementation authorization

The examined 2024-2026 period remains useful for software regression and descriptive
diagnostics. It cannot be described as untouched OOS for hypotheses now designed.
Walk-forward reuse can examine stability but cannot restore that lost independence.

The proposed next phase is **broker/product specification and offline execution
contract redesign only**, separately authorized. Required prerequisites:

1. Choose a legal product/account/platform; verify GBP denomination, stake/unit
   minimum and step, point definition, minimum stop distance, margin tiers,
   close-out, charges, automation licence and historical-data rights. Unknown
   fields block that product. Do not create or fund an account to bypass the gate.
2. Specify currency ownership/funding for genuine unleveraged spot. A synthetic
   signed short is not a deliverable, borrowing-free currency sale. If studying
   fully collateralised derivatives, define that explicitly and prove funding
   headroom/invariants on artificial cases. Leveraged execution remains disabled.
3. Specify price publication, receipt and order latency, contingent stop activation,
   quote-size eligibility and missing-quote behavior. Any future data collection
   requires separate authorization; none is initiated here.
4. Preregister at most two economic hypotheses and a baseline, with fixed pair/
   session family, cost cases, endpoints and stopping rules before prospective
   evidence. Potential hypotheses are lower-turnover intraday continuation and
   liquidity-conditioned reversion; no parameter, indicator implementation or
   historical test is supplied. Do not infer an edge from cheaper fees alone.
5. Reserve a genuinely later chronological evaluation interval after registration,
   preferably at least six months, at least 100 completed trades and 26 calendar
   weeks per primary cohort. Extend collection time rather than lowering sample
   requirements or shifting split boundaries after outcomes. Keep positions within
   both session and split; frozen walk-forward folds may not overlap evaluation.
6. Use week/block resampling jointly across correlated pairs/sessions, with
   sensitivity to longer dependence blocks. Freeze the testing family and apply
   a family-wise correction such as Holm to primary claims. Require positive net
   expectancy with uncertainty support after broker-specific base and stressed
   costs, not only a profitable point estimate or a best-cell ranking.
7. Predefine regime checks using contemporaneously available metadata, invalidation
   denominators, exceptional-trade concentration, GBP 100 marked drawdown halt,
   GBP 5/10 planned trade/aggregate risk, no overnight exposure, no averaging down,
   martingale, deposits or scaling to recover losses. Ordinary stops do not bound
   gap losses at the planned risk amount. Unknown capital remains unresolved.
8. Independent acceptance must verify funding, bid/ask/time semantics, cost
   provenance, all exclusions, sealed results and source preservation before any
   positive research classification. Retain negative results and failed hypotheses.

If suitable contract size, automation rights or defensible execution data cannot
be verified, the decision becomes **PAUSE FOREX**. CONTINUE FOREX RESEARCH is not
the current recommendation. See [instrument feasibility](phase6b4_instrument_feasibility.md)
and [acceptance](phase6b4_acceptance.md). No Phase 6B-5 work is begun.
