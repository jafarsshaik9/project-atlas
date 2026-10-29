# Project Atlas — Offline Quantitative Research

## Active research scope: Forex and financial spread betting

From Phase 6B-4, active trading research is exclusively Forex and financial
spread betting. Existing stock code, datasets and reports are preserved as legacy
infrastructure; their presence below does not authorize new stock research.
Phase 6B-4 recommended **REDESIGN RESEARCH**, with no profitable strategy
established. See the [Phase 6B-4 diagnostics and roadmap](docs/phase6b4_profitability_diagnostics.md)
and [capital/broker feasibility assessment](docs/phase6b4_instrument_feasibility.md).
No trading, broker authentication, leverage activation, new historical backtest
or subsequent phase is authorized by these reports.

Phase 6B-5 adds an artificial-only contract and capital-feasibility model. The
current recommendation is **REVISE CONTRACT MODEL**: complete verified product
specifications before further strategy evaluation. Read the
[broker matrix](docs/phase6b5_broker_contract_matrix.md),
[capital comparison](docs/phase6b5_capital_efficiency.md) and
[acceptance](docs/phase6b5_acceptance.md). Phase 6B-6 has not started.

## Phase 3B: deterministic historical backtesting

Atlas can now replay an accepted Phase 3A US-equity dataset through the existing
ORB strategy, independent risk engine, cost engine and GBP virtual ledger. It never
modifies the source dataset and has no broker, provider-network or notification path.

UNKNOWN source publication availability remains invisible in `VERIFIED` mode. The
separate `ASSUMED` mode applies a declared bar-end delay only inside the replay and
labels every result exploratory and not point-in-time verified. It does not rewrite
stored timestamps or claim a provider publication time.

```powershell
Copy-Item examples/historical-backtest.assumed.json local/historical-backtest.json
# Edit only the local copy: set its accepted dataset SHA and explicit FX/cost assumptions.
.\.venv\Scripts\python.exe -m atlas.main historical-backtest `
  --historical-config local/historical-backtest.json `
  --historical-db local/market-data/massive-pilot/historical.sqlite3
```

The initial engine is one recorded US session per run. Orders are modeled at a
strictly later one-minute bar open, with executable spread, configured slippage and
fees, and a capacity ceiling derived from a configured fraction of reported volume.
Reported volume is not claimed to be order-book liquidity. Read the
[Phase 3B architecture](docs/phase3b_architecture.md) and
[implementation report](docs/phase3b_report.md) before interpreting any output.

## Phase 3A-M: Massive US historical data

The dedicated adapter uses explicit USD common-stock reference evidence, exact
minute normalization, secure pagination and a shared conservative persistent quota.
Original publication availability remains UNKNOWN. No historical observations feed
trading, portfolio balances or Telegram. Massive is inactive unless explicitly
invoked; no subscription or account-setting change is automated.

```powershell
.\.venv\Scripts\python.exe -m atlas.main market-data providers
.\.venv\Scripts\python.exe -m atlas.main market-data massive-pilot --help
```

The initial owner-authorised AAPL pilot is limited to four lifetime authenticated
attempts and stops on failure. Further requests need separate approval. Read
[setup, quota and recovery](docs/massive_provider.md) and
[actual pilot/acceptance results](docs/phase3a_massive_acceptance_report.md).

## Phase 3A: historical data infrastructure

Isolated historical contracts, maintained cash-equity calendars, quality/quarantine,
resumable local storage and provider commands are implemented. **No real market
data has been downloaded.** The original artificial provider works offline for
LSE GBP/GBX and NYSE/NASDAQ USD one-minute bars. A US-only Twelve Data adapter is
HTTP-mocked; external access/licensing remains blocked without a separately
approved account. LSE one-minute vendor access is unverified.

