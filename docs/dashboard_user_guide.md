# Dashboard user guide

**SYNTHETIC RESEARCH ONLY — NOT LIVE TRADING PERFORMANCE.**

The dashboard reads offline Atlas research. It never places an order or starts a
continuous market scanner. GBP 1,000 default virtual capital is independent of
Trading 212 Practice funds.

## First-time setup on Windows

In VS Code PowerShell:

```powershell
Set-Location "D:\Jafar's Work\project-atlas"
# Dependencies are already installed here. For a fresh approved environment:
# .\.venv\Scripts\python.exe -m pip install -e '.[dev,dashboard]'

.\.venv\Scripts\python.exe -m atlas.main research-check
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest
.\.venv\Scripts\python.exe -m atlas.main dashboard-setup
.\.venv\Scripts\python.exe -m atlas.main dashboard-check
.\.venv\Scripts\python.exe -m atlas.main dashboard
```

At dashboard-setup, create/confirm a unique password of at least 12 characters in
the masked local prompts. Do not send it in chat or put it in a command argument.
Setup requires an interactive terminal. Running setup again changes the password
and revokes all previous sessions.

Open **http://127.0.0.1:8000**, then sign in. Stop the server with **Ctrl+C** in
its terminal. Start it again with the same dashboard command; history remains.
Starting the website does not start Telegram or a research worker.

## Pages and metrics

- Overview: 12 KPI cards; equity/reference, P/L and observed drawdown charts.
- Open positions: actual persisted simulated exposure, native quote units and
  GBP valuation/risk. A flat run has an explicit cash/empty state.
- Trade history: confirmed simulated fills only, instrument/side filters,
  sortable timestamp/instrument/side, bounded pagination and fee/FX detail view.
- Risk monitor: authoritative policy settings, planned open risk, research-day
  realized loss, exposure, rejections, adverse loss alerts and kill-switch state.
- Bot activity: causal event history with filters and pagination.
- System health: database, observed heartbeat, last run/signal/error and
  non-secret notification status.

Select a run in the header. Each run has its own portfolio; switching runs does
not combine their equity or P/L. Identical repeated fixture/config/code runs are
deduplicated; use `--new-run` only to intentionally create another experiment.

Values are GBP except explicitly labelled native prices. **250 GBX = GBP 2.50.**
USD reporting uses the recorded fixture conversion, never a guessed live rate.
Unrealized P/L includes entry costs but not future liquidation fees. N/A gives a
reason when native valuation or history is insufficient.

Today's P/L is N/A unless there is a valuation dated today in UTC; research-day
P/L is separate. The initial single-session fixtures do not provide a daily/
weekly/monthly performance series. Those range controls remain disabled until
sufficient history exists within the selected run.

Charts use persisted snapshots only, break gaps rather than invent observations,
and expose UTC tooltips. At most the latest 1,000 snapshots are returned, with an
explicit truncation indicator. Monetary arithmetic stays on the server.

No heartbeat means STOPPED for the offline runner; an expired in-progress lease
means ERROR/INTERRUPTED. A fresh lease is a short observation window, not a claim
that a process can never have failed. Historical market data always remains
labelled historical. Margin is zero/not applicable; leverage is None.

## Phone on the same private Wi-Fi

Use your laptop's actual Wi-Fi IPv4 address (inspect it without changing settings):

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object InterfaceAlias, IPAddress
```

The following address is an example; replace it with your laptop's private IP.

**LAN HTTP is not encrypted. Passwords and cookies can be intercepted on an
untrusted network.** For an intentionally trusted private Wi-Fi test:

```powershell
.\.venv\Scripts\python.exe -m atlas.main dashboard --lan --host 192.168.1.50 --allow-insecure-lan
```

On the phone open `http://192.168.1.50:8000` and sign in with your workspace
password. No credentials belong in the URL. Responsive layout and menu support
small screens; rotate or scroll tables horizontally for detailed records.

For TLS with a certificate trusted by your devices:

```powershell
.\.venv\Scripts\python.exe -m atlas.main dashboard --lan --host 192.168.1.50 --tls-cert local/dashboard-cert.pem --tls-key local/dashboard-key.pem
```

Use `https://192.168.1.50:8000`. Certificate issuance/device trust is your separate
local setup; Atlas does not alter trust stores. No wildcard/public binding,
automatic firewall rules, port forwarding or public tunnel is configured.
If the phone cannot connect, inspect existing private-network firewall/Wi-Fi
isolation settings yourself. Do not open the service to the public internet.

## Persistence and backup

Default location: `local/atlas-history.sqlite3`, plus SQLite WAL/SHM sidecars.
The private database also contains password/session hashes; protect backups.
Do not copy only the main file while writers are active. Use the SQLite online
backup API through:

```powershell
.\.venv\Scripts\python.exe -m atlas.main history-backup --backup-path local/backups/atlas-2026-10-27.sqlite3
```

The destination must be new; existing files are never overwritten. This command
can take a consistent snapshot while the dashboard/research process is active.
Keep backups private and outside Git. An operating-system/user backup of the
ignored local directory is separate from the GitHub source backup.

To start clean synthetic history without deleting anything, stop the server and
notification worker, choose a new database for all subsequent commands, then
repeat password setup and seed a run:

```powershell
$env:ATLAS_HISTORY_DB = Join-Path (Get-Location) 'local/atlas-fresh.sqlite3'
.\.venv\Scripts\python.exe -m atlas.main dashboard-setup
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest
.\.venv\Scripts\python.exe -m atlas.main dashboard
```

Use the same environment/path in every terminal. Remove that environment variable
to return to the previous database. No source file or old history is removed.

The database restores views, **not an in-flight ledger/process**. A failed lease
does not authorize continuation or trading. No real market-data feed is supplied.

## Read-only APIs and troubleshooting

Authenticated GET endpoints: `/api/runs`, `/api/portfolio`, `/api/equity`,
`/api/pnl`, `/api/drawdown`, `/api/positions`, `/api/trades`,
`/api/trades/{event_id}`, `/api/risk`, `/api/activity`, `/api/health`,
`/api/notifications`. Queries accept selected `run_id`; lists have bounded limit/
offset parameters. Only login/logout are HTTP POST actions.

| Symptom | Action |
| --- | --- |
| Setup required | Run dashboard-setup in your local interactive terminal. |
| No data | Run synthetic-backtest against the same history database. |
| Login rejected | Check password; wait five minutes after repeated failures, or reset locally. |
| Historical/N/A budget | Expected: old fixture state cannot authorize new risk. |
| Worker ERROR after restart | The old lease expired; inspect activity. No running process is inferred. |
| Telegram configured but no phone message | Review outbox status and [Telegram setup](telegram_setup.md); configuration is not delivery proof. |
| Database unavailable/newer schema | Check local permissions/path or use a compatible application; do not delete the database to conceal the error. |

Screenshots generated during verification are in `local/phase2c-preview/`.
You can generate your own after sign-in using Edge/Chrome Developer Tools:
Ctrl+Shift+I, Ctrl+Shift+M for a mobile viewport, then the command menu's
"Capture screenshot"/"Capture full size screenshot". Capture only synthetic
screens; exclude login input and local secret-entry terminals.

