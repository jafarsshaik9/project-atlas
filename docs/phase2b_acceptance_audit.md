# Phase 2B acceptance audit and remediation

**Current assessment (2026-10-27): PASS for the bounded offline Phase 2B scope.**
F-01, F-02 and F-03 are remediated. All **292 tests pass**, including the three
original acceptance regressions and all 40 original broker tests. Ruff and both
offline research commands pass. No Phase 2C work, broker requests or orders
were performed. This is not a profitability finding or production approval.

The original audit below remains historical evidence of the defects at backup
checkpoint `74b175323455c7a688448c7f95952adad74e3591`. Its FAIL/PARTIAL
dispositions and command output describe that checkpoint, not the remediated
working tree. Current evidence and limitations are recorded here.

## Remediation assessment

| Category | Current status | Evidence and limits |
| --- | --- | --- |
| A. Accounting | PASS | Cash, basis, positions, fees and realized/unrealized P&L reconcile; interleaved partial fills/exits cannot reopen an unbudgeted remainder. |
| B. Risk | PASS | Full conservative planned/stressed envelope remains until flat; unknown risk prevents entries. Existing per-trade/daily/aggregate, duplicate, kill-switch and affordability tests pass. |
| C. Strategy/replay | PASS | Available post-entry stop quotes are honoured, simultaneous adverse evidence takes priority, replay stays deterministic, and calendar content is fingerprinted. |
| D. Market correctness | PARTIAL | Synthetic UK/US DST, holidays/half-days and units/FX tests pass. Authoritative exchange coverage and real broker costs remain outside this phase. |
| E. Safety | PASS for offline boundaries | No network dependency or broker execution added; unchanged read-only demo broker and network-blocking CLI tests pass. Authenticated demo connectivity was intentionally NOT VERIFIED. |

### F-01: partial-fill risk accounting — CLOSED

**Root cause:** all planned/stressed risk moved from the reservation to the first
partial fill. A full or proportional exit could release that risk while the
zero-risk entry remainder remained fillable.

