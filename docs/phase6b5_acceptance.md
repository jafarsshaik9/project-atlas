# Phase 6B-5 acceptance and independent review

Date: 29 October 2026. **Decision: PASS for offline contract/capital modeling and
evidence-aware strategy redesign. Recommendation: REVISE CONTRACT MODEL.**
No executable broker contract, profitable strategy, leveraged facility or trading
authorization is established. Public contract evidence must be completed before a
new strategy study. Phase 6B-6 is not started.

## Starting checkpoint and preserved scope

Repository: `main`, initially clean at
`fe27a6850d19b56a3aa1f257a796323a768b81c5`, matching local `origin/main`.
The remote is checked again before the authorized checkpoint. Phase 6B-4's
diagnostics, feasibility, acceptance and local result seals were present and read,
along with relevant execution/risk architecture and legacy strategy provenance.

Baseline: **1,692 passed**, zero failures/skips, **735.36 seconds**; 653 existing
Starlette/pandas warnings. The longer baseline duration is an observed run time,
not a change in test scope. Protected data and Phase 6B-3/4 verification files were
inventoried before implementation. No reset, worktree replacement or overwrite of
unrelated changes was needed.

## Deliverables and boundaries

- [Broker matrix](phase6b5_broker_contract_matrix.md): 192 source-dated findings,
  explicit product/platform scope and verified/illustrative/inferred/unresolved
  classifications. A public capability does not prove an executable contract.
- [Capital comparison](phase6b5_capital_efficiency.md): four policies, separate
  planned risk, margin, notional, costs, stress and unencumbered cash.
- [Strategy redesign](phase6b5_strategy_redesign.md): six economic hypotheses,
  failure criteria and prospective validation discipline, without implementation
  or parameter fitting.
- [Next-phase roadmap](phase6b5_next_phase_roadmap.md): precise unresolved gates
  and a separate authorization boundary.
- `atlas.forex.feasibility`: immutable typed inputs, missing-field gates, quantity
  normalization, costs/GBP conversion, static allocation and adverse stress.
- `scripts/forex_capital_feasibility.py`: reads only the committed artificial
  fixture, writes a new local verification directory, repeats results exactly and
  seals source/input/result bytes. It opens no historical store or broker client.

The prior diagnostics, execution, strategy, calendar, risk, dashboard, Telegram,
broker transport and accepted historical readers are unchanged. README only updates
the active research summary and links; stock code/data/results remain preserved.
No package dependency or broker integration was added.

## Independent critical review

This is a separate adversarial pass by the same implementation agent, not external
human certification. The additional acceptance tests are separate from the ordinary
model tests; a high-precision reconciliation independently checks saved outcomes.

| Challenge/finding | Resolution / evidence |
| --- | --- |
| Margin capacity can admit a catastrophic artificial gap | Buffered policies now have an independent GBP 100 aggregate stress guard; margin-only/full-notional comparators retain visible tail failures |
| Risk sizing could bypass funding | Bounded grid sizing requires fixed GBP 5/10 risk, actual verified margin, cost/stress reserve and GBP 1,000 funding simultaneously |
| Multiple identities could bypass pair constraints | One position per pair, global 1/3-position policy, fixed identity priority; no favorable netting |
| GBP base principal re-converted through its own quote | GBP/USD principal remains GBP units; inconsistent reference/conversion inputs are rejected; dedicated regression |
| Simultaneous positions could use conflicting GBP conversion rates | One consistent quote per conversion currency is enforced; multi-pair fixture quotes corrected and regression added |
| Caller Decimal context could alter results | Fixed 40-digit context and rounding; adversarial low-precision/ROUND_UP caller yields identical results |
| Confusion between lots, units, stakes and display ticks | Explicit multiplier/minimum/step; separate GBP stake and JPY pip conventions; incompatible normalization rejected |
| Double subtraction of margin or premiums | Reservations encumber capital, not equity; stress loss already includes costs/premium; independent GBP 217.50 reservation and GBP 783.435 free-margin check |
| Tiny sizes evade commission minimums | Per-side fee minimum enforced before currency conversion; infeasible GBP 6 round-trip minimum cannot fit GBP 5 risk |
| Stop, guarantee or negative-balance rule presented as maximum loss | Ordinary raw shock loss can exceed capital; guarantees are conditional and FX can change GBP loss; maximum realized loss remains unknown |
| Unknown fee, margin or financing defaults to zero | Essential missing fields block assessment; optional overnight scenario needs explicit rates and is never admitted |
| Marketing/API descriptions become executable rights | Every output is non-executable; public/artificial verification cannot be interchanged; entitlement/licensing remain separate gates |
| Reuse of stock/US gap evidence as Forex performance | Old US gap was an artificial equity fixture; lesson is gap-risk failure, with no new stock strategy or Forex edge claim |
| Hidden historical optimization or holdout use | New runtime imports no execution/research/provider module; artificial matrix runs with network/database construction forbidden in acceptance tests |