```powershell
.\.venv\Scripts\python.exe -m atlas.main market-data --help
.\.venv\Scripts\python.exe -m atlas.main market-data providers
.\.venv\Scripts\python.exe -m atlas.main market-data instruments
.\.venv\Scripts\python.exe -m atlas.main market-data download
.\.venv\Scripts\python.exe -m atlas.main market-data download --venue XNAS --minutes 10
.\.venv\Scripts\python.exe -m atlas.main market-data download --config examples/market-data.fixture.json
.\.venv\Scripts\python.exe -m atlas.main market-data coverage
# Copy a reported dataset SHA-256 locally into $datasetId:
.\.venv\Scripts\python.exe -m atlas.main market-data inspect $datasetId
.\.venv\Scripts\python.exe -m atlas.main market-data validate $datasetId --json
.\.venv\Scripts\python.exe -m atlas.main market-data backup local/backups/historical-checkpoint.sqlite3
```

Default storage is ignored `local/market-data/historical.sqlite3`; `--db` selects
a separate test store. Downloads default to ten **artificial** minutes on
2024-10-28. Repeating identical requests is idempotent; `--refresh` explicitly
fetches again. Example output: `ARTIFICIAL_OFFLINE_FIXTURE | ACCEPTED`, 10 bars,
`QUALITY: WARNING` for partial session, incomplete actions and unassessed
survivorship. ACCEPTED is a storage state, not validated trading evidence.
`--scenario missing` produces a quarantined dataset and exit code 1.

After the acceptance remediation, interrupted refreshes can be resumed explicitly:
repeat the original request/provider/config/database arguments with
`--resume-job $jobId`, instead of `--refresh`. The failed import reports its job ID
on stderr; request or policy changes are refused. Publication and inspection now
verify bound requests, durable source chains, normalized bars and quality together.
Metadata-only coverage shows `integrity=NOT_VERIFIED`; use inspect/validate to
verify contents. Read commands refuse missing databases without initializing them.
Inspect/validate `--json` stdout is one JSON document, including failure reports.

Unknown original availability stays UNKNOWN and is excluded from strict consumer
selection. Phase 3B consumes data only through an explicit offline command; dashboard
startup, Telegram and broker code remain isolated. No Phase 3C or broker execution is added.
Read [architecture](docs/phase3a_architecture.md), [provider comparison and approval
steps](docs/market_data_providers.md), [quality/storage/backup policy](docs/historical_data_quality.md)
and [acceptance evidence](docs/phase3a_acceptance_report.md).

## Phase 2C: local research dashboard

The authenticated dashboard adds persistent, isolated synthetic run history,
portfolio/risk charts, confirmed-fill history, activity and opt-in Telegram alerts.
**SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE.** No broker order
capability is enabled, and the GBP 1,000 virtual capital stays independent of Practice funds.

From the project directory in PowerShell (approved dependencies are installed):

```powershell
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest
.\.venv\Scripts\python.exe -m atlas.main dashboard-setup
.\.venv\Scripts\python.exe -m atlas.main dashboard-check
.\.venv\Scripts\python.exe -m atlas.main dashboard
```

Create the workspace password at masked local prompts; then open
**http://127.0.0.1:8000**. Stop with **Ctrl+C**. A fresh environment installs
the approved `.[dev,dashboard,cards,market-data]` extras for the full application.
Chart.js is bundled locally; Node is unnecessary.

Research runs now persist by default to ignored `local/atlas-history.sqlite3`.
Identical inputs/code are deduplicated; `--new-run` creates a separate experiment.
`--no-persist` keeps a replay entirely in memory. Separate runs never compound P/L.
Research-check remains offline and does not create portfolio history.

Read the [dashboard/mobile/backup guide](docs/dashboard_user_guide.md),
[Telegram setup](docs/telegram_setup.md), [Phase 2C architecture](docs/phase2c_architecture.md)
and [verification report](docs/phase2c_report.md). Telegram is disabled by default;
`notifications-preview` is offline. Real delivery requires your explicit local
test invocation and is not claimed verified. LAN access requires authentication
and explicit configuration; LAN HTTP is unencrypted. No firewall/router changes occur.

SQLite restores dashboard views after restart, not an in-flight trading ledger.
Charts show actual persisted snapshots; insufficient-period views stay unavailable.

