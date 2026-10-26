# Setup report — 26 October 2026

| Step | Status |
| --- | --- |
| Workspace inspected | Completed; existing empty `Project Atlas` directory left untouched. |
| Python | Installed and verified: 3.14.10, current user. |
| pip | Verified: 26.2.2 inside `.venv`. |
| Git | Installed and verified: 2.56.0.windows.3. |
| Official API authentication/schema/rate limit | Verified against official API and OpenAPI documentation. |
| Project source structure | Created in `project-atlas`, with `src/atlas` layout. |
| Git initialization | Completed on branch `main`; no commit created. |
| Virtual environment | Created at `.venv`; verified Python 3.14.10 and virtualenv=True. |
| Dependency installation | Completed successfully with approved network access. |
| Dependency consistency | `python -m pip check`: no broken requirements. |
| pytest | Latest Phase 2B verification: 266 passed in 1.03 seconds, exit 0; original 40 broker and 40 capital/settings cases retained. All broker requests mocked. |
| Ruff lint | `python -m ruff check .`: all checks passed, exit 0. |
| Ruff formatting | `python -m ruff format --check .`: 27 files already formatted, exit 0. |
| Offline health | Capital reporting now uses configurable research settings, default GBP 1,000; initial risk budgets GBP 5/10/10. Latest verification is recorded below. |
| Actual demo connectivity | User ran the local secure credential script and confirmed `Demo connection OK`. Agent did not receive credentials or independently observe the authenticated response. |
| Credential exclusions | Git verified `.env`, `.env.private`, `.venv`, logs and local config are ignored; `.env.example` is included. |
| PowerShell credential script | Syntax parsed successfully. |

## Installation notes

Installations were approved by the user. Python was initially 3.14.7 from Winget,
then updated to 3.14.10 using the official python.org installer after checking its
published SHA-256 and valid Python Software Foundation Authenticode signature.
The update completed with installer exit code 0.

Python: `C:\Users\Dell\AppData\Local\Programs\Python\Python314\python.exe`.
Git: `C:\Program Files\Git\cmd\git.exe`. Winget was requested to use user scope,
but Git's installer selected Program Files and announced an administrator prompt.
No further system configuration changes were made. Existing terminals may need
restarting to refresh PATH; the README provides executable paths that work now.

The first dependency installation was blocked by sandbox networking. The retry
outside the sandbox completed successfully. Formatting failures were corrected
before the final checks above.

## Installed versions

| Package | Version |
| --- | --- |
| project-atlas | 0.1.0, editable |
| httpx | 0.28.1 |
| pytest | 9.1.1 |
| ruff | 0.17.2 |
| anyio | 4.15.1 |
| certifi | 2026.10.23 |
| colorama | 0.4.6 |
| h11 | 0.16.0 |
| httpcore | 1.0.9 |
| idna | 3.20 |
| iniconfig | 2.3.1 |
| packaging | 26.3 |
| pip | 26.2.2 |
| pluggy | 1.7.0 |
| Pygments | 2.21.0 |
| typing_extensions | 4.16.0 |

## Remaining work

The user previously confirmed a successful local demo check, then reported a
credential-format error on a subsequent attempt. Validation now identifies the
affected field and explains how to correct it without exposing any input. A local
retry with the original raw credentials is pending; their values were not inspected.
The credential script also supports `-UseClipboard` to bypass terminal paste
handling. This path passed PowerShell syntax validation; it still requires the
user's local check with their actual credentials. No real clipboard data was read
by the agent.
Credentials are session-only and must be entered again for subsequent checks.
No orders were placed. The client has no order functionality.

Recommended next task: define market-data requirements and the provider interface,
then implement an offline backtest with transaction costs and risk limits.

## Capital/settings update — 26 October 2026

The current starting virtual capital is GBP 1,000, independent of the Trading 212
Practice account balance. `ResearchSettings` provides configurable initial
percentages: 0.5% planned risk per trade, 1% daily loss threshold, and 1% maximum
concurrent planned risk (GBP 5, GBP 10 and GBP 10). These are provisional research
settings, not proven optimal parameters or guaranteed loss limits. At that update
only an immutable opening cash snapshot and configuration were implemented.
The subsequently approved Phase 2B now adds the local transaction/fill ledger,
independent research risk gate and replay, with no broker order capability.

Verification: pytest 80 passed; Ruff lint and formatting passed; offline health
reported GBP 1,000 opening cash and GBP 5/10/10 provisional budgets. SHA-256 checks
confirmed the broker implementation, broker credential configuration, secure
credential script, dependency file and original broker tests were unchanged.
No authenticated broker check or order was performed during this update.

## Offline Phase 2B implementation

The full implementation, 266-test verification, security review, artifact list,
artificial sample outcomes and limitations are recorded in
[phase2b_report.md](phase2b_report.md). Phase 1 broker files and dependencies are
unchanged. No broker orders or new external connections were enabled.
