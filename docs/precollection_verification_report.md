# Pre-collection verification report

28 October 2026. All performance examples below are **original artificial
fixtures, not market performance**. Alpaca collection was not started, no account
permission record was created, and no broker or subscription endpoints were used.

## Git checkpoint and validation

The seven pending collection-readiness files were reviewed, allowlist-staged,
secret-scanned and committed as:
`0b790634f7613f6bd6be3730143628d05e5a368b`
(`feat(research): prepare gated full historical collection`). The non-force push
to the existing private `origin/main` succeeded; local and remote hashes matched.
The checkpoint passed 1,205 tests, Ruff lint/formatting and `pip check`.

The subsequent diagnostics/configuration changes are included in the final
preparation checkpoint. Their complete suite passed **1,214 tests**, including nine new readiness
tests. Ruff lint, formatting and `pip check` passed. Existing strategy thresholds,
risk rules and accepted market-data/portfolio records were not changed.
Credentials, SQLite files, downloaded data, logs, screenshots, local manifests
and benchmark output remain excluded from Git.

## Collection dry run

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --manifest local/research/two-year/alpaca-dry-run-manifest.json --run-plan local/research/two-year/alpaca-run-plan.json
```

Confirmed AAPL, MSFT, NVDA, AMZN and GOOGL; 501 exchange sessions each;
28 October 2024 through 27 October 2026; 972,450 expected regular-session minute
bars. The frozen manifest and separate run plan remain unchanged. The expected
501 initial requests are not a cap: pagination and retries may add requests.

AAPL 27 October 2026 remains ACCEPTED with warnings, 390 bars, dataset
`31059db0b3252dbd15168065aaae011ac75364ba2fee21cb2ead5b964b1d5f3a`.
The plan references this dataset read-only; manifest checksum remains
`be327cbb3867295cf3efc772eda1e5b2931156f389e0b4f6e0dbc068a95d3fcf`.
Five-symbol pagination, empty/short pages, continuation cycles, duplicate/missing
bars and interrupted-checkpoint recovery passed deterministic offline tests.

No new authenticated market-data requests occurred: the canonical Alpaca ledger
still contains only its original one pilot attempt. Account-specific permission
remains UNVERIFIED. No approval/permission file or full-collection state was created.

## Offline ingestion benchmark

Command used, with a new isolated output directory:

```powershell
.venv/Scripts/python.exe -m atlas.readiness_benchmark --output-dir local/verification/precollection/benchmark-final --sessions 2 --symbols 5
```

The benchmark refuses to overwrite an existing benchmark database. It uses
FixtureProvider provenance, existing calendar/quality contracts and the actual
HistoricalStore persistence and reinspection pipeline; no credentials or network.

| Measurement | Observed |
|---|---:|
| Artificial bars | 3,900: two sessions, five instruments |
| JSON/contract parsing | 0.124 seconds |
| Initial quality validation | 1.464 seconds |
| Persistence, including mandatory revalidation | 4.223 seconds |
| Published-dataset reinspection | 4.820 seconds |
| Total elapsed, including fixture generation/other overhead | 12.892 seconds |
| Throughput | 302.5 bars/second |
| Python traced peak allocation | 10.7 MiB |
| Process peak working set | 118.0 MiB |
| Database growth | 4,530,176 bytes; about 1,162 bytes/bar |
| Linear projection to 972,450 bars | 3,215 seconds, about 54 minutes |
| Linear projected database size | 1.13 GB |

These timings include tracemalloc overhead. The OS process peak also includes
interpreter/imports; it is not the same measure as traced Python allocations.
This small warm-cache fixture sample is not a full-size Alpaca decoder or storage
benchmark. Real source evidence, SQLite growth, disk contention and long backoffs
can change the results. Combine approximately 54 minutes of measured ingestion
projection with the earlier 5–20 minute network estimate: plan roughly one hour,
with 60–90 minutes of headroom as a provisional estimate, not a guarantee.

Repeated integrity validation during publication/reinspection dominates measured
time. It was retained; no unnecessary optimisation or weakened validation was applied.
Artifacts are under `local/verification/precollection/benchmark-final/` and ignored.

## ORB diagnostics and reporting

New read-only CLI:

```powershell
.venv/Scripts/python.exe -m atlas.main research-history diagnose --bundle local/verification/precollection/demo-v2/bundle.json --allow-artificial --output local/verification/precollection/demo-v2/cli-diagnostics.json
```

The output refuses overwrite. It includes opening-range formation/high/low,
breakouts, exact strategy-filter reasons, session eligibility, independent risk
decisions, available cash, position counts/value limits, concurrent-risk limits,
execution outcomes and source-quality warnings. Categories distinguish no breakout,
strategy rejection, risk rejection, unavailable data and simulated execution rejection.
Only observations available at the evaluated timestamp are considered.

Per-partition probes are labelled **INDEPENDENT_PARTITION_PROBE**; they reset the
research account for that probe. They must not be confused with the actual shared
account. `shared_portfolio` contains actual shared replay decisions/rejections,
instrument/session attribution and equity/available-cash valuations. Those cash
valuations are after each observation batch, not invented pre-decision snapshots.

Reporting includes completed position round trips, wins/losses/breakeven, realised
and unrealised GBP P&L, equity and drawdown, per-ticker/month/session counts, zero-trade
partitions, assumptions and chronological walk-forward results. Partial exits are
grouped by their original entry, rather than inflated into separate completed trades.
The underlying engine's exit-intent count is preserved separately.

Synthetic five-symbol, three-session demonstration (19–21 October 2026):

- Shared starting capital GBP 1,000; 12 completed trades, 0 wins and 12 losses.
- Realised net P&L **GBP -6.1920**, unrealised **GBP 0**, final equity **GBP 993.8080**.
- Maximum observed drawdown **GBP 6.7440**.
- Three actual `INSUFFICIENT_CASH` rejections explain the three zero-trade partitions.
- One chronological 1/1/1 training/validation/test fold, using unchanged default ORB.
- Five-minute candles and the existing configured 15-minute opening range; no threshold
  changes were made to manufacture more opportunities.
- ASSUMED availability and fixed GBP/USD conversion are explicitly exploratory.
  Strict VERIFIED mode with unknown publication times selected zero bars and made no trades.
- Corporate-action, survivorship and availability warnings remain visible. One artificial
  fold demonstrates plumbing, not strategy edge or statistical validity.

## Dashboard verification

The real FastAPI/uvicorn app was started with the existing root service environment
and isolated empty/completed history and paper databases. Verification URLs were
`http://127.0.0.1:8765` and `http://127.0.0.1:8766`; temporary servers were stopped
after testing. No original workspace password or portfolio record was reset.

