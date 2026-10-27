# Phase 2C implementation and verification report

27 October 2026. **SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE.**

**Latest extension: multiple owner-approved private Telegram recipients.**
Persistent recipient approval/preferences and per-recipient/event delivery state
now support one bot sending the same eligible HTML/PNG alerts to the owner and
an approved friend. No real messages were sent during implementation. Original
single-recipient behavior is retained until explicit registry activation.
See [the multi-recipient verification report](telegram_multi_recipient_report.md)
and [exact setup commands](telegram_setup.md#multiple-authorised-private-recipients).
Trading, accounting, risk, broker and research-ownership source remain unchanged.
Verification: **505 passed, 1 existing warning**; Ruff lint/formatting and the
offline two-recipient PNG/restart/deduplication smoke test passed. All 463
pre-extension tests remain, plus 42 new cases. Real multi-phone delivery is
unverified; no real notifications were sent by this implementation task.

**Latest Telegram visual upgrade: PASS for offline/software verification.**
HTML alerts, opt-in local PNG cards and a fictional offline gallery are implemented.
**463 tests passed**, including all 398 pre-upgrade cases and 65 new presentation/
delivery cases; Ruff lint and formatting pass. No real redesigned message/card
was sent during this work. See [the visual upgrade report](telegram_visual_upgrade_report.md)
and [setup/preview instructions](telegram_setup.md).

**Latest ownership remediation: PASS (27 October 2026).** The stale-worker
terminal-write defect H-01 is fixed and both unchanged acceptance regressions pass.
That remediation suite passed: **398 passed, 1 existing deprecation warning**; Ruff
lint and formatting pass. The original 373 tests are retained. See
[phase2c_acceptance_audit.md](phase2c_acceptance_audit.md) for the original failing
evidence and remediation results. The subsequently authorized visual upgrade is
recorded above. Phase 3 has not started; its separate authorization gate remains.

The user has confirmed dashboard/Telegram operation; the acceptance audit did not
independently repeat a real notification. Physical phone/Wi-Fi/TLS verification
is distinct from source/test acceptance.

## Ownership remediation checkpoint

Only production `src/atlas/history.py` changed in this remediation. Added
`tests/test_worker_ownership.py` with 23 focused test cases; the two existing
acceptance tests and all earlier tests were left unchanged. Updated this report,
the architecture and acceptance audit, and the Telegram guide's stale gate notice.

The root cause was appending a failure event/outbox row before an owner-qualified
status UPDATE whose affected-row count was ignored. Schema 3 now persists unique,
non-reusable per-run attempt identities. Failure and completion validate current
owner, RUNNING state and unexpired lease inside the same `BEGIN IMMEDIATE`
transaction as their event/outbox/status changes. Expired leases cannot be
renewed. Superseded failure handlers have no side effects; valid failures remain
visible. Duplicate or conflicting terminal commits are rejected. See the
[transaction design and upgrade procedure](phase2c_architecture.md#research-worker-ownership-fence).

Verification includes 59 focused history/CLI/ownership tests, all 398 tests,
Ruff lint, formatting of all 53 Python files, research-check, persisted
synthetic-backtest, dashboard-check, offline notifications-preview, the 18-run /
342-snapshot independent accounting probe, and actual loopback dashboard startup
with desktop/mobile browser checks. Default fixture: GBP 1,000 starting capital,
GBP 1,004.56 ending equity, GBP 4.56 synthetic net P&L, GBP 0.34 costs; reconciled.
These artificial returns are not evidence of profitability.

Ownership remediation and the current automated acceptance suite: **PASS**.
The offline Phase 2C foundation could be frozen at that checkpoint, subject to the
documented limits. No styling, image cards or delivery redesign was implemented
in that earlier ownership remediation. Phase 3 remains
gated on that upgrade's acceptance and explicit user authorization. No broker
request/order, real Telegram message, credential inspection, system change or
dependency installation occurred during remediation.

## Original implementation verification record

The offline/dashboard checks below passed during implementation. They remain
historical evidence, not a final acceptance decision. Telegram transport/outbox
was then verified with mocks; real delivery had not yet been independently
verified. No broker request, Practice order, live order or later-phase work occurred.

## Gate 0

Before Phase 2C code was written, the README, architecture/roadmap, backtesting
assumptions, Phase 2B completion/acceptance reports and affected source/tests were
reviewed. F-01/F-02/F-03 remediations were present. The full suite returned
**292 passed in 1.11s**; Ruff lint passed and format check reported 31 files already
formatted. No Phase 2B defect was silently fixed under this task.

## Delivered files

| Added files | Responsibility |
| --- | --- |
| `src/atlas/activity.py` | Versioned typed event/payload contracts and consistent portfolio snapshots |
| `src/atlas/history.py` | SQLite schema 1→2 migration, WAL transactions, idempotency, causal history, leases, outbox, backup |
| `src/atlas/research_history.py` | Observational adapter from actual replay/ledger data to isolated run histories |
| `src/atlas/dashboard_models.py` | Validated selected-run portfolio, charts, positions, trades, risk and health views |
| `src/atlas/dashboard_security.py` | Scrypt passwords, opaque cookie sessions, persisted login limits and private binding checks |
| `src/atlas/dashboard_cli.py` | Setup/server/check/backup and explicit notification commands |
| `src/atlas/notifications.py` | Provider interface, Telegram formatting/transport, bounded audited delivery |
| `src/atlas/telegram_setup.py` | Masked local secret entry and nonce-verified private recipient discovery |
| `src/atlas/web/__init__.py`, `web/app.py` | Optional FastAPI app; authenticated read-only APIs |
| `web/templates/login.html`, `dashboard.html` | Local login and six-screen responsive workspace |
| `web/static/atlas.css`, `atlas.js`, `login.js` | Navy/teal terminal design, mobile navigation, interactions and chart rendering |
| `web/static/vendor/*` | Pinned Chart.js 4.5.1 browser distribution, MIT license and integrity manifest |
| `tests/test_activity_history.py` | Persistence, migrations, rollback, restart, idempotency, accounting and causality |
| `tests/test_dashboard.py` | API schemas, access controls, cookies, binding, filters, pagination, units and read-only routes |
| `tests/test_notifications.py` | Mocked delivery, retries, rate limiting, redaction, preview, deduplication and discovery |
| `tests/test_phase2c_cli.py` | CLI persistence/setup/backup, migration, gap alerts, missing FX and snapshot consistency |
| `docs/phase2c_architecture.md` | Design, boundaries, events, clocks, persistence and security |
| `docs/dashboard_user_guide.md` | Laptop/mobile setup, metrics, backup/reset, APIs and screenshots |
| `docs/telegram_setup.md` | BotFather, secure local discovery/test, opt-in delivery and limits |
| `docs/phase2c_report.md` | This evidence |

The web/template/static paths above are relative to `src/atlas/`.
Modified: `backtest.py` (optional observational callback only), `main.py` and
`research_cli.py` (commands/persistence integration), `pyproject.toml`,
`tests/conftest.py` (temporary database isolation and external-HTTP test guard),
`.env.example`, `.gitignore`, README, architecture and roadmap documentation.

Existing acceptance/broker test assertions were not changed or removed.
Existing Phase 2B remediation edits in the working tree were preserved.

## What the user sees

Six screens: overview, open positions, confirmed simulated trade history, risk
monitor, bot activity and system health. Desktop sidebar becomes a mobile menu.
Search, side filtering, sortable timestamp/instrument/side columns, pagination
and trade details operate on actual persisted confirmed fills.

The twelve overview cards expose configured starting capital, equity, available/
invested/reserved cash, realized/unrealized/total P&L, total return, today's P&L,
open positions and completed positions/trades. Currency is GBP with native
GBX/GBP/USD fill and position prices kept explicitly distinct. Unavailable FX
is N/A with a reason. Historical data never invents today's performance.

Equity, P/L and drawdown charts use actual snapshots; gaps are broken, UTC
tooltips identify time, and insufficient day/week/month ranges stay unavailable.
Risk displays actual provisional settings, open planned risk, daily realized loss,
exposure, rejections, gap-loss warnings and as-of kill state. Remaining budget
is N/A for historical snapshots. Margin is zero/not applicable; leverage is None.
The offline engine is STOPPED after completion, not falsely continuously scanning.

## Commands and actual verification

All Python/tool commands used `.\.venv\Scripts\python.exe` from the project.

| Command | Actual result |
| --- | --- |
| `-m pytest -q` | **373 passed, 1 warning in 17.85s; exit 0** |
| `-m pytest -q tests/test_broker.py tests/test_phase2b_acceptance.py tests/test_phase2b_remediation.py` | **66 passed in 0.56s; exit 0** (40 broker + 3 acceptance + 23 remediation) |
| `-m ruff check .` | All checks passed; exit 0 |
| `-m ruff format --check .` | Passed; 50 files already formatted after this report was added |
| `-m atlas.main research-check` | Passed, GBP 1,000 with GBP 5 / 10 / 10 budgets; exit 0 |
| `-m atlas.main synthetic-backtest` | Passed, reconciled persisted actual fixture; exit 0 |
| `-m atlas.main dashboard-check` | SQLite schema 2 and integrity OK; user password setup required; exit 0 |
| `-m atlas.main notifications-preview` | Offline confirmed synthetic exit preview; no credentials/message/network; exit 0 |
| `-m pip check` | No broken requirements found; exit 0 |
| `local/phase2c_browser_smoke.py` (ignored verification artifact) | Temporary authenticated loopback server + installed headless Chrome: all six screens, trade details, 12 cards, 3 charts; zero JavaScript exceptions; exit 0 |

There are **81 additional Phase 2C test cases**, preserving all 292 earlier cases.
The sole warning is Starlette's TestClient deprecation notice for its HTTPX
compatibility path. Tests pass; the existing broker's HTTPX 0.28.1 was preserved
rather than silently migrating its client. No warning filter suppresses it.

The browser test ran desktop 1440×1100 and mobile 390×844. Desktop content width
1425 fit its viewport; mobile body width was exactly 390 (no horizontal overflow).
Mobile menu open/close and trade-detail navigation passed. The temporary server/
browser were stopped, the test session revoked, and a process check confirmed
zero remaining Atlas preview processes. No browser driver or Node tooling was installed.

Local previews: `local/phase2c-preview/atlas-desktop.png` and
`local/phase2c-preview/atlas-mobile.png`. They are ignored artifacts; manual capture
instructions are included in the user guide for a fresh checkout.

## Actual synthetic sample

**SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE**

| Metric | Persisted default XLON result |
| --- | ---: |
| Starting virtual capital | GBP 1,000.00 |
| Ending equity / available cash | GBP 1,004.56 |
| Completed simulated trade | 1 (two confirmed fills) |
| Gross P&L | GBP 4.90 |
| Actual simulated costs | GBP 0.34 |
| Realized/total net P&L | GBP 4.56 |
| Observed maximum drawdown | GBP 2.06 |
| Final open positions / reserved cash | 0 / GBP 0.00 |

Final visual-smoke run ID:
`8e03c19c3ee6cb93e97ffc3d91758ed68af5648648730be7804a991bd72b99b9`.
Dataset SHA-256:
`73e70678a73326078377c21dbee8b76ac072056f1c6766b241546d61a17f08b8`.
Core research configuration SHA-256 remains:
`f2cb154dd96a1a111ffd421d4ec91d352ed46446883e7e56e79ac7f30a8bbb6e`.

These executed fixture observations establish software behavior, not a profitable
strategy. Repeated identical inputs/code reuse the same history; explicit new
experiments never combine their GBP 1,000 ledgers into a fake cumulative return.

## Dependencies and setup

Approved project-local installation succeeded using `pip install -e '.[dev,dashboard]'`.
Pydantic is an explicit base dependency for persisted contracts; the web server/
templates remain an optional extra. A subsequent editable metadata refresh used
the declared isolated build backend successfully. An offline no-build-isolation
attempt had found no runtime setuptools; it did not change system Python.

| Tested dependency | Version |
| --- | --- |
| Python | 3.14.10 |
| FastAPI | 0.162.0 |
| Jinja2 | 3.1.6 |
| Uvicorn | 0.54.0 |
| Pydantic | 2.14.1 |
| Starlette | 1.10.0 |
| HTTPX | 0.28.1 |
| pytest | 9.1.1 |
| Ruff | 0.17.2 |
| Vendored Chart.js | 4.5.1 |

Chart.js npm package SHA-512 integrity was checked before extracting its browser
build and license. The vendor SHA-256 is
`ecc3cd1eeb8c34d2178e3f59fd63ec5a3d84358c11730af0b9958dc886d7652a`.
There is no runtime CDN, external font, paid feed, AI service, cloud or Node build.

## Start, phone access, Telegram and backup

First create your own masked local password, then start the default local site:

```powershell
.\.venv\Scripts\python.exe -m atlas.main dashboard-setup
.\.venv\Scripts\python.exe -m atlas.main dashboard
```

Open `http://127.0.0.1:8000`; stop with Ctrl+C. The default user database is seeded
with actual research, but no default/unknown workspace password was installed.

Phone access requires the same trusted Wi-Fi, a specific private laptop IP,
explicit `--lan --host <PRIVATE_IP>` and authentication. Unencrypted HTTP additionally
requires `--allow-insecure-lan`; TLS certificate/key configuration is supported.
No public/wildcard bind, firewall rule, router forwarding or tunnel is enabled.
See [the user guide](dashboard_user_guide.md) for exact commands and limitations.

For your own explicitly authorized real Telegram test, create a dedicated bot
through BotFather, then use the masked nonce-based helper:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_setup --action test --discover-chat
```

Nothing sent automatically during startup or tests. Real delivery is not claimed
successful. [Telegram instructions](telegram_setup.md) explain queuing with
`--notify-synthetic` and running the separate sender. Retried network delivery
can duplicate a message after an ambiguous response; it cannot duplicate fills.

Data is stored privately under `local/atlas-history.sqlite3`. Use
`history-backup --backup-path local/backups/<NEW_NAME>.sqlite3` for SQLite online
backup; do not copy only a live main database file without WAL state. Fresh
history can use a new database path without deleting source or old data.

## Security and limitations

Source/candidate-file inspection found no credential-pattern matches or
credential/database/log/key candidates. Git check-ignore confirmed database/WAL,
preview artifacts, virtualenv, .env and local key exclusions. No private .env
contents were read. A local comparison confirmed broker.py, config.py, risk.py,
broker-check.ps1, test_broker.py and the original acceptance tests are unchanged
from the protected source checkpoint.

No broker/network imports were added to the core replay/portfolio/risk/strategy/
domain/session/data/cost modules. Tests block real HTTPX transport globally and
use mocks for Telegram/broker calls. The dashboard API has only read endpoints
plus authentication; no order, strategy-run, risk-policy or activation endpoint exists.

Authentication uses salted scrypt and opaque hashed-at-rest sessions, origin/
host checks, strict HttpOnly cookies, login limits and bounded queries/bodies.
TLS adds Secure cookies/HSTS; LAN HTTP remains unencrypted. Tokens, private chat
IDs and raw Telegram/broker payloads are never stored or returned to the UI.

Remaining limitations/deferred capabilities:

- Durable history is not in-flight ledger reconstruction, automatic resume or
  a tamper-proof signed audit log.
- The runner is single-instrument/session; there is no continuous scanner.
- Day/week/month performance is unavailable without enough selected-run history;
  separate experiments must not be compounded.
- Unrealized values are carrying-value P&L, excluding future exit fees; native
  position conversion assumes Phase 2B's validated constant fixture FX.
- Underlying calendars, fees and quotes are synthetic, not authoritative live data.
- Quote exits require full liquidity; a triggered illiquid stop may remain open.
- Real Telegram acceptance/phone receipt, physical Wi-Fi access and user TLS trust
  are NOT VERIFIED by mocks or mobile emulation.
- Delivery is bounded best effort, not exactly once. An unavailable database cannot
  provide durable notification delivery.
- Workspace authentication is single-user and is not designed for public exposure.
- No real market data, broker execution, leverage, shorting or later phase is enabled.

No new development phase was begun automatically.