**Correction:** [exit_fill](../src/atlas/portfolio.py#L378) cancels the entry
remainder on the first exit, including a partial exit. The consumed intent cannot
reopen the position. Partial exits release monetary basis and cash normally but
retain the complete approved planned/stressed risk envelope until flat. This is
a deliberately conservative bound, not a proportional minimum-risk estimate;
fixed remaining exit charges make proportional risk release unsafe without
re-costing. [Reservation validation](../src/atlas/portfolio.py#L191) rejects zero
planned risk or stress below planned risk. [Reconciliation](../src/atlas/portfolio.py#L137)
flags missing position/orphaned reservation risk, and entry fills check consistency
before changing cash. The independent risk engine already rejects inconsistent
snapshots; its approval interface was not changed.

**Evidence:** the original [F-01 regression](../tests/test_phase2b_acceptance.py#L20)
passes by safely rejecting the cancelled remainder. The
[new partial-fill/exit test](../tests/test_phase2b_remediation.py#L32) uses two
entries, a partial exit, a rejected late remainder and a final exit: final cash
and equity GBP 999.86, realized P&L GBP -0.14, fees GBP 0.04, zero final risk and
successful reconciliation. [Unknown/inconsistent risk tests](../tests/test_phase2b_remediation.py#L91)
verify atomic rejection and that corrupted risk state blocks new authorization.

### F-02: stop-breaching quote ignored — CLOSED

**Root cause:** quotes marked positions and supplied time exits only. The entire
entry candle was excluded from OHLC exits, so a known post-entry adverse quote
inside it could be ignored before a later target fill.

**Correction:** [eligible exit quotes](../src/atlas/backtest.py#L124) require
matching identity/unit, raw adjustment, complete good-quality tick-valid prices,
freshness, receipt by the decision time and an event strictly after entry.
A stop quote exits at its bid with executable-price costs and adequate full-size
bid liquidity; bids below the stop are not raised to the stop price. At equal
availability times, [event ordering](../src/atlas/backtest.py#L138) handles
eligible stop-breaching quotes for an existing position before bar/time exits.
Only that already-available group is examined, not future groups.

A stop is latched when liquidity is insufficient. Later bars cannot create a
target win or invent a stop fill; the runner awaits an eligible quote with
enough bid size. A recovered quote is conservatively capped at the stop, while
a worse bid remains a worse fill. Missing liquidity leaves an explicitly
unliquidated position. Later eligible OHLC ambiguity remains stop-first.

**Evidence:** the original [F-02 regression](../tests/test_phase2b_acceptance.py#L76)
now exits at **08:20:02 UTC / 245 GBX**, below the 250 GBX stop, with negative net
P&L. Added [gap/cost/determinism](../tests/test_phase2b_remediation.py#L171),
[ineligible quote](../tests/test_phase2b_remediation.py#L194),
[simultaneous stop](../tests/test_phase2b_remediation.py#L205) and
[illiquid latched-stop](../tests/test_phase2b_remediation.py#L227) tests pass.
The existing next-actionable-entry, delayed-bar, future-data-independence,
gap, ambiguous-bar, no-trade and time-exit tests also pass.

### F-03: calendar configuration fingerprint — CLOSED

**Root cause:** configuration serialization omitted the injected calendar despite
its ability to change a session from open to closed.

**Correction:** [SessionCalendar.provenance](../src/atlas/sessions.py#L34) is now
part of the interface. [FixtureCalendar](../src/atlas/sessions.py#L60) exposes a
canonical manifest sorted by venue/date. It records UTC opening/closing times,
timezone names and offsets, half-day flags, explicit holidays/closed dates,
schema/version and the reject-unknown-date policy. Closed dates and absent
unknown dates differ. The report hashes the full manifest with its other
configuration and the updated fill-model version. Missing provenance fails
closed with `CALENDAR_PROVENANCE_UNAVAILABLE` rather than producing an incomplete
fingerprint.

**Evidence:** the original [F-03 regression](../tests/test_phase2b_acceptance.py#L102)
passes. Added [equivalent entry-order](../tests/test_phase2b_remediation.py#L241),
[session assumptions](../tests/test_phase2b_remediation.py#L261),
[holiday versus unknown](../tests/test_phase2b_remediation.py#L278), and
[missing provenance](../tests/test_phase2b_remediation.py#L288) tests pass.
Equivalent manifest contents/versions produce identical fingerprints regardless
of entry order; material calendar assumptions change them without changing the
dataset checksum.

## Remediation verification commands and results

Working directory: `D:\Jafar's Work\project-atlas`. All tools use the existing
virtual environment. No dependency was installed or upgraded.

| Exact command | Exit | Final result |
| --- | --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q tests\test_phase2b_acceptance.py tests\test_phase2b_remediation.py` | 0 | 26 passed in 0.21s: 3 original regressions + 23 additional focused cases |
| `.\.venv\Scripts\python.exe -m pytest -q` | 0 | **292 passed in 2.03s**, no skips/xfails |
| `.\.venv\Scripts\python.exe -m pytest -q tests\test_broker.py` | 0 | **40 passed in 0.44s** |
| `.\.venv\Scripts\python.exe -m ruff check .` | 0 | All checks passed |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 0 | 31 files already formatted |
| `.\.venv\Scripts\python.exe -m atlas.main research-check` | 0 | GBP 1,000 virtual capital; GBP 5 / 10 / 10 planned/daily/concurrent budgets; no leverage/shorts/broker execution |
| `.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest` | 0 | Default synthetic XLON: 1 trade; gross GBP 4.90, costs GBP 0.34, net GBP 4.56; final equity GBP 1,004.56; no open position; reconciled |

**SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE**

Default dataset SHA-256 remains
`73e70678a73326078377c21dbee8b76ac072056f1c6766b241546d61a17f08b8`.
The updated calendar/fill-model configuration SHA-256 is
`f2cb154dd96a1a111ffd421d4ec91d352ed46446883e7e56e79ac7f30a8bbb6e`.

Existing scenario-wide deterministic replay and accounting tests pass. The new
quote-stop replay is also repeated and compared as a complete report. The
existing offline CLI tests prohibit broker credential reads and block socket/
HTTPX access for both research commands. AST import review of the ten offline
research modules found no broker or network imports.

Local Git comparison against checkpoint `74b1753` confirms no changes to
`broker.py`, `config.py`, `main.py`, `risk.py`, `broker-check.ps1`, `pyproject.toml`,
`test_broker.py` or the three original acceptance tests. No credentials/private
configuration were read, no broker connection was attempted, and no live or
Practice execution was added or used.

## Remediation files and remaining limits

Modified:

- `src/atlas/portfolio.py` — risk invariants, fail-closed entry checks, remainder
  cancellation on exit and conservative risk retention until flat.
- `src/atlas/backtest.py` — chronological quote stops, conservative simultaneous
  event handling, illiquid-stop latch and calendar/fill-model provenance.
- `src/atlas/sessions.py` — deterministic complete fixture-calendar provenance.
- `docs/backtesting_assumptions.md` — precise updated risk/fill/provenance policies.
- `docs/phase2b_acceptance_audit.md` — this evidence and retained historical audit.

Added `tests/test_phase2b_remediation.py`: 23 parameterized cases. The original
269 tests, including all three acceptance regressions, remain unchanged.

Risk retention can overreserve the minimum exposure of a reduced position;
re-costing/releasing that envelope requires a separately reviewed policy.
Quote exits require full-size bid liquidity, and a latched stop can remain
unliquidated; queueing, partial quote exits and execution market impact remain
unmodelled. Recovery-capped fills are conservative synthetic assumptions.
Calendars/fees/FX remain artificial; replay is single-instrument/session with
instant GBP conversion. Matching fingerprints also requires the same application
source and truthful deterministic provenance from any future calendar adapter.
These existing research limits do not authorize real-data use or broker execution.
No Phase 2C implementation was begun.

---

## Historical audit evidence (2026-10-26; superseded above)

The following is the original audit record. Source/test links are pinned to
the audited backup commit; commands, counts and findings below are historical.



Audit date: 2026-10-26. Workspace: `D:\Jafar's Work\project-atlas`.
Interpreter: project `.venv\Scripts\python.exe`, Python 3.14.10.

**Original audit assessment: FAIL — Phase 2B is not accepted for progression to Phase 2C.**

The original 266 tests pass, but independent adversarial checks exposed three
material acceptance gaps. Three focused regression tests were added; the final
suite reports **3 failed, 266 passed**. Ruff lint and formatting pass.
Production code, existing tests, broker configuration and dependencies were
not changed. The failures are recorded, not suppressed with skips or xfail.

The earlier [Phase 2B report](phase2b_report.md) describes the implementation
completion baseline. This audit supersedes that report's acceptance implications.
No strategy profitability conclusion follows from synthetic results.

## Scope and method

Reviewed the completion report, [backtesting assumptions](backtesting_assumptions.md),
[README](../README.md), approved architecture/roadmap, actual research and broker
source, fixtures, example configuration and existing tests. Traced ledger
transactions, risk authorization, strategy visibility and replay event handling.
Used original synthetic data and local, deterministic adversarial variations.
No broker requests, external market data, software installation or third-party
trading code execution occurred. Private configuration contents were not read.

Status meanings:

- **PASS:** the stated bounded acceptance check is supported by source and executed tests.
- **FAIL:** a reproduced counterexample violates the criterion.
- **PARTIAL:** working portions exist, but a defect or stated scope limit prevents full acceptance.
- **NOT VERIFIED:** evidence was intentionally unavailable or outside this offline audit.

## Commands and observed results

All commands below ran from the workspace above, using the project virtual
environment. Running tools as Python modules ensures the intended interpreter
and installed project tools are used.

| Exact command | Exit | Actual result |
| --- | --- | --- |
| `.\.venv\Scripts\python.exe --version` | 0 | Python 3.14.10 |
| `.\.venv\Scripts\python.exe -m pytest -q`, before adding regressions | 0 | 266 passed in 1.20s |
| `.\.venv\Scripts\python.exe -m ruff check .` | 0 | All checks passed |
| `.\.venv\Scripts\python.exe -m ruff format --check .`, baseline | 0 | 28 files already formatted |
| `.\.venv\Scripts\python.exe -m atlas.main research-check` | 0 | Offline configuration check passed; GBP 1,000; GBP 5 / 10 / 10 risk budgets |
| `.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest` | 0 | Default XLON synthetic replay completed and reconciled; sample below |
| `.\.venv\Scripts\python.exe -m pytest -q tests\test_phase2b_acceptance.py` | 1 | 3 failed in 0.11s on initial regression run |
| `.\.venv\Scripts\python.exe -m pytest -q`, final full run | 1 | **3 failed, 266 passed in 1.08s** |
| `.\.venv\Scripts\python.exe -m ruff check .`, final | 0 | All checks passed |
| `.\.venv\Scripts\python.exe -m ruff format --check .`, after regressions | 0 | 29 files already formatted |
| `.\.venv\Scripts\python.exe -m ruff format --check .`, after audit report | 0 | **30 files already formatted** |
| `.\.venv\Scripts\python.exe -m pytest -q tests\test_broker.py` | 0 | **40 passed in 0.30s** |

The research commands were also rerun after adding regressions and both returned
exit 0 with the same displayed results. Successful CLI exit codes do not override
the acceptance failures. Ruff includes Markdown in its reported formatting file
count; adding this report explains the final increase from 29 to 30.

Only the new regression file was formatted during this audit, using
`.\.venv\Scripts\python.exe -m ruff format tests\test_phase2b_acceptance.py`.
A focused Ruff check/fix was also used while authoring that file; existing
source and tests remain byte-for-byte unchanged.

Additional verification:

- `& 'C:\Program Files\Git\cmd\git.exe' check-ignore --no-index .env .env.private .venv/test logs/test.log local/research/trader-skill/README.md`:
  exit 0; every listed private/runtime path was ignored.
- `& 'C:\Program Files\Git\cmd\git.exe' ls-files`: exit 0; no tracked files.
- `& 'C:\Program Files\Git\cmd\git.exe' status --short`: exit 0; project files are untracked.
- `& 'C:\Program Files\Git\cmd\git.exe' rev-parse --verify HEAD`:
  no committed HEAD. An initial invocation of bare `git` failed because Git is
  absent from the tool shell's PATH; using the existing executable resolved it.
  No PATH or system settings were changed.
- Local Python here-string probes, executed with
  `@' ... '@ | .\.venv\Scripts\python.exe -`, inspected AST imports, public text
  credential patterns, file existence and log-file names. Results and scan
  boundaries are recorded under Safety below.
- `Get-FileHash -LiteralPath src\atlas\broker.py,src\atlas\config.py,scripts\broker-check.ps1,pyproject.toml,tests\test_broker.py -Algorithm SHA256`
  matched the retained earlier Phase 2B protection baseline for all five files.
  SHA-256 checks of all pre-existing Python source/tests also matched this
  audit's initial baseline. No committed Git baseline exists for independent
  historical comparison.

## Acceptance matrix

### A. Accounting — PARTIAL

| Criterion | Status | Evidence and limits |
| --- | --- | --- |
| Configurable GBP 1,000 capital, independent of Practice balance | PASS | [ResearchSettings](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/research.py#L14); [default budgets](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_research.py#L27), [broker balance independence](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_research.py#L89), [parameterized replay](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L289). |
| Cash, positions, basis, equity and realized/unrealized P&L | PASS | [reconciliation/snapshot](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/portfolio.py#L137); [partial fills and average cost](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L45), [daily equity accounting](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L373), [scenario reconciliation](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L60). Cash plus remaining basis equals initial capital plus realized P&L; equity uses marked positions. |
| No overspending, negative cash, short fills or spending unconfirmed sales | PASS | [reserve](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/portfolio.py#L174), [entry fill](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/portfolio.py#L252); [atomic failure tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L107), [unconfirmed sales/shorts](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L132), [concurrent authorization](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L289). |
| Partial fills and costs | PARTIAL | Monetary basis, fee budgets and cancellation accounting pass existing tests, including [fill bounds](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L324). Risk attached to an interleaved partial-fill sequence is lost: **F-01**. |
| GBP/GBX conversion and USD conversion | PASS | [QuoteUnit.major](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/domain.py#L59); [GBX units](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L16), [independent USD calculation](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L191). GBX divides by 100 before GBP arithmetic. |
| Decimal costs and spread charged once | PASS | [CostEngine](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/costs.py#L68); [hand-calculated costs](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L157), [spread double counting](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L174), [FX expense attribution](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L309). |

The cash-by-currency implementation uses immediate conversion to GBP and zero
retained USD cash. It does not implement native USD cash retention, settlement
delays or multi-day FX revaluation; these limits are explicitly documented.

### B. Risk — FAIL

| Criterion | Status | Evidence and limits |
| --- | --- | --- |
| 0.5% planned per trade; 1% daily; 1% concurrent defaults | PASS | [ResearchSettings](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/research.py#L14), [default-budget test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_research.py#L27), executed research-check. Initial fixed-capital budgets are GBP 5 / 10 / 10. |
| Per-trade sizing, cash and capital-allocation enforcement | PASS | [RiskEngine.evaluate](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/risk.py#L75); [risk-limit cases](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L188), [fill price/fee bounds](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L324). |
| Aggregate concurrent planned-risk enforcement across supported fill sequences | FAIL | Ordinary reservations pass [aggregate test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L193), but **F-01** creates an open position with zero recorded risk. |
| Daily realized/unrealized losses block new risk | PASS | [daily-loss tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L199). Negative realized/unrealized contributions are summed without offsetting losses with the other component's gains. New planned risk must also fit the remaining daily budget. UTC daily boundary is a research policy. |
| Stale/future/missing data and invalid quotes fail closed for entries | PASS | [domain quote validation](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/domain.py#L177), [risk quote checks](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/risk.py#L146); [data tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L220), [bid below stop](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L405). Existing-position quote-stop handling separately fails **F-02**. |
| Unknown costs/FX, invalid quantities, unsupported instruments | PASS | [missing-cost/FX tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L234), [asset/short/session checks](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L242), [cost schedule validation](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L205). |
| Duplicate intents and inconsistent/stale snapshots rejected | PASS | [duplicate/snapshot tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L262), [snapshot-version test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L388); structural reconciliation does not detect F-01's missing risk. |
| Kill switch prevents new entry authorization | PASS | [kill-switch test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L242); [authorize](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/risk.py#L225) independently evaluates policy before reserving. This is an authorization gate, not a claim that changing the switch revokes every previously authorized in-flight ledger operation. |
| Strategies/AI/manual approval flags cannot approve risk | PASS | [approval-interface test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L281). Accounting primitives remain internal simulation tools, not a sandbox against arbitrary Python access. |
| Planned stop and stressed loss are separate; gaps can exceed both | PASS | [stress test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_portfolio_risk.py#L350), [gap replay test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L112): exit at 235 GBX versus planned 250 GBX stop; daily policy event recorded. F-02 prevents accepting exit handling generally. |

All percentages are provisional software research settings, not validated
optimal parameters or guaranteed loss limits.

### C. Strategy and replay — FAIL

| Criterion | Status | Evidence and limits |
| --- | --- | --- |
| ORB uses completed, available bars and explicit range duration | PASS | [strategy](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/strategy.py#L53), [bar validation](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/sessions.py#L61); [15-minute range versus 5-minute candles](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L21), [delayed receipt](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L95). |
| Chronological availability order; no future evidence in entry decisions | PASS | [replay event order](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/backtest.py#L110); [future-data independence](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L103), [data freshness](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L126). This is bounded test/source evidence, not a universal proof for future integrations. |
| Entry no earlier than next actionable price | PASS | [next-quote test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L73), [same-time quote test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L84). |
| Conservative stop/target treatment | FAIL | Later eligible bars use stop-first, verified by [ambiguity test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L120). Fresh post-entry stop-breaching quotes are ignored: **F-02**. |
| No-fill, partial-fill, time exits and unresolved liquidation | PARTIAL | [fill tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L138), [time/half-day exits](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L127), [missing exit liquidity](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L210). Entry-candle exclusion is disclosed, but does not justify ignoring known post-entry quotes. |
| Identical effective inputs replay deterministically | PASS | [scenario-wide replay tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L60), original fixture manifest/checksums. |
| Reports fully identify effective replay configuration | FAIL | Calendar input can change outcomes without changing reported configuration/checksum: **F-03**. |
| No-trade scenarios produce no artificial profit | PASS | [no-trade tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L51), [scenario-wide checks](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L60). |
| Economic evidence and profitability claims | PASS | [research-only ranking](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L151), mandatory synthetic labels. No evidence-supported profitable strategy has been established. |

### D. Market correctness — PARTIAL

| Criterion | Status | Evidence and limits |
| --- | --- | --- |
| UK/US local sessions, UTC timestamps, DST mismatch periods | PASS | [session contracts](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/domain.py#L216), [fixture calendar](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/sessions.py#L36); [explicit DST cases](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L81). Supported venues include XLON, XNYS and XNAS. |
| Holidays, half-days and unknown dates | PASS | [calendar tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L91), [half-day time exit](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L127). Unknown dates fail closed rather than assuming a weekday session. |
| Complete authoritative exchange holiday coverage | NOT VERIFIED | Explicit synthetic fixture sessions only; no maintained production exchange calendar is integrated. Dates/times demonstrate interfaces and tests, not authoritative exchange coverage. |
| GBP, GBX and USD amounts, ticks, quantities and validated FX | PASS | [instrument contract](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/domain.py#L87), [cost/FX tests](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L191), [FX freshness](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_domain_sessions_costs.py#L222). |
| FX and cost assumptions explicit | PASS | [assumptions](backtesting_assumptions.md), [synthetic cost example](../examples/costs.synthetic.json). Decimal-string rates, effective dates and explicit known fee components are required. |
| Real broker fees, taxes, FX execution and market liquidity | NOT VERIFIED | Artificial schedules and FX rates are not authoritative Trading 212 rates. Bar-exit liquidity and execution queueing are not modelled. |

### E. Safety — PARTIAL

| Criterion | Status | Evidence and limits |
| --- | --- | --- |
| No order placement or live/Practice execution capability | PASS | [broker client](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/broker.py#L87) calls only summary GET; [demo GET/auth test](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_broker.py#L38), [live rejection](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_broker.py#L178). Local ledger fills are simulations. No broker request was made during this audit. |
| Demo URL/environment preserved | PASS | [fixed URL](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/broker.py#L15), [Settings](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/config.py#L12); `https://demo.trading212.com/api/v0/equity/account/summary`, default `ATLAS_ENV=demo`. Other environments fail validation. |
| Research commands have no hidden network/credential access | PASS | [offline CLI routing](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/main.py#L9), [research entry point](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/research_cli.py#L82); [network and secret-read traps](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_strategy_replay_cli.py#L159) pass for both commands. Static AST review found no network or broker imports in ten offline research modules. |
| Credential exposure in inspected public files/logs | PASS | Selected Basic/Bearer and common service-token pattern scans: 36 public text files, zero matches. No project `*.log` files found outside excluded `.git`/`.venv`/`local` runtime directories. Actual `.env` absent. Private configuration and excluded runtime contents were not read. |
| Private/runtime paths ignored by Git | PASS | [.gitignore](../.gitignore); executed check-ignore confirms `.env`, `.env.private`, virtualenv, logs and local research paths. `.env.example` is a placeholder template. |
| No secrets in tracked files/history | NOT VERIFIED | Git has zero tracked files and no committed HEAD. No currently tracked credential file exists, but there is no committed history to attest to. Pattern scans cannot prove absence of every possible raw secret; private configuration was intentionally not inspected. |
| Original Phase 1 behavior preserved | PASS | All 40 broker tests pass; protected file hashes match the retained prior baseline and this audit did not change them. Health/read-only routing, bounded retries, fixed demo URL and sanitization remain intact. |
| Current authenticated demo connectivity | NOT VERIFIED | Intentionally not rerun; this audit uses offline/mocked verification and does not load credentials. Earlier user-reported connectivity is not fresh audit evidence. |

No credentials were printed, logged, uploaded or used by this audit. No live
or Practice orders were placed.

## Material findings and focused regressions

### F-01 — HIGH: partial-entry risk can disappear while exposure remains

Source: [entry_fill](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/portfolio.py#L252),
[exit_fill](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/portfolio.py#L357),
[snapshot](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/portfolio.py#L148),
[aggregate risk check](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/risk.py#L194).

Regression:
[test_remaining_entry_fill_retains_planned_risk_after_first_partial_is_closed](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_phase2b_acceptance.py#L20).

Reproduction uses a genuine independent risk authorization for quantity 10:
fill 5 at 254 GBX, sell those 5 at 252 GBX, then fill the remaining authorized
5 at 254 GBX. The first partial receives all original planned/stressed risk and
the reservation retains zero. Closing that position releases all recorded risk;
the final partial therefore creates a position with zero planned/stressed risk.

Observed snapshot: quantity 5, concurrent planned risk **GBP 0.00**,
`consistent=True`. Bare price-to-stop exposure alone is **GBP 0.20**
(254 minus 250 GBX, times 5); costs would increase planned exposure.
The test fails `0.00 >= 0.20`. Monetary reconciliation still passes, so it is
insufficient as a risk-integrity check.

Impact: subsequent aggregate-risk decisions can undercount exposure. The shipped
minimal replay immediately cancels unfilled entry remainders, so its default
fixture does not exercise this interleaving. The supported ledger API does.

Required remediation, not implemented: preserve/recompute sufficient risk across
position and remaining reservation throughout interleaved fills/exits, or safely
cancel/invalidate the entry remainder when the prior filled portion is closed.
The regression permits safe cancellation if no residual position or intent remains.

### F-02 — HIGH: observed post-entry stop breach can become a target win

Source: [bar stop/target branch](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/backtest.py#L153),
[quote branch](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/backtest.py#L184).

Regression:
[test_observed_post_entry_quote_stop_breach_cannot_become_a_target_win](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_phase2b_acceptance.py#L76).

Added a fresh, complete, valid quote at **08:20:02 UTC**, one second after the
entry at 08:20:01: bid **245 GBX**, ask **247 GBX**, adequate bid liquidity.
The planned stop is **250 GBX**. The containing candle's low is adjusted to
240 GBX so its OHLC remains consistent with the observed quote.

Replay marks the position but does not evaluate its stop on quotes. It excludes
the entire entry-containing candle and later exits at the **259 GBX target at
08:30 UTC**, reporting **GBP +4.56 net** despite the known post-entry breach.
The regression fails on exit timestamp before reaching its price/P&L assertions.

Impact: the engine can report a synthetic winner after an observable stop event.
This is not merely ambiguous pre-entry OHLC ordering: the adverse quote is
explicitly available after the fill.

Required remediation, not implemented: evaluate standing exits on eligible fresh
post-entry executable quotes with stated liquidity/gap/cost assumptions. Preserve
conservative bar ambiguity handling and prevent later target fills after a stop.

### F-03 — MEDIUM: configuration provenance omits outcome-changing calendar input

Source: [injected replay inputs](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/backtest.py#L79),
[configuration serialization](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/backtest.py#L300),
[FixtureCalendar](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/src/atlas/sessions.py#L36).

Regression:
[test_effective_calendar_is_part_of_replay_configuration_provenance](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_phase2b_acceptance.py#L102).

Run the same dataset with its normal calendar, then an injected calendar marking
that session closed. The first report has **1 trade**, the second **0**.
Both retain the same dataset checksum and the same reported configuration checksum:

`42925bac46b58b140e681058472e7ecef91f2a7678f49f0d329d43ca5124734a`

This is not a cryptographic collision or nondeterminism for identical effective
inputs. The outcome-changing effective input is absent from reported provenance.
A calendar version label alone is insufficient if different contents share it.

Required remediation, not implemented: record and fingerprint the effective
calendar input/content or an immutable calendar manifest, alongside other
effective replay configuration. Verify any other injected input needed for
reproduction is identifiable.

## Default synthetic CLI result

**SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE**

| Measure | Actual output |
| --- | --- |
| Dataset / venue | orb / XLON |
| Starting capital / ending equity | GBP 1,000.00 / GBP 1,004.56 |
| Opportunities / local simulated trades | 1 / 1 |
| Gross P&L / explicit costs / net P&L | GBP 4.90 / GBP 0.34 / GBP 4.56 |
| FX expenses included in costs | GBP 0.00 |
| Wins / losses | 1 / 0 |
| Maximum drawdown | GBP 2.06 |
| Cash utilization | 24.90% |
| Rejected opportunities / risk-limit events | 0 / 0 |
| Largest planned / stressed risk | GBP 4.25 / GBP 9.56 |
| Final open positions / reconciliation | 0 / True |

Dataset SHA-256:
`73e70678a73326078377c21dbee8b76ac072056f1c6766b241546d61a17f08b8`.
Configuration SHA-256:
`42925bac46b58b140e681058472e7ecef91f2a7678f49f0d329d43ca5124734a`.

These are an executed fixture example, not evidence of profitability or of
acceptance across adversarial inputs. F-02 demonstrates why the positive
fixture result must not be generalized.

## Audit changes and acceptance gate

Created only:

- [tests/test_phase2b_acceptance.py](https://github.com/jafarsshaik9/project-atlas/blob/74b175323455c7a688448c7f95952adad74e3591/tests/test_phase2b_acceptance.py):
  three focused, ordinary regression tests for the reproduced material gaps.
- [docs/phase2b_acceptance_audit.md](phase2b_acceptance_audit.md): this report.

No implementation fixes, dependency changes, new strategy features, broker
changes, orders or Phase 2C work were performed. Existing tests were neither
deleted nor weakened.

Before Phase 2C, authorize a bounded Phase 2B remediation task for F-01, F-02 and
F-03. Require all **269 current tests** and both Ruff checks to pass, rerun the
offline CLI examples, and review the corrected risk/exit/provenance semantics.
Retain explicit limitations around fixture calendars, synthetic costs/FX,
single-session replay and unmodelled execution liquidity. Real-data adapters,
authoritative calendars and statistically credible validation remain future work.

