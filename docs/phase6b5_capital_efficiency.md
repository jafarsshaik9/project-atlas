# Phase 6B-5 GBP 1,000 capital feasibility and reservation

**Artificial scenarios only. No margin/leverage facility or trading is enabled.**
The new module is `atlas.forex.feasibility`; its only shared dependency is the
existing immutable contract/Decimal/digest infrastructure. It neither imports an
execution engine nor reads prices, broker configuration or historical databases.

Inputs: [versioned fixtures](../examples/forex-capital-phase6b5.json).
Outputs: [allocation comparisons](phase6b5_results/allocation-summary.csv) and
[contract estimates](phase6b5_results/contract-estimates.csv). Detailed local
results and byte/source seals are under `local/verification/phase6b5/run4/`.

## Explanation of the old observations

The accepted Phase 6B-4 diagnostics found mean reservation of **99.9103%**, mean
planned risk **GBP 1.2125**, and **1,231 exposure exits in 2,779 C0 holdout trade
observations** across separate, statistically correlated experiments. No historical
strategy or attribution was rerun in this phase.

The original entry rule takes the smaller of risk-limited size and nearly all
available full-notional funding, allowing for explicit round-trip costs. For many
small stop distances, cash becomes binding well before GBP 5 planned risk.
The engine then compares marked notional with current liquidation equity; changes
in price, conversion and liquidation cost can trigger early closure. This combines
a near-total reservation policy with a dynamic notional ceiling, not a verified
broker margin requirement. Source inspection found intentional contract behavior,
not evidence justifying rewriting the historical outcomes.

Actual derivative margin is different from full-notional collateral, while actual
deliverable FX needs owned currencies and settlement. A signed short with cash
collateral does not establish a borrowing-free physical currency sale. The new
static model refuses deliverable inventory admission and leaves the old execution
engine unchanged. An exposure cap remains a risk policy even if it is not margin.

## Contract and accounting model

`InstrumentContract` is frozen and distinguishes base units, lots with an explicit
unit multiplier, and GBP-per-point stakes. It requires minimum quantity, grid step,
point increment, stop minimum, fee basis/currency/minimum, conversion markup and
direction/guarantee information. Derivatives also require margin and a maximum
retail leverage ceiling. Guaranteed and overnight scenarios need their extra terms.
Missing essential fields produce `BLOCKED_MISSING_SPEC` without fabricated zeros.

All £5 trade/£10 aggregate budgets use fixed initial GBP 1,000 capital. They do
not rise after profits. Portfolio capacity is at most three positions, at most
one per pair; single-position comparisons cap the same requests at one position.
Priority is fixed scenario identity, never profitability or a retrospective ranking.

| Quantity convention | Principal / point value |
| --- | --- |
| Base units | Base quantity is supplied units |
| Lots | Base units = lots times the explicit contract multiplier |
| Quote-currency point P/L | Units times point increment, converted to GBP on the obligation side plus explicit markup |
| GBP stake | GBP value/point is the stake, without another USD/JPY conversion |
| GBP/USD base principal | Base GBP units are already GBP; never reconvert them through their own quote |

The reference USD-per-GBP and JPY-per-GBP quotes are supplied artificial inputs,
not downloaded observations. GBP/USD reference and conversion quotes must agree;
simultaneous positions must use one consistent quote for each conversion currency.
JPY pip size is 0.01; another contract point/display tick can have a different
increment, so pip value is `point value * pip increment / point increment`.

For the stated **mid-reference adverse move** `d`, point value `v`, spread `s`,
entry/exit slippage `ae/ax`, commission `C` and reserved guarantee premium `G`:

```
ordinary planned risk = v*(d+s+ae+ax) + C + financing
conditional guaranteed planned risk = v*(d+s+ae) + C + G + financing
ordinary stress loss = v_stress*(d+additional_gap+stressed_spread+ae+ax)
                       + stressed_commission + stressed_financing
```

Distances are budgeting movements, not a claimed broker order-price API. An actual
entry-relative executable stop must not add spread again. Commission includes
explicit per-side minimums. Conversion markup components are disclosed subsets of
costs already included, not extra charges to add a second time. Guarantees omit
gap/exit slippage only conditionally on the artificial guarantee; variable GBP
conversion and other costs can still increase the loss.

Funding/fee amounts use fixed 40-digit Decimal arithmetic; current broker rounding,
cash settlement, quote size and minimum order restrictions remain unverified.
No exact maximum realized loss is returned. The GBP 5 figure is a planning target,
and the GBP 100 stress budget is a scenario guard, not a real loss guarantee.

## Four funding policies

| Policy | Required reservation | Role |
| --- | --- | --- |
| FULL_NOTIONAL | Gross notional plus explicit round-trip costs/premium | Conservative collateral comparator, not proof of physical FX funding |
| MARGIN_ONLY | Verified margin plus explicit costs/premium | Minimum-capital comparator; can fail badly in a gap |
| MARGIN_STRESS | Greater of initial/stressed margin plus modeled stress loss | Buffers costs, losses, FX and higher margin; enforces GBP 100 aggregate stress budget |
| RISK_FUNDED | Same margin/stress constraint, with size reduced to the minimum/step grid | Jointly enforces GBP 5/10 risk, GBP 1,000 funding and GBP 100 stress limits |

