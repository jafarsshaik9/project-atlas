# Atlas development roadmap — after Phase 2A

Phase 2C checkpoint (27 October 2026): local authenticated dashboard, versioned
SQLite event history and opt-in Telegram outbox are implemented in synthetic-only
mode after the Phase 2B acceptance gate passed. See [phase2c_report.md](phase2c_report.md).
No continuous scanner, real-data provider, ledger-resume mechanism or broker sender
was added. Real Telegram delivery and actual phone/Wi-Fi reachability require
user-local verification. Further phases still require separate authorization.

Date: 26 October 2026. The approved Phase 2B offline foundation is implemented:
contracts, fixture sessions, synthetic UK/US data, costs, portfolio accounting,
independent risk, ORB v1 and chronological replay with CLI reports. No dependency,
external provider connection or broker order capability was added. The working
Trading 212 integration stays intact. Profitability remains unestablished.

Read [claude_trader_evaluation.md](claude_trader_evaluation.md) for source-backed
reuse decisions and [atlas_architecture.md](atlas_architecture.md) for contracts
and safety boundaries.

## Phase 2A outcome

Both user-specified repositories were inspected at pinned revisions. Each combines
Claude instructions with standalone Python tools. The day-trading pack supplies
the more relevant scanning and planning ideas; the original pack adds allocation
and portfolio-maintenance examples. Neither supplies an Atlas-ready Trading 212
adapter, LSE/FX model, net-GBP cost ranker or walk-forward framework.

Recommendation: adapt small, attributed research ideas after approval, rather
than install either skill pack. Build independent deterministic risk and portfolio
accounting. Reject upstream broker/MCP workflows and US tax assumptions for the
Atlas path. No strategy is approved for trading by this evaluation.

## Priorities and exit criteria

| Priority / milestone | Proposed scope | Evidence needed before proceeding |
| --- | --- | --- |
| P0 — Phase 2B approval and scope | Implemented offline with GBP 1,000 and configurable 0.5% per-trade, 1% daily and 1% concurrent planned-risk settings | No authority for external feeds, live execution or Practice orders follows from Phase 2B |
| P0 — Domain contracts and synthetic data | Instrument/currency units, UTC/availability clocks, typed bars/quotes, missing-data states and immutable run manifests | LSE pence/pounds, USD/GBP conversion, DST/half-day, duplicate/missing/incomplete bar and stale/future-time cases pass offline tests |
| P0 — Data-provider decision | Compare authorized LSE/US intraday history, quotes/spreads, adjustment policy, retention, latency, quotas and total cost | Documented source terms and affordable coverage; small representative samples establish quality; no provider assumed selected |
| P0 — Independent costing and virtual ledger | Cash/exposure accounting, parameterized fees/FX/taxes, quantity rounding, reservations and conservative fills | Hand-calculated round-trip cases reconcile; no spread double counting; unknown costs reject eligibility; concurrent proposals cannot overspend |
| P0 — Independent risk policies | Pure fail-closed checks for capital, data, supported assets, stressed loss and aggregate state | Deterministic rejection tests; stale/missing policy/state cannot pass; AI cannot alter policy or create an approval |
| P1 — One complete strategy | Versioned long-only ORB candidate, explicit entry/exit/time-stop rules and data requirements | Synthetic edge cases and no-future-data checks; no trades is a valid result; hypothesis is stated before evaluation |
| P1 — Backtest and walk-forward | Historical clock, next-actionable fills, costs, chronological folds, variant registry and untouched holdout | Reproducible fold artifacts; training-only fitting; no overlapping-label leakage; reports show net outcomes, drawdown, uncertainty and cost stress |
| P1 — Opportunity discovery and ranking | Coverage-aware scanner, net-GBP expectancy with feasible quantities, allocation and final independent risk decision | Ordering reconciles to cost calculations; all rejected/zero candidates remain visible with reason codes; no budget-filling objective |
| P2 — Multiple strategies | Add a separately specified second hypothesis only after the evaluation harness is reliable | Per-strategy validation before combination; correlated signals do not count as independent diversification; shared capital limits hold |
| P2 — Local paper/shadow observation | Virtual fills against validated data; compare predicted costs and observed market conditions; retain demo account-summary observation | Restart/replay determinism, reconciled virtual ledger, drift monitoring and redacted audit logs; no broker orders |
| P3 — Optional advisory/news | Publication-time event ingestion, deduplication and schema-bound AI research notes | Injection tests, provenance, zero access to credentials/order tools, and pipeline functioning with AI disabled |
| P3 — Forex paper research | Separate pair identities, units, sessions, conversion, bid/ask and funding/rollover assumptions | FX-specific dataset rights, cost/fill tests and virtual ledger; do not route through Invest API by assumption |
| Later, separate approval — Practice execution study | Verify broker capabilities and proposed intent lifecycle in documentation and mocks before considering a sender | New explicit authorization; broker-specific sizing/order/exit support, reconciliation and kill-switch criteria met |
| Later, separate approval — ISA compatibility | Read-only account-type and instrument compatibility study, followed by its own design decision | Dedicated official API/account validation; no inference from Invest Practice success; no live orders authorized |

These are dependency-ordered milestones, not calendar promises. Feasibility,
data access and research evidence determine whether a later milestone is useful.

## Phase 2B delivered scope

The offline implementation now provides:

1. Validated instrument, bar, quote, clock, signal, portfolio and risk contracts.
2. Original LSE/US datasets, an exported OHLCV/quote bundle and reproducible manifests.
3. Effective-dated synthetic costs and full GBP 1,000 simulated cash/position
   accounting, reservations, partial fills, fees/FX and reconciliation.