Earlier artificial `run1`, `run2` and `run3` and earlier suite XML remain local
evidence. Corrections changed only the new offline model/fixtures before acceptance;
the final matrix is `run4`. No historical trade, source price, strategy parameter,
result or holdout was recomputed or modified. Final artificial reproduction has
identical full results and hashes.

## Final verification

The final run covers **24 artificial books x four funding policies x two position
limits = 192 cases**. Sixty-five new parameterized/adversarial tests cover contracts,
steps, pip values, margin, cash, spread/slippage/fees, conversion, guarantee premium,
ordinary gaps, simultaneous positions, stress limits, missing/unverified fields,
overnight rejection, frozen inputs and determinism.

Final post-review regression suite: **1,757 passed**, zero failures, errors or
skips, **265.83 seconds**, with the same 653 existing warnings. All 65 new tests
pass. Ruff lint passes, Ruff reports **207 files** passing formatting, and `pip check`
reports no broken requirements. No existing test was removed, relaxed or skipped;
no new dependency was introduced. Earlier pre-review suite XML remains separate.

Preservation checks matched all **5,216 readable protected artifacts** and all
**45 Phase 6B-3/4 local verification files**, with no changes or additions inside
the protected source/research trees. The two previously unreadable old Massive
reports remain explicitly outside byte-hash verification. All readable stock and
Forex databases/results are preserved. This phase's new local evidence is stored
outside those protected trees.

## Evidence and reproducibility

Committed compact evidence under `docs/phase6b5_results/`:

- [Field-level broker evidence](phase6b5_results/broker-findings.json).
- [All allocation cases](phase6b5_results/allocation-summary.csv).
- [All individual contract estimates](phase6b5_results/contract-estimates.csv).
- [Input/source/result seal](phase6b5_results/artificial-result-seal.json).
- [Acceptance summary](phase6b5_results/acceptance-summary.json).

The [fixture JSON](../examples/forex-capital-phase6b5.json) uses explicit artificial
values/evidence, with one partial public IG contract used to demonstrate blocking.
Its bytes are preserved by a narrow `.gitattributes` rule. Detailed results,
preservation inventories, independent review, suite XML and Git checkpoint are
local-only under `local/verification/phase6b5/` and are not staged. No historical
dataset, raw prior research result, `.env`, credential or private account value is
committed.

```powershell
.venv/Scripts/python.exe scripts/forex_capital_feasibility.py `
  --output local/verification/phase6b5/reproduction
```

The destination must be new. The command performs static artificial calculations
twice; it is not a historical backtest or an order simulator. Current production
contract interpretation cannot be inferred from a passing artificial fixture.

## Acceptance conclusion

The contract model is defensible as deterministic **artificial-only funding
analysis** with missing values and permissions kept visible. The public provider
investigation narrows some size/platform questions but does not close all binding
contract fields. Unverified contracts remain blocked or illustrative and never
executable. A technically successful phase does not establish profitability.

Recommendation: **REVISE CONTRACT MODEL**, specifically complete verified real
product specifications, fee/settlement precision, funding, execution/latency and
data/automation rights. Then seek a separate review/authorization decision. Do not
advance merely because margin permits more positions. If suitable contracts and
data cannot be established at viable costs/size, STOP remains appropriate.

No broker API requests (authenticated or unauthenticated), paper/demo/live trades,
market-history downloads, credential/private-account access, funding or account
changes, leverage activation, historical strategy runs/optimization, overnight
positions or Telegram notifications occurred. Only public documentation pages
were accessed. Phase 6B-6 is not started.
