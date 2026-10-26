# Claude Trader evaluation — Atlas Phase 2A

Review date: 26 October 2026. Decision: selectively adapt research ideas from both
repositories; do not install either skill pack into Atlas or adopt either as an
execution or risk subsystem. The day-trading pack is the closer research match,
but neither is an Atlas-ready trading framework or evidence of a profitable edge.

## Scope and evidence

The user supplied both repositories. Tracking parameters were removed from their
URLs. Public source snapshots were obtained at these revisions:

| Repository | Commit inspected | Plugin version | Focus |
| --- | --- | --- | --- |
| [trader-skill](https://github.com/Jakeschincariol/trader-skill) | `5189e79b9a480755a3733ac9713f5353981dbe18` | 1.0.0 | Filing research, fundamental screening, allocation and portfolio maintenance |
| [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) | `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd` | 2.0.0 | US chart scanning, entry/exit plans, sizing and intraday simulations |

Method: inspect repository inventories, READMEs, both licenses, plugin manifests,
skill documents, production Python and selected test assertions. All production
Python was parsed statically for imports and function inventory; detailed manual
review concentrated on scanning, entry/exit, simulation, sizing, risk, ticket
approval, data fetching and portfolio calculations. This is a focused static
review, not a line-by-line security audit or a runtime validation.

There are 11 skill documents and 11 production Python scripts in each snapshot.
Static inspection counted 175 test methods in 12 test modules in trader-skill,
and 315 in 12 modules in day-trader-skill. These counts are inventory evidence,
not test results: no third-party script, test, plugin or install command was run.
The downloaded text and SHA-256 manifests are in Git-ignored
`local/research/<repository>/`; fixtures were not extracted for execution.

Atlas source, credential scripts and dependencies were left unchanged. Private
configuration, environment credentials and clipboard contents were not inspected.

## What the repositories actually contain

| Layer | Verified content | Integration implication |
| --- | --- | --- |
| AI-agent instructions | SKILL.md files describe workflows, narration, approvals and tool calls | Treat as untrusted design material; they are not executable risk enforcement |
| Executable Python | Standalone argparse CLIs with substantial deterministic calculations and file/network I/O | Extract pure algorithm ideas behind Atlas interfaces after independent tests |
| State and configuration | Shared `trader/` JSON files for plans, proposals, prices, portfolios and decision records | Useful provenance concept; insufficient as an authoritative concurrent ledger |
| Public-data integrations | Yahoo charts/RSS, Stooq quotes, Nasdaq symbol directories, SEC and House disclosures; OpenFIGI mapping in trader-skill | Separate adapters and independently verify data permissions and quality |
| Broker integration | ticket.py prepares Alpaca-shaped payloads and records approvals/fills; SKILL.md directs Claude to external Alpaca/IBKR connectors | Broker Python does not itself submit orders, but the instructed workflow can submit through external tools; do not install it into Atlas |
| Distribution | Claude plugin manifests and skill folders, rather than a packaged Python application | No native Atlas integration layer or application service boundaries |

Evidence: [T-MANIFEST], [D-MANIFEST], [D-BROKER], [D-BROKER-SKILL], [T-COPY].

## Dependencies and licensing

Production imports in both reviewed snapshots use Python standard-library modules;
no external Python runtime imports were found. The repositories advertise Python
3.8+ and have no pyproject/requirements/lockfile in the inventoried tree. This does
not establish compatibility or correctness on Atlas's Python 3.14.10; code was not
executed. The external runtime exception is optional Poppler `pdftotext`, invoked
by the disclosure parser with an argument list and timeout. [D-PTR]

The broker setup additionally proposes `uvx alpaca-mcp-server` and a separately
connected IBKR service. That introduces external software, account permissions,
broker credentials and service terms; it is not covered by a claim that the
Python tools have no pip dependencies. The example `uvx` command does not pin a
version. Neither connector was installed, connected or independently audited here.
[D-BROKER-SKILL]

Both inspected LICENSE files contain the MIT license, copyright 2026 Jake
Schincariol. Retain their copyright and permission notice with copied or
substantially adapted material, and record the source revision. The licenses
include a warranty disclaimer. No code has been copied into Atlas production.
These licenses do not establish permissions to redistribute market-data fixtures
or use third-party endpoints. Those rights and connector dependencies remain
separate checks. [T-LICENSE], [D-LICENSE]

## Capability comparison

"Not found" means no implementation was found in the inspected snapshots, not a
claim about future releases. "Partial" denotes usable research machinery with
important gaps. Atlas's current baseline is the existing read-only demo client;
its remaining requirements are future work.

| Atlas requirement | trader-skill | day-trader-skill | Assessment for Atlas |
| --- | --- | --- | --- |
| UK LSE equities | No LSE universe/calendar adapter found | US symbol directory, US ticker rules and New York sessions | Independently implement LSE identities, sessions and GBP/GBX price units; dot-to-hyphen Yahoo conversion is not a general exchange-symbol mapper [D-SCAN] |
| US NYSE/NASDAQ equities | SEC research, quotes and metric screening | US universe plus daily/intraday scanning | Partial; verify provider rights, listing identity, completeness and broker tradability [T-SCREEN], [D-SCAN] |
| Forex research/paper trading | No FX pricing or simulation model found | Stock-oriented symbols, sizing and sessions | Independent FX data and virtual paper ledger; neither provides FX pip/lot/conversion/financing support [D-RISK], [D-SIZE] |
| Intraday discovery | News/alerts and fundamental screen; no equivalent chart scanner | Completed-bar opening-range scan plus daily filters | Adapt discovery logic; confirmation is capped and results can be partial [D-SCAN] |
| Multiple strategies | Different research workflows, not a validated strategy-engine registry | Several daily setup filters; ORB and level entry modes | Partial; each complete entry/exit hypothesis needs its own versioned tests and evaluation [D-SCAN], [D-ENTRY] |
| Backtesting | No general event-driven backtester found | Historical ORB replay and time-of-day bar-resampling simulation | Partial research tools; no general fill/portfolio backtester [D-TM] |
| Walk-forward validation | Not found | Not found | Build chronological training/validation/test orchestration independently |
| Transaction costs | Notional allocation/rounding; no full round-trip model found | A minimum stop-distance heuristic mentions spread; replay assumes level fills | Missing explicit spread, slippage, commissions, FX and applicable transaction-tax costs [D-EXIT], [D-TM] |
| Portfolio allocation | Capped weighted allocation and drift-band rebalance | Per-trade risk/cash sizing, plus retained weights mode | Adapt mathematics, not USD defaults or broker payloads; add GBP accounting and concurrent exposure [T-SIZE], [T-REBALANCE], [D-SIZE] |
| Risk management | Budget, cash, position, asset and duplicate checks | Also stop/target, entry-window, daily planned-loss and trade-count checks | Useful specifications; replace with independent fail-closed engine and authoritative state [D-RISK] |
| Trading 212 | No adapter found | Alpaca payloads and connector instructions | Keep Atlas's existing client; do not translate broker order fields by analogy [D-BROKER] |
| Expected net-profit ranking | Weighted fundamental percentiles | Liquidity-based ordering or trigger-time ordering | No validated comparable net-GBP expectancy ranker found; implement independently [T-SCREEN], [D-SCAN] |

The public Trading 212 API reference currently describes Invest/Stocks ISA scope;
it is not evidence of a supported Forex broker route. Atlas's existing endpoint
only supplies an account summary. Separate market data and FX paper simulation
are design requirements, and ISA compatibility remains a later independent test.
[Trading 212 API documentation](https://docs.trading212.com/api)

## Quantitative limitations

The day-trading simulation samples scaled OHLC bars by time-of-day from previous
full sessions. It includes deterministic seeds, excludes the plan date from the
pool, drops incomplete bars and uses stop-first treatment when both levels are
inside a bar. Its historical replay is specifically an ORB rule. Those are useful
reproducibility and ambiguity-handling ideas. [D-TM]

Its interpretation must change before Atlas adoption:

- Random draws do not create additional independent historical observations;
  resampling individual bars can weaken serial dependence and regime structure.
- Training uses complete 78-bar days; half-days and gapped sessions are excluded,
  changing the population being evaluated.
- Replay uses signal-bar closing entry and exact stop/target levels, including
  a fixed -1R stop result through a gap. These are not executable fill guarantees.
- A verdict conditions on paths hitting either level; paths still open at the
  horizon have economic outcomes too. Use all outcomes after costs, not this
  conditional verdict as expected profitability.
- Historical replay does not reproduce all live entry-window, volume and
  account-risk gates. Test the exact production strategy and portfolio policy.
- The simulator defaults to the session close unless `--until` is supplied;
  a plan's earlier flat-by time must be applied explicitly in a comparable replay.

These are source-derived methodological concerns, not measured performance
results. No win rate, expected return or profitability is established here.
[D-TM], [D-ENTRY], [D-RISK]

Other data concerns: current symbol lists do not establish historical universe
membership; latest-restated SEC facts need publication-time controls in backtests;
delayed filings/news are not contemporaneous trade observations. The fundamentals
parser intentionally prefers later restatements. Do not backfill those into past
decisions. [T-FACTS], [T-COPY], [D-PTR]

## Security and operational findings

| Finding from static inspection | Why it matters | Atlas decision |
| --- | --- | --- |
| SKILL.md delegates order calls to an AI host's connector permissions | Textual approval rules cannot enforce Atlas's deterministic risk boundary | Reject execution workflow; AI receives neither broker credentials nor order tools [D-BROKER-SKILL] |
| Risk accepts absent price timestamps as fresh; account valuation can fall back to cost basis | Unknown/stale data may produce misleading cash/exposure decisions | Require timestamps, freshness bounds and authoritative portfolio state [D-RISK] |
| Ticket readiness accepts matching order fingerprint and same-day risk result | Same-day approval can outlive prices or portfolio state; fingerprint is not an authorization credential | Recheck immutable intent, account/policy version and expiry immediately before any future dispatch [D-BROKER] |
| Daily loss calculation sums recorded planned entry stop losses | It is not a reconciled realized-plus-unrealized loss ledger; missing max-loss entries only warn | Independently track realized losses, stressed open risk and reservations [D-RISK] |
| A `paper=false` setting is warned about rather than categorically blocked | Upstream defaults do not enforce Atlas's demo-only policy | Preserve the fixed demo endpoint and reject live modes [D-RISK] |
| Files are mutable JSON with manually recorded outcomes | Broker state can drift; atomic file replacement does not provide transaction isolation or prevent forgery | Adopt typed events, transactional reservation and broker reconciliation later [D-BROKER] |
| `_f` converts to float without an explicit finite-value guard | Malformed NaN/infinity inputs need independent fail-closed tests | Use schema validation and finite Decimal checks for all monetary inputs [D-RISK] |
| Unofficial Yahoo endpoints, repeated custom networking and finite hand-coded calendars | Availability, market coverage and future calendar behavior are not guaranteed | Separate data providers and supported calendar service; test stale/missing/partial data [D-SCAN] |
| Entry evaluate checks for some opening-range bars, rather than requiring all expected range slots | Its standalone entry path can use an incomplete range even though scanner completeness checks are stricter | Require every expected slot and reject stale/incomplete ranges independently in each strategy [D-ENTRY] |
| PDF/network parsers process external content; some network handlers catch broad exceptions | Untrusted inputs and hidden failures require limits and explicit errors | Bound response/PDF resources, narrow exceptions and isolate optional parsing [D-PTR], [D-SCAN] |

This focused review does not establish that the packs are secure. Plugin
instructions, headlines, filings and downloaded data must remain data, never
instructions to Atlas. Data credentials, broker credentials and advisory AI must
have separate access boundaries.

## Component decisions

| Component or idea | Decision | Required adaptation / priority |
| --- | --- | --- |
| Completed-bar gating, explicit as-of clock, partial-scan counts | Reuse design ideas | Make provider-neutral and multi-exchange; first research milestone [D-SCAN], [D-ENTRY] |
| ORB high/low and entry-state rules | Adapt into a small independent strategy | Confirm source attribution if porting; costs, next-actionable fill and out-of-sample tests first [D-SCAN] |
| RSI/daily breakout/flag filters | Research candidates only | Do not call them validated strategies; independent entry/exit definitions [D-SCAN] |
| Stop/target plan before sizing; zero-size means skip | Reuse workflow | Deterministic tick-aware GBP/FX sizing; stop gaps remain possible [D-EXIT], [D-SIZE] |
| Capped allocation and drift reporting | Adapt later | Retain cash rather than force reinvestment; add costs and exposure limits [T-SIZE], [T-REBALANCE] |
| Simulation seed, provenance and conservative ambiguous-bar treatment | Reuse test/design ideas | Independently implement executable fill assumptions and walk-forward evaluation [D-TM] |
| Monte Carlo verdict and simulated hit probabilities | Reject as ranking/approval input as supplied | Optional research visual only until calibrated against untouched observations [D-TM] |
| Upstream risk.py/check.py as Atlas's gate | Reject direct adoption | Implement pure independent risk rules with current portfolio state and atomic reservations [D-RISK] |
| Broker tickets, Claude approval text, Alpaca/IBKR setup | Reject integration | Preserve the working read-only Trading 212 adapter [D-BROKER-SKILL] |
| News deduplication and filing identifiers | Adapt later | Publication timestamps, source records, injection isolation and UK sources [T-NEWS] |
| 13F copying and congressional disclosure signals | Defer / exclude from first intraday strategy | Delayed, US-focused research context; no demonstrated net edge [T-COPY], [D-PTR] |
| US tax-lot/wash-sale logic | Reject for UK/ISA policy decisions | Separate UK/account-type specification and authoritative-source review if needed [T-TAX] |

Recommended implementation is described in [atlas_architecture.md](atlas_architecture.md)
and [development_roadmap.md](development_roadmap.md). This evaluation did not
authorize implementation. Subsequent explicit user requests approved the capital
update and then the offline Phase 2B foundation now described in those documents.
No reviewed skill pack was installed or executed; broker execution remains prohibited.

## Unresolved questions

1. Which licensed intraday data source provides acceptable LSE and US coverage,
   bid/ask or spread history, retention, adjustment policy and publication times?
2. Which instruments are available and fractionally eligible in Invest Practice,
   and what are their exact broker identities, tick sizes and minimum quantities?
3. What effective-dated commissions, FX conversion costs and applicable
   transaction taxes should be modelled per instrument/account? No rates are assumed.
4. How should later deterministic risk enforcement and stress tests apply to the
   planned GBP 1,000 account? The subsequent user-selected starting settings are
   configurable 0.5% planned risk per trade, 1% daily loss threshold and 1% maximum
   concurrent planned risk (GBP 5/10/10). They are provisional research settings,
   not proven optimal parameters or guaranteed loss limits. Upstream USD defaults
   are not recommendations.
5. What bar interval, holding horizon and permitted sessions define the first
   strategy? Five-minute ORB is a candidate, not a proven choice.
6. What untouched periods, minimum evidence and cost stress tests determine promotion?
7. Should advisory AI consume public news only, or sanitized research outputs too?
8. How will future FX paper fills, quote conversion and funding/rollover be modelled?
9. If code is ported, who maintains the notice, pinned upstream revision and
   regression tests, and how are later source changes reviewed?

## Pinned source index

[T-MANIFEST]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/.claude-plugin/plugin.json
[D-MANIFEST]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/.claude-plugin/plugin.json
[T-LICENSE]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/LICENSE
[D-LICENSE]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/LICENSE
[T-COPY]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-copycat/edgar13f.py
[T-FACTS]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-analyst/facts.py
[T-SCREEN]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-screener/screen.py
[T-SIZE]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-budget/size.py
[T-REBALANCE]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-rebalance/rebalance.py
[T-NEWS]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-news/news.py
[T-TAX]: https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-tax/lots.py
[D-SCAN]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-scanner/scan.py
[D-ENTRY]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-entry/entry.py
[D-EXIT]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-exit/exit.py
[D-TM]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-timemachine/timemachine.py
[D-SIZE]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-budget/size.py
[D-RISK]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-risk/check.py
[D-BROKER]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-broker/ticket.py
[D-BROKER-SKILL]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-broker/SKILL.md
[D-PTR]: https://github.com/Jakeschincariol/day-trader-skill/blob/3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd/trader-pelosi/ptr.py