4. Independent fail-closed risk recomputation and atomic reservation.
5. One deterministic ORB hypothesis, chronological replay, offline commands,
   provenance, rejection reasons, quality outcomes and risk/audit reports.

The capital/settings update is implemented in `src/atlas/research.py`, with
original synthetic scenarios in `tests/fixtures/research_scenarios.json`.
The default opening cash is GBP 1,000. Planned risk per trade is 0.5% (GBP 5),
daily loss threshold 1% (GBP 10), and maximum concurrent planned risk 1% (GBP 10).
They are configurable percentages of initial capital, not the broker's Practice
balance. These provisional research settings are neither proven optimal trading
parameters nor guaranteed loss limits. Arithmetic, simulated transactions,
deterministic enforcement and failure cases are tested. Real-data suitability,
production reliability and economic validation remain unproven.

The implementation retains clear skip/rejection reasons, reproducible original
fixtures, automated tests, lint and existing Phase 1 behavior. Outputs state
synthetic research only. No paid feed, external service setup, third-party code
execution, new broker endpoint or order method is included.
Selecting real historical data and performing walk-forward research follows its
own data decision; synthetic success does not establish economic profitability.

## Reuse backlog

| Candidate | Suggested treatment | Conditions |
| --- | --- | --- |
| ORB and completed-bar concepts | Adapt into an original Atlas strategy specification | Pinned evidence and attribution for any actual port; independent reference cases |
| Allocation caps and risk-per-share sizing | Use as mathematical design references | GBP/FX units, quantity increments, costs and aggregate state replace upstream assumptions |
| Reproducible seed and conservative ambiguous-bar cases | Reuse workflow/test ideas | Add block/regime sensitivity, executable fills and holdout evidence |
| Fundamental/news workflows | Defer to advisory research | Point-in-time data, UK sources and provenance; no direct trading authority |
| Full skill-pack installation, order connectors, same-day JSON PASS gate | Reject for Atlas | No permission to install or execute these workflows |
| US tax policy, copying investor/congress trades as an immediate intraday edge | Exclude from initial scope | Separate jurisdiction/account and hypothesis review would be required |

## Recommended next phase

Approve a read-only market-data feasibility and validation phase: compare
authorized historical LSE/US coverage and retention, replace fixture calendars
with a reviewed maintained calendar adapter, and validate actual cost applicability.
Begin with a small point-in-time sample and contract tests. Add a combined
multi-instrument replay and chronological holdout/walk-forward harness only after
those inputs are reliable. No broker order sender is needed for this work.

The current fixture calendar, instantaneous GBP conversion, one-instrument runner,
OHLC fill assumptions and in-memory ledger are explicit limitations. Details are
in [backtesting_assumptions.md](backtesting_assumptions.md). A successful artificial
scenario does not justify skipping data licensing, holdout evidence or safety checks.

If a future port copies source, record repository/revision, retained MIT notice,
files/functions changed, dependency provenance and independent regression cases.
Review updates as new third-party changes; do not auto-sync upstream code.

## Research promotion gates

Before labeling a strategy suitable for paper evaluation, record its complete
hypothesis, parameter search space, universe/timeframe, cost assumptions and
promotion criteria. Set the criteria before looking at final holdout results.
The minimum sufficient sample and allowable drawdown remain decisions to make,
not universal constants supplied by this roadmap.

An evaluation must include chronological out-of-sample folds, independent final
holdout, net expectancy, uncertainty, drawdown/tail behavior, exposure, turnover,
capacity/rounding effects and stressed costs. Record unsuccessful variants too.
Reject or retain as research-only when evidence is weak or cost sensitivity
removes the edge. More Monte Carlo draws cannot cure inadequate historical data.

Before any future broker write capability, require a separately approved design
for supported broker semantics, fresh authoritative state, idempotency, atomic
cash/risk reservations, final risk rechecks, partial fills, reconciliation,
uncertain outcomes and operational shutdown. Human approval alone is insufficient
to bypass any deterministic risk rule.

## Decisions still needed

| Question | Why it blocks a later decision |
| --- | --- |
| Start with a small US universe, LSE universe, or parallel data validation? | Determines dataset/calendar requirements and whether first results transfer across markets |
| What is the monthly data budget, permitted use and retention policy? | Free/unofficial endpoints have not been selected as reliable infrastructure |
| What interval and holding horizon should the first hypothesis use? | Defines availability rules, costs, sample size and realistic fill model |
| Which effective-dated account/instrument costs apply? | Net profitability cannot be assessed without them |
| How should stressed losses and later risk enforcement use the provisional settings? | The initial GBP 1,000, 0.5% per-trade, 1% daily and 1% concurrent settings are configured, but their suitability is unproven and actual losses can exceed planned amounts |
| What market-data and later broker quantity/tick constraints apply? | Determines whether a candidate is practically affordable |
| What evaluation history, holdout and statistical evidence are sufficient? | Controls overfitting and premature strategy promotion |
| How much historical listing/corporate-action data is available? | Controls survivorship and adjustment bias |
| What is advisory AI allowed to read? | Determines privacy, provenance and injection boundaries |
| Which FX paper conventions and future ISA validation are desired? | Both require separate scope from equities and Invest Practice |

Phase 2B is local simulation only. Subsequent phases require their own scope;
no connector, Practice order or live broker order was enabled.
