# Phase 6B-4 independent acceptance

Date: 29 October 2026. **Decision: PASS for the completed diagnostic and public
feasibility assessment. Research recommendation: REDESIGN RESEARCH.**
This accepts reproducible analysis and disclosed unknowns; it does not accept a
broker/product for trading, establish profitability or authorize leverage.

## Scope and implementation

Starting checkpoint: `main`, clean, `1e132746897af186d420a43ea40706f7895638b0`.
The expected Phase 6B-3 checkpoint, PASS receipt, frozen manifest, all 180 statuses
and trade-level database were present. The prior research has zero promising
strategies and its final holdout is already exposed.

Only a pure `atlas.forex.diagnostics` arithmetic module, a read-only diagnostic
script, 28 new tests, explicit hypothetical contract inputs and documentation/
compact derived summaries were added. README now identifies Forex and financial
spread betting as the exclusive active research scope. Existing stock/Forex code,
execution/risk/calendar models, historical readers and previous results were not
modified. No dependencies, integrations, live order paths or new strategy
backtesting capabilities were added.

The script buffers one UTC date and reads source partitions using the existing
read-only proof checker. It reads no `.env`, account ID or API token. It imports
neither execution/research runners nor broker clients. Import boundaries are
tested. New automated cases use artificial numeric inputs; the complete legacy
suite remains a regression check, not a new historical stock/Forex experiment.

## Independent adversarial review

This is a separate accounting/code-review pass by the same implementation agent,
not external human certification. It challenges arithmetic, source preservation,
claims and unknown capability boundaries rather than searching for profitable
parameters.

| Finding / attack | Resolution or limitation |
| --- | --- |
| Treat gross reference as a cost-free strategy | Explicitly label the fixed-trade endpoint accounting convention; selection, size, barriers and exposure exits remain cost-dependent |
| Remove commission and claim an edge | 35/36 C0 holdout cohorts were already negative before commission; no re-execution or strategy promotion |
| Attribute performance difference across C0-C3 to spread alone | Publish per-cell components and warn that eligibility, size and exits differ; no causal counterfactual claim |
| Ignore capital-policy forced exits | Disclose 1,231/2,779 C0 holdout exposure exits, near-total cash reservation and GBP 1.21 mean planned risk |
| Apply a 1,000-unit minimum but permit arbitrary whole-unit MT4 size | Initial capital utility corrected to floor to the configured 1,000-unit step; deterministic regression added |
| Treat a guarantee premium as spendable cash until triggered | Initial capital illustration corrected to reserve the premium alongside margin; separate cash-feasibility regression |
| Treat a stop, margin or negative-balance rule as a GBP 5 loss cap | Uncapped adverse-gap illustration and conditional guarantee costs; leveraged loss test exceeds GBP 1,000 before account-level protections |
| Wrong bid/ask GBP conversion or JPY pip | Both directions, signs and three pairs reconcile against exact synthetic source rates; JPY point/margin test and original source settlement checks |
| Use advertised spreads/minima as current executable inputs | Every source and illustration distinguishes public table, educational example, current ticket requirement and unknown entitlement |
| Infer retail spread-bet API from institutional/CFD API | IG has explicit public spread-bet API documentation; OANDA exact entitlement requires verification; CMC retail SB API is not established |
| Treat current broker self-disclosure as an independently retrieved FCA permissions record | Register lookup failed to render; exact entity/permissions verification remains a future gate |
| Reuse exposed holdout as fresh evidence | No new hypothesis was run; prospective design requires genuinely later preregistered evaluation |
| Mutate old data/results or hide a historical rerun | Before/after inventories, exact result database hash, sealed input records and import review passed |

The initial diagnostic `run1` is preserved. After the two capital-utility fixes,
`run2` retained **identical trade-attribution gzip bytes, cell components and exit
buckets**. The corrections affect only hypothetical contract calculations; they
do not change a Phase 6B-3 trade, equity curve, metric, strategy parameter or result.
There is no new holdout strategy evaluation or profitability claim.