The root environment loader is narrowly allowlisted for dashboard/notification
settings, respects explicit process variables and does not load broker credentials.
The existing workspace auth hash is configured. Since its owner password is not
stored in `.env`, actual owner-password login was not exercised; isolated test
accounts verified the same password/session implementation without requesting secrets.

Verified on both running servers:

- Unauthenticated APIs return 401, invalid passwords fail and valid fixture login returns 200.
- `/`, `/paper`, 12 private APIs (`runs`, `portfolio`, `equity`, `pnl`, `drawdown`,
  `positions`, `trades`, `risk`, `activity`, `health`, `notifications`, `paper`),
  and two JavaScript assets returned valid responses: 16 checks per fixture.
- Portfolio equity/cash/P&L match the persisted replay ledger; flat positions are
  reported correctly, and two buy/sell execution events appear in trade history.
- Completed history has 78 valuation snapshots. Empty history has no invented points
  or valuations; its separate untouched paper account shows GBP 1,000 and no fills.
- Risk/engine state is accurately STOPPED, with historical synthetic freshness labels.
  The isolated paper kill switch persisted as true and was shown by `/api/paper`.
- POST mutation attempts to paper/positions endpoints returned 405. No real-broker
  action endpoint or notification sender is started by the website.
- An actual process restart retained the authenticated session, identical portfolio
  values and exactly the original two execution events, with no duplicate trades.
- Configured token/chat values were absent from checked HTTP responses and fixture logs.

Actual headless Chrome verification also passed; this was **not inferred from HTTP
status codes**. Six views (overview, positions, trades, risk, activity, system)
became visible with no error banner and `History connected`. Completed trade rows
numbered two; empty trade rows numbered zero. Three Chart.js instances rendered
populated or empty datasets appropriately. The completed charts plot 61 unique
timestamp points from 78 actual snapshots; gaps are not interpolated. Screenshots
were visually inspected, including the equity line and synthetic-data warning.

Evidence: `dashboard-http-report.json`, `dashboard-restart-report.json`,
`browser-report.json`, `completed-browser.png`, `empty-browser.png` under
`local/verification/precollection/` (ignored).

## Telegram verification

Saved root configuration is present, enabled, syntactically valid and card-enabled.
Token/chat values were never printed. No owner-approved recipient record could be
established in the configured history store: **zero verified approved recipients**.
Therefore live system-test delivery was **skipped**, as explicitly required when
approval cannot be established. No `getMe`, `getUpdates`, real trade alert, photo
delivery or recipient-list modification occurred. Live authentication/delivery
remains unverified; credentials were not requested again.

Offline mocked tests passed for HTML entry/exit messages, realised P&L, risk-stop,
kill-switch, PNG routing, multi-recipient approval/preferences, provider failures,
retry/error handling and confirmed-delivery restart idempotence. Four example
cards (buy, sell, risk, halt) decoded as 1080×1120 PNG files. No live image-card
messages were sent.

Confirmed/mock-acknowledged deliveries did not duplicate after restart. Telegram
provides no end-to-end idempotency key; an acknowledgement lost after delivery
can still cause a duplicate when the existing outbox retries an uncertain result.
Exactly-once live delivery is not claimed, and live delivery was not tested here.

## End-to-end isolated simulation

One accepted artificial historical partition was replayed using its recorded
calendar and availability assumptions through the existing ORB, independent risk
engine, simulated execution and portfolio ledger. The historical-to-activity adapter
now accepts that recorded calendar and the existing later-observation execution rule.

The chain produced one completed round trip, two fill events, reconciled final
equity/cash **GBP 999.4140**, realised net P&L **GBP -0.5860**, and zero open exposure.
The dashboard matched those exact values. Two mocked Telegram payloads matched
the persisted entry/exit events and exit P&L. Restart reused the same research
identity, preserved portfolio values and did not duplicate fills or acknowledged
notifications.

A separate Phase 4B paper-engine fixture independently ended at **GBP 1,002.424**;
restart preserved its fills/cash, and its persistent kill switch was exercised.
These are separate isolated accounts/scenarios, not a combined or real account.

## Remaining gates

- Personal/non-commercial research and local storage have since been confirmed
  from the existing Support correspondence. See [scoped data-use record](alpaca_data_use_record.md).
  Full two-year historical SIP entitlement remains unconfirmed; the latest Support
  reply says SIP requires a paid subscription. Collection execution remains unapproved.
- Live Telegram verification requires established recipient approval; none was inferred
  merely from a configured chat ID. No recipient list was changed.
- The benchmark is a small instrumented projection and real market performance has
  not been evaluated. Production owner-password login was not exercised.
- Diagnostics, configuration support, tests and this report are included in the final
  preparation checkpoint; generated artifacts remain Git-ignored.
