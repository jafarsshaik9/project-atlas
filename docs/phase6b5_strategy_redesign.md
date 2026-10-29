# Phase 6B-5 strategy redesign: evidence and preregistration proposals

**Hypotheses only. No new strategy, parameter search or historical backtest.**
Active scope is Forex and financial spread betting. Existing stock research remains
preserved, and no equity strategy is developed or evaluated here. The Phase 6B-3
holdout has been examined and cannot be reused as fresh evidence for these ideas.

## Findings that can and cannot be transferred

| Structural issue | Existing evidence | Interpretation boundary |
| --- | --- | --- |
| Spread/slippage sensitivity | Every original stressed-cost holdout cohort lost money | Costs changed selection, sizing and exits; cross-scenario differences are not pure causal fee attribution |
| Commission | 35/36 C0 holdout cohorts lost before the arbitrary research commission | Removing it alone does not establish an edge |
| Low captured movement | 19/36 same-trade holdout gross references negative, 17 positive | Reference is not a cost-free strategy; weak/inconsistent capture, not proof that all FX signals fail |
| Turnover | Rules allow one entry per session; recurrent costs are material | Excessive turnover relative to opportunity is a hypothesis, not a demonstrated optimal frequency |
| Capital reservation | Mean 99.91% reservation and GBP 1.21 planned risk; 44.3% exposure exits | Funding/notional policy materially shapes the sample; changing it is not automatically profitable |
| Exits | Many exposure/holding exits; stops and targets contribute differently | Saved buckets do not identify a superior exit rule or intrabar path |
| Stop placement | Some published SB minima cannot fit ordinary stop distances within GBP 5 | Do not tighten stops merely to fit broker stake sizes |
| Session/timing | London, NY and overlap are correlated cohorts; close-based entry and zero assumed publication delay | No verified best session or exact delay attribution; C4 remained blocked |
| Signal quality | No original rule met the frozen PROMISING criteria | No persistent edge established; cannot isolate signal quality from the accepted execution/funding model |

These are the accepted [Phase 6B-4 diagnostics](phase6b4_profitability_diagnostics.md),
not a new decomposition or a rerun. London ORB specifically used a complete
15-minute range, a completed-close breakout, next-close entry, opposite-boundary
stop and 1.5R target. Its loss does not justify an opportunistic alternative range
length, target or session chosen on the same exposed period.

### US gap approach: keep its provenance straight

The [Phase 2B report](phase2b_report.md) records an **artificial XNAS equity gap
fixture**, not an empirical Forex strategy: GBP -29.23 versus GBP 4.48 planned
risk and GBP 8.23 stressed risk. It demonstrates that gaps can exceed both planning
figures. It supplies no evidence that a US gap strategy works on currency pairs.
The prior Forex matrix contained ORB, momentum, mean reversion and a directional
baseline, not a new US equity gap experiment.

FX's OTC weekday market and broker session boundaries differ from a US cash-equity
opening auction. A session discontinuity hypothesis would need its own economic
definition, broker prices and timestamps; no such strategy is implemented here.
Existing stock code/results are preserved. Holding a position across an unknown
gap or overnight remains prohibited, regardless of a fixture's name.

## Candidate directions

The following six concepts are a backlog, **not six authorized experiments or a
parameter grid**. At most two primary hypotheses plus a baseline should be chosen
and frozen for a later, separately authorized study using independent evidence.
Do not choose them because of a favorable pair/session cell in the old holdout.