## Validation and preservation

- Full final suite: **1,692 passed**, zero failed/errors/skips, **284.53 seconds**.
  All 28 new arithmetic/adversarial cases pass. The 653 existing Starlette/pandas
  warnings remain unchanged.
- Ruff lint passes; all **197 Python files** pass formatting; `pip check` reports
  no broken requirements. No existing test was removed or relaxed.
- Both read-only attribution passes verify **105,264 session rows**, attribute
  **58,447 saved trades**, and account for all **540 cell/split status rows**.
  1,545 source partitions containing traded dates were proof-checked; all frozen
  source files are additionally covered by preservation hashes.
- Separate 50-digit reconciliation confirms each published component sum against
  frozen gross, precommission, commission and net metrics within `1e-25` GBP.
  The two passes' trade details and exit summaries match exactly. Hypothetical
  capital outputs repeat deterministically at the final version.
- All **5,216 readable protected artifacts** match the pre-phase inventory. The
  same two older Massive reports remain unreadable and outside hash verification.
  All **28 Phase 6B-3 local verification files**, including its full result database,
  are unchanged. No accepted source candle, overlay or stock result was rewritten.
- No authenticated broker/API access, real/demo orders, new market-history
  downloads, account changes, deployment, leverage activation or Telegram messages
  occurred. Public broker, FCA and HMRC documentation pages were accessed.

## Evidence disposition

Committed evidence:

- [Loss attribution, execution review and prospective roadmap](phase6b4_profitability_diagnostics.md).
- [Product, broker, capital, regulatory and tax assessment](phase6b4_instrument_feasibility.md).
- [Versioned calculation inputs](../examples/forex-feasibility-phase6b4.json).
- [All 540 attribution rows](phase6b4_results/attribution.csv) and
  [exit buckets](phase6b4_results/exits.csv).
- [Capital calculations](phase6b4_results/capital-calculations.json),
  [source references and verification status](phase6b4_results/sources.json), and
  [acceptance summary/seals](phase6b4_results/acceptance-summary.json).

Local-only, Git-ignored evidence under `local/verification/phase6b4/` includes both
diagnostic runs, compressed per-trade components, source/code/input seals, full
JSON outputs, original/final test XML, preservation inventories, independent-review
receipt, acceptance receipt and final Git checkpoint. The source candle databases,
Phase 6B-3 evidence, `.env`, credentials and raw trade records are not staged.

Reproduction of the final diagnostic uses a fresh derived output directory:

```powershell
.venv/Scripts/python.exe scripts/forex_profitability_diagnostics.py `
  --output local/verification/phase6b4/reproduction
```

This command recomputes attribution from saved trades; it never regenerates
signals or executes a historical strategy. Output overwrite is rejected. Current
broker terms can change; public contract assumptions must be reverified before
they are reused for another phase.

## Recommendation and outstanding gates

**REDESIGN RESEARCH**, with actual trading and new historical strategy evaluation
paused. Native micro-unit arithmetic leaves a plausible technical research route;
the current funding policy materially constrains the original results. Spread
betting may remove the arbitrary research commission and simplify GBP P/L, but
does not establish cheaper executable costs or an edge. Published minimum stakes
can make a defensible stop exceed GBP 5 risk even where margin fits GBP 1,000.

The exact proposed next scope is public/product specification and artificial-only
contract validation: verified account/product identity, minimum/step/stop rules,
GBP settlement/fees, automation/data rights, and a defensible funding/latency model.
No product is accepted until those gates pass. A later strategy study additionally
requires separately authorized fresh data, preregistration, dependence-aware
multiple-testing controls and genuinely untouched chronological evidence.

No account funding, stock research, leveraged execution, overnight exposure,
deployment or new strategy optimization is authorized. **Phase 6B-5 has not been
started.** No additional user information is required to complete this diagnostic
phase; later product/account decisions need the missing contract evidence.