Atlas now includes a working offline Phase 2B research foundation and the preserved
demo-only, read-only Trading 212 Invest Practice diagnostics. Planned virtual
capital defaults to GBP 1,000 independently of the Practice account balance.
No broker order sender, live environment, paid feed, Forex trading or ISA
integration is enabled. The only strategy is an unvalidated synthetic ORB hypothesis.

## Run offline research

From the project directory in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m atlas.main research-check
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --venue XNAS --scenario gap
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --scenario sideways
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --scenario time_exit --session-date 2026-12-24
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --opening-range-minutes 15 --json
```

These commands require no broker credentials, internet, AI service or market-data
subscription. Research commands do not load broker credentials. The default ORB
uses a configurable 15-minute opening range on five-minute candles.

Example artificial XLON run:

```text
SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE
Dataset: orb; venue: XLON
Starting virtual capital: GBP 1,000.00
Ending virtual equity: GBP 1,004.56
Opportunities: 1; simulated trades: 1
Gross P&L: GBP 4.90; costs: GBP 0.34; net P&L: GBP 4.56
Wins/losses: 1/0; max drawdown: GBP 2.06; cash utilisation: 24.90%
```

The artificial XNAS gap example ends at GBP 970.77 (net loss GBP 29.23), exceeding
its GBP 5 planned loss budget and breaching the daily threshold. Sideways data
identifies zero opportunities and retains GBP 1,000. These are software fixtures,
not evidence that ORB is profitable or that planned/stressed risk bounds actual losses.
Full output includes rejections, data outcomes, risk events and provenance hashes.
JSON additionally contains configurations, risk decisions, fills and audit history.

Configuration examples are explicit offline inputs:

```powershell
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --research-config examples/research.json --risk-config examples/risk.synthetic.json --cost-config examples/costs.synthetic.json
```

Explicit research JSON takes precedence over research environment variables;
without that file the existing environment settings below apply. Monetary values
and rates in JSON must be strings to preserve Decimal precision. Unknown costs
reject new risk; example zero tax rates and all fee figures are synthetic.
The examples also define provisional allocation, gap-stress and quote-age settings.

Implemented modules: validated domain contracts; explicit synthetic sessions and
clock; original synthetic observations; effective-dated costing; transactional
in-memory portfolio accounting; independent risk decisions and reservations;
ORB v1; chronological replay and reports. Phase 2B needed no new dependency;
Phase 2C adds the project-local dashboard dependencies described above.

Read [synthetic data](docs/synthetic_data.md), [fill/cost/risk assumptions and
limitations](docs/backtesting_assumptions.md), [architecture](docs/atlas_architecture.md)
and [next steps](docs/development_roadmap.md) before interpreting results.

## Planned capital and provisional research settings

`ResearchSettings` is the single application source for starting capital and the
three research percentages. The simulated `PortfolioLedger` uses those settings
only and never initializes from Trading 212 cash or account value. The earlier
immutable `VirtualLedger` opening snapshot remains for compatibility and health
reporting; Phase 2B uses the separate full accounting ledger.

| Setting | Process environment variable | Default | Initial GBP budget |
| --- | --- | --- | --- |
| Starting virtual capital | `ATLAS_STARTING_CAPITAL_GBP` | GBP 1,000.00 | GBP 1,000.00 |
| Planned risk per trade | `ATLAS_PLANNED_RISK_PER_TRADE_PCT` | 0.5% | GBP 5.00 |
| Daily loss threshold | `ATLAS_DAILY_LOSS_THRESHOLD_PCT` | 1% | GBP 10.00 |
| Maximum concurrent planned risk | `ATLAS_MAX_CONCURRENT_PLANNED_RISK_PCT` | 1% | GBP 10.00 |

Percentages use initial configured capital as a fixed research denominator;
`0.5` means 0.5%, not 50%. GBP budgets round down to whole pennies. Starting
capital must be positive, finite and a whole number of pennies; percentages must
be positive, finite and at most 100, with per-trade planned risk no greater than
maximum concurrent planned risk. Invalid inputs fail without echoing their values.

These are provisional research settings, not proven optimal trading parameters
or guaranteed loss limits. Gaps, slippage and costs can produce larger losses.
They govern local simulation eligibility only. Independent risk checks include
costs, reservations, daily losses, stressed risk, fresh data and kill-switch state.
No simulated fill is a broker order or authorization to trade real money.

For example, from the project directory in PowerShell:

```powershell
$env:ATLAS_ENV = 'demo'
$env:ATLAS_STARTING_CAPITAL_GBP = '1000.00'
$env:ATLAS_PLANNED_RISK_PER_TRADE_PCT = '0.5'
$env:ATLAS_DAILY_LOSS_THRESHOLD_PCT = '1'
$env:ATLAS_MAX_CONCURRENT_PLANNED_RISK_PCT = '1'
.\.venv\Scripts\python.exe -m atlas.main health
```

These are non-secret session settings. `.env.example` documents them but is not
loaded automatically. Offline `health` reports configured capital, opening virtual
cash and derived risk budgets. `broker-check` continues to report the broker's
actual demo summary; it does not alter the virtual ledger. Existing mocked broker
balances intentionally differ from planned capital to preserve that distinction.

## Windows PowerShell setup

Use Python 3.14 (recommended) or 3.13 and Git. Installation requires your approval.
Python 3.14 has current Windows binary installers; the latest Python 3.13 security
release is source-only. See the [official Python releases](https://www.python.org/downloads/).
From the workspace:

```powershell
Set-Location '.\project-atlas'
git init -b main
python --version
python -m pip --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,dashboard,cards,market-data]'
.\.venv\Scripts\python.exe -m atlas.main health
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Use the full installed Python executable path if `python` resolves to the Windows
Store alias. Activation is optional; using the venv executable avoids changing
PowerShell execution policy. Select `.venv\Scripts\python.exe` in VS Code's
Python interpreter selector. Standard pytest is the test command.