Margin is at least the greater of the stated rate and reciprocal verified maximum
leverage, so rounded 3.33% cannot imply leverage above 30. Unknown margin evidence
cannot unlock a margin-based policy. Fixture verification is explicitly artificial;
it never creates verified broker access.

RISK_FUNDED's bounded grid search solves monotone accounting constraints at a fixed
risk budget and requested size ceiling. It is position sizing on artificial inputs,
not strategy optimization. The other policies compare fixed requests and reject
oversized requests; their feasible-position counts are not directly interchangeable
with a resizing policy's counts.

Reservation is an encumbrance, not a second loss. At the stress mark:
`equity = initial equity - modeled stress loss`, and
`free margin = equity - stressed margin`. Do not deduct the reserved margin again
from equity. Costs and premium already included in stress loss are reserved once.
The model reports the 50% account-margin threshold indicator but cannot simulate
actual broker liquidation timing or negative-balance protection. Gross shock losses
are not truncated to GBP 5, margin or account balance.

## Measured artificial results

The final matrix contains **24 fixed books x four policies x two position limits =
192 cases**, each repeated identically. Artificial reference prices include EUR/USD
1.10, GBP/USD 1.30 and USD/JPY 150. Contract fees, steps, guarantee terms and 3.4%
margin are explicit assumptions, not IG/OANDA/CMC executable specifications.

Reference case: 1,000 EUR units, USD-per-GBP bid 1.25, 20-point stop, two-point
spread, 0.5 slippage on each side, GBP 0.00005/unit/side commission, 50-point
additional gap and five-point stressed spread:

| Policy | Admitted | Notional GBP | Reserved GBP | Capital used | Planned risk GBP | Stress loss GBP |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full notional | 1 | 880 | 880.34 | 88.034% | 1.94 | 6.18 |
| Margin only | 1 | 880 | 30.26 | 3.026% | 1.94 | 6.18 |
| Margin plus stress | 1 | 880 | 36.10 | 3.610% | 1.94 | 6.18 |
| Risk funded | 1 | 880 | 36.10 | 3.610% | 1.94 | 6.18 |

Identical size has identical planned and stress loss; freeing reservation does not
create profit or reduce market exposure. The buffered policies leave GBP 963.90
available, but it cannot all be spent without rechecking aggregate risk/stress.

For a larger requested ceiling, RISK_FUNDED admits 2,577 EUR units: notional
GBP 2,267.76, reservation GBP 93.0297, planned risk GBP 4.99938 and modeled stress
GBP 15.92586. This is hypothetical derivative margin arithmetic, not newly permitted
leveraged trading. Three distinct-pair requests can be jointly sized to roughly
GBP 10 planned risk and GBP 202.46 reservation; one-position mode admits only the
first eligible request. Every remainder and rejection is in the result matrix.

GBP 0.50/point stake example, five-point move, 0.6 spread and 0.5 slippage/side:

| Policy | Admitted | Reservation GBP | Planned risk GBP | Stress loss GBP |
| --- | ---: | ---: | ---: | ---: |
| Full notional | 0 | Required amount exceeds GBP 1,000 | 3.30 requested | 30.50 requested |
| Margin only | 1 | 187.80 | 3.30 | 30.50 |
| Margin plus stress | 1 | 217.50 | 3.30 | 30.50 |
| Risk funded | 1 | 217.50 | 3.30 | 30.50 |

At a 20-point move, that minimum stake requires GBP 10.80 planned loss and is
rejected under every policy. Margin availability cannot rescue an excessive
minimum stake. With the artificial guarantee, planned/stress cost is GBP 3.65;
the premium is reserved once, and buffered reservation is GBP 190.65. A two-point
stop fails the artificial guarantee minimum rather than being treated as protected.

In a deliberately extreme short-side 20,000-point additional gap, the unchanged
1,000-unit position has GBP 1.94 planned risk but **GBP 1,602.18 gross shock loss**.
Full-notional and minimum-margin comparators can admit it; buffered admission
rejects it. Risk funding reduces to 62 units: planned GBP 0.12028, stress GBP
99.33516 and reservation GBP 104.5663728. The shock is a mathematical tail test,
not a market forecast; even the stress guard cannot guarantee losses stay below
GBP 100 in a different shock.

Higher artificial margin from 3.4% to 10% increases buffered reservation for the
reference case from GBP 36.10 to 93.62. FX stress, spread widening, adverse slippage,
wide stops, minimum/step failures, insufficient cash and aggregate constraints
are also covered. Overnight charges are computed diagnostically where rates exist,
but every overnight admission is rejected; unknown rates remain unknown.

## Policy conclusion and integration boundary

Do not choose a policy because it admits more trades. Margin-only is a baseline
with inadequate stress protection. Full-notional alone does not cap short/gap risk.
Margin-plus-stress and independently funded risk sizing are coherent artificial
candidates because they preserve separate funding and risk constraints, reserve
premiums/costs once and reject large modeled exposures. They are not adopted by
the existing execution engine.

Before integration, actual broker specifications, margin tiers/rounding, direction
and quantity semantics, fees, guarantee terms, settlement and automation/data rights
must be verified. Execution timing and market-data gaps still require a separately
accepted contract. Deliverable inventory, netting/hedging, actual close-out,
cross-margin and broker liquidity remain unsupported. No historical profitability
claim follows from these static feasibility calculations.