| Candidate | Economic rationale / failure addressed | Trade-offs and required data | Complexity / overfitting risk | Preregistered criterion and invalidation |
| --- | --- | --- | --- | --- |
| Lower-frequency intraday continuation | Seek larger moves relative to repeated spreads and close-entry delay | Fewer observations; longer intraday exposure; matched quote/latency evidence and sufficient later sessions | Medium; horizon selection creates researcher degrees of freedom | Freeze one horizon and entry limit before new evaluation; invalidate if net expectancy remains nonpositive under verified base/stress costs or sample is insufficient |
| Expected-move versus cost gate | Refuse opportunities whose conservatively estimated move is dominated by all-in friction | Requires a strictly past-only movement estimator, broker tariff/size and quote-cost inputs; may reject most sessions | Medium/high; forecast calibration can overfit | Freeze estimator/training windows and ratio rule before OOS; invalidate if admitted opportunities do not outperform the predefined no-gate reference after costs or coverage becomes too sparse |
| Volatility-adjusted range/stop planning | Scale risk distance to observable volatility rather than shrinking stops to satisfy a minimum stake | Smaller quantity or more rejections in volatile periods; requires complete past windows and independently verified stop/step rules | Medium; volatility window and multiplier selection are risks | Freeze one rule and size/funding constraints; invalidate on unstable risk allocation, infeasible minima or failure of prospective net-risk criteria |
| Liquidity/session eligibility | Avoid wide-spread or uncertain-availability periods using information actually available at decision time | Reduces sample; needs synchronized quote timestamps, provider hours and published-event availability | Medium/high; selecting profitable hours retrospectively is prohibited | Freeze eligible windows and spread metrics from contract/economic reasoning; invalidate if benefits vanish after costs or depend on an ex-post session exclusion |
| Capital-aware opportunity rejection | Size on the broker grid under independent funding, planned risk and stress budgets | May reduce risk below GBP 5 or reject all trades; needs exact contract and settlement/rounding evidence | Low/medium engineering complexity; policy comparisons can bias selection | Preserve no-borrowing or explicitly scoped derivative funding; invalidate on any funding breach, hidden risk ratchet or dependence on relaxed stress limits |
| Intraday time-exit redesign | Avoid positions whose predicted opportunity cannot outlast entry delay/cost within the permitted session | Late opportunities rejected; may miss recoveries; requires correct publication/execution timing and flat quotes | Medium/high; selecting exit duration on old profits is overfitting | Freeze duration/deadline behavior and adverse gap policy; invalidate if apparent improvement needs unknown intrabar ordering, overnight carry or ignored invalidations |

These criteria specify what must be frozen and falsified, not tuned values. No
historical parameter was adjusted to populate this table. Larger expected moves,
lower turnover, more selectivity or cheaper fees are not themselves evidence of
predictability or positive expectancy.

## Prospective evidence and selection discipline

Before observing another evaluation interval:

1. Resolve one specific product contract and data/automation licence. Missing
   quantity, margin, stop, fee, conversion or timing fields block that product.
   Artificial funding feasibility is necessary engineering evidence, not sufficient
   execution realism or an authorization to use leverage.
2. Freeze the small hypothesis family, exact formulas, parameters, currencies,
   sessions, cost/stress cases, baseline and all exclusions. Freeze one primary
   metric per claim; report the entire family and negative results.
3. Define genuinely later chronological OOS after registration, preferably at
   least six months, 100 completed trades and 26 calendar weeks per primary cohort.
   Extend observation time rather than lowering the sample threshold after results.
   Existing 2024-2026 data may support regression/development only, with its exposure
   disclosed; walk-forward reuse does not make it untouched again.
4. Training windows may precede each frozen walk-forward test fold only. Include
   all signal publication/receipt/execution delays; no position crosses a fold,
   session or flat boundary. Broker-specific spread, commission, slippage, currency
   conversion and rejection assumptions must be fixed before each evaluation.
5. Assess uncertainty using joint calendar-block resampling across correlated
   pairs/sessions, with predeclared longer-block sensitivity. Apply a family-wise
   procedure such as Holm to primary claims; no best-cell significance claim.
6. Require positive net expectancy with uncertainty support under base and stressed
   costs, bounded marked drawdown, low disclosed invalidation exposure and no
   exceptional-trade dominance. Freeze numerical thresholds before evaluation;
   retain GBP 5/10 planned risk, GBP 100 research drawdown/stress guard and no
   overnight, martingale, averaging down, recovery deposits or profit-based scaling.
7. Invalidate rather than assign convenient P/L to unknown exits. Ordinary stops
   do not bound realized loss; artificial gap stress is not a worst-case bound.
   Independent acceptance must inspect funding, timing, costs and exclusions before
   any positive research classification.

No strategy is promoted. The current recommendation is **REVISE CONTRACT MODEL**:
complete verified real-product inputs and execution/data rights before another
research authorization. If those cannot be resolved at appropriate stakes/costs,
STOP is defensible. Phase 6B-6 is not started.