### Exact-version rebuild

`requirements/phase3a-windows-py314.txt` captures the verified Windows x64 Python
3.14.10 environment, including every extra, transitive package and the editable
wheel's setuptools build backend. This fixes the earlier missing-extra setup gap.
After approval for installation, use a **new** ignored environment:

```powershell
.\.venv\Scripts\python.exe -m venv local/phase3a-rebuild-venv
.\local\phase3a-rebuild-venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements/phase3a-windows-py314.txt
.\local\phase3a-rebuild-venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e '.[dev,dashboard,cards,market-data]'
.\local\phase3a-rebuild-venv\Scripts\python.exe -m pip check
.\local\phase3a-rebuild-venv\Scripts\python.exe -m pytest -q
```

The second install uses the explicitly pinned build backend rather than fetching
an unpinned isolated backend. Package version equality and complete tests were
verified in the separate rebuild. The snapshot is not a wheel-hash lock, does not
bundle Python or wheels, and is not claimed verified on Python 3.13, Linux or
macOS. Normal ranged extra installation above may resolve different versions.
Calendar dependencies add pandas/numpy and small calendar/timezone helpers;
no Node, Arrow, paid SDK or system setting change is required.

For this machine, Python is installed at
`$env:LOCALAPPDATA\Programs\Python\Python314\python.exe` and Git at
`C:\Program Files\Git\cmd\git.exe`. Existing VS Code terminals may retain their
old PATH. For example, from the project directory:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" --version
& 'C:\Program Files\Git\cmd\git.exe' status --short
```

## Secure local credentials and connectivity

Create an API Key and API Secret within **Invest Practice**, with only the account
read permission needed for the summary. Confirm the account is Practice yourself;
the summary schema does not expose the account type. Do not use live credentials.

Run this in your own local terminal, never in chat:

```powershell
.\scripts\broker-check.ps1
```

Both values are entered through masked `Read-Host -AsSecureString` prompts. The
script puts them only in process environment variables for the Python subprocess,
then restores the previous environment in `finally`. It stores no credential file.
These are `ATLAS_ENV=demo`, `TRADING212_API_KEY`, `TRADING212_API_SECRET`.
The credentials must exist briefly as plaintext in process memory for HTTP Basic
authentication; no command prints them. Do not enable HTTP debug tracing or paste
credentials into shell commands, source files, chat or issue reports.

If masked input rejects pasted characters, use the local clipboard option:

```powershell
.\scripts\broker-check.ps1 -UseClipboard
```

At the first prompt, copy only the raw API Key from Trading 212, return to the
terminal and press Enter **without pasting**. At the second prompt, copy only the
raw API Secret, then return and press Enter again. The script reads the clipboard
locally without displaying its contents. It does not read the clipboard until
you confirm each prompt. Copy ordinary text afterward to replace the clipboard
contents. No clipboard contents are written to a file.

If execution policy blocks the script, paste its reviewed contents into your local
PowerShell session; no system policy change is necessary. `.env.example` documents
variable names only for broker/research/Telegram commands. Only explicitly authorised
Massive requests may lazily select MASSIVE_API_KEY from local `.env`. `.env`, venv,
logs, local configuration and editor files are ignored by Git.

With environment variables already set locally:

```powershell
.\.venv\Scripts\python.exe -m atlas.main broker-check
```

The output contains only currency, total value and available cash. Account ID,
credentials, response bodies and request headers are never printed. `health` is
offline and reports credential presence without verifying them.

## API contract and safety

Verified 26 October 2026 against the
[official documentation](https://docs.trading212.com/api) and its
[OpenAPI specification](https://docs.trading212.com/_bundle/api.json?download=).

- Authentication: HTTP Basic, API Key as username, API Secret as password.
- Only request: `GET https://demo.trading212.com/api/v0/equity/account/summary`.
- Limit: one request per five seconds, shared across the account's keys/IPs.
- Summary fields: `id`, `currency`, `totalValue`, `cash` (`availableToTrade`,
  `inPies`, `reservedForOrders`), `investments` (`currentValue`, `totalCost`,
  `realizedProfitLoss`, `unrealizedProfitLoss`). Atlas validates and returns only
  the three fields it displays; unknown fields are ignored. Money uses Decimal.
