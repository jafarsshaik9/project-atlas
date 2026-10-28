# Approved IEX session gap: 10 March 2025

The full collection stopped after 445 accepted partitions (89 sessions) when
the five-symbol request for 10 March 2025 returned HTTP 200 with no bars and no
continuation token. A separately approved diagnostic re-fetch reproduced the empty
response using one HTTP attempt. Its five partitions remain quarantined.

The user explicitly approved excluding this date for AAPL, AMZN, GOOGL, MSFT and
NVDA and implementing the local change. This approval does not authorize collection
execution. It is recorded in the ignored local file
`local/research/two-year/iex-exclusion-2025-03-10.json`.

The exclusion binds the existing manifest and run ID, the single session date,
all five symbols, the explicit approval, and checksums of the quarantined diagnostic
datasets. Validation requires zero raw and valid bars, with `NO_VALID_BARS` as the
only failing quality finding. It cannot exclude malformed data or replace an accepted
partition. SIP and the pilot cannot use this record.

The original AAPL quarantine remains quarantined. During a future approved resume,
the coordinator references the other four diagnostic quarantines as `EXCLUDED`.
No accepted dataset is overwritten, no new bar is created, and no HTTP request is
made for the excluded date. The run durably binds the exclusion record before
processing it, so subsequent resumes must supply the same record. Other quarantines
still stop the run. Frozen manifests, plans and execution approvals are not rewritten.

Progress distinguishes accepted/completed sessions from excluded sessions and shows
`coverage_complete: false`. Finishing returns `COMPLETED_WITH_GAPS`, with five excluded
partitions. The coverage target is 500 available sessions per symbol out of the original
501 requested sessions. After the existing 89 completed sessions, 411 sessions remain
to process; pagination and retries can increase HTTP attempts. The dry-run request
estimate of 500 describes the whole plan after exclusion, not remaining requests.

Quarantined evidence cannot be selected for research. Backtests must use accepted
partitions only; this session contributes no bars, signals or trades, and reports
must disclose the date gap. No interpolation or cross-provider substitution occurs.

Offline check (no credentials or HTTP):

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --feed iex --manifest local/research/two-year/alpaca-iex-manifest.json --run-plan local/research/two-year/iex-full-run-plan.json --exclusion-record local/research/two-year/iex-exclusion-2025-03-10.json
```

Future resume, only when the user chooses to execute collection:

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --feed iex --manifest local/research/two-year/alpaca-iex-manifest.json --run-plan local/research/two-year/iex-full-run-plan.json --permission-record local/research/two-year/iex-full-approval.json --exclusion-record local/research/two-year/iex-exclusion-2025-03-10.json --execute --resume
```

The existing account/entitlement/execution approval checks and shared quota remain
in force. An exclusion record grants no network access. Preparation runs only the
dry run; it does not execute or resume the collection.