- Fixed origin, TLS verification, no redirects, no proxy/environment credential
  discovery, no endpoint override, no order methods.
- Ten-second HTTP operation timeouts; five-second connect timeout. Three attempts
  maximum for transport failures, HTTP 408/429/5xx. Attempts are at least five
  seconds apart per client; broker reset/Retry-After headers are respected.
  A requested delay over 30 seconds stops the check for manual retry.
- Rate pacing is per client, not a distributed account limiter. Run checks at
  least five seconds apart and coordinate other clients using the same account.
- Static sanitised errors exclude upstream payloads and exception details. The
  broker diagnostics do not configure logging or persist broker account data.
  Phase 2C separately persists synthetic research activity and redacts Telegram transport logs.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Python alias cannot run | Use a real Python 3.13 installation and its full path. |
| `No module named atlas` | Install `-e '.[dev]'` using this project's venv Python. |
| Missing credentials | Run the secure local prompt script. |
| Credential format error | Follow the field-specific message. Paste the raw key and secret separately, without quotes, prefixes, spaces or line breaks. Never share their values. |
| HTTP 401 | Regenerate/check the Practice API Key and Secret. |
| HTTP 403 | Check account read scope and Trading 212 IP restrictions. |
| HTTP 429 | Wait; account-wide limits include other clients. |
| HTTP 5xx / network / TLS / timeout | Retry later; check connectivity and local certificate configuration. Do not disable TLS. |
| Invalid schema/JSON | Recheck official docs; do not post raw responses or credentials. |
| Live environment rejected | Set `ATLAS_ENV=demo`. Live access is deliberately unavailable. |

## Next development step

Next, evaluate authorized point-in-time market data and maintained calendar
coverage, verify cost applicability and design chronological holdout validation.
The current synthetic results do not justify broker execution or a profitability claim.
