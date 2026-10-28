# Separate free IEX historical collection

The old `alpaca-dry-run-manifest.json` and `alpaca-run-plan.json` still target
**SIP**. They are not converted in place. A `--feed iex` argument against a SIP
manifest is rejected rather than silently changing the data source.

The new planning default is `alpaca-iex`; `--provider alpaca` still explicitly
creates the legacy SIP configuration. Massive remains an explicit fallback.

## Eligibility and separate gates

Reviewed 28 October 2026 against primary documentation:

- [Alpaca market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api)
  lists IEX coverage on the free Basic plan, history since 2016 and 200 historical
  requests/minute. Atlas conservatively retains its 15-minute historical delay gate.
- [Historical bars API](https://docs.alpaca.markets/us/reference/stockbars) explicitly
  accepts `feed=iex`. Its 10,000-row limit is across symbols; short pages may continue.

These statements establish documented Basic eligibility, **not independent
verification of this account's subscription or authenticated IEX access**. The
current task makes no authenticated requests. The approval command requires the
user's explicit Basic-account declaration and records it as `BASIC_USER_DECLARED`.

The scopes remain separate:

| Check | Current status / requirement |
|---|---|
| Personal non-commercial research and local storage | Existing Support confirmation, read from the scoped personal-use evidence record |
| Basic-plan IEX eligibility | Provider-documented; no SIP paid-entitlement condition inherited |
| Account-specific historical IEX access | Not yet tested; requires the separately approved pilot |
| Pilot execution | Explicit interactive user approval, then `--execute`; maximum two lifetime attempts |
| Full execution | Separate interactive approval after a successful IEX pilot, then `--execute` |

The original unresolved SIP entitlement field is not used as the IEX gate. Nor is
the successful SIP pilot used as evidence of IEX access. Personal/commercial-use
restrictions remain unchanged: own individual account, no redistribution or
third-party display, and no commercial use.

No real IEX approval or access record has been created by this preparation task.

## Configuration already prepared offline

- Full manifest: `local/research/two-year/alpaca-iex-manifest.json`.
  ID `85006fe931e9381c843b9dec9a85b189f6ef2ad02409a9c17ae23e52e485c0d9`.
- Full preflight plan: `local/research/two-year/iex-preflight-run-plan.json`.
- Pilot manifest: `local/research/two-year/alpaca-iex-pilot-manifest.json`.
  ID `d5da26b084a5fe523bafeea48f7a947edd638d531adf09b569369c97866aa282`.
- Pilot plan: `local/research/two-year/iex-pilot-run-plan.json`.
- The pilot is AAPL, 27 October 2026, one regular session, at most two HTTP
  attempts total including retries and continuation pages.
- The full manifest covers AAPL, MSFT, NVDA, AMZN and GOOGL, 28 October 2024
  through 27 October 2026: 501 sessions per symbol, 2,505 partitions.

All requests pin `feed=iex`, `timeframe=1Min`, `adjustment=raw`, ascending order,
USD and explicit regular-session UTC boundaries. `asof=-` prevents silent symbol
renaming. Only `https://data.alpaca.markets/v2/stocks/bars` is available; no account,
broker or order API is introduced. `ALPACA_ENDPOINT` is not read or modified.

## Exact workflow — commands for the user, not execution approval from this task

Run from `D:/Jafar's Work/project-atlas`. Manifest creation has already completed;
the following dry runs do not load credentials or make HTTP requests.

### 1. Repeat the safe IEX preflight

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --feed iex --manifest local/research/two-year/alpaca-iex-manifest.json --run-plan local/research/two-year/iex-preflight-run-plan.json
```

### 2. Explicitly approve only the small pilot

```powershell
.venv/Scripts/python.exe -m atlas.main research-history approve-iex --manifest local/research/two-year/alpaca-iex-pilot-manifest.json --run-plan local/research/two-year/iex-pilot-run-plan.json --record local/research/two-year/iex-pilot-approval.json --account-plan basic
```

This requires the user's interactive terminal. Read the displayed feed, scope and
Basic-account/personal-use declaration, then type the exact `APPROVE IEX PILOT`
phrase and run ID shown. There is no `--yes` or noninteractive approval flag.
It records approval only; it sends no request and refuses to overwrite an old record.

### 3. Run that approved pilot

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --feed iex --pilot --manifest local/research/two-year/alpaca-iex-pilot-manifest.json --run-plan local/research/two-year/iex-pilot-run-plan.json --permission-record local/research/two-year/iex-pilot-approval.json --execute
```

If interrupted after state creation, use the same command with `--resume` added.
The original two-attempt lifetime budget is retained, never reset. HTTP 401/403,
quota failures, invalid data and empty sessions stop the run; there is no SIP fallback.

A successful native pilot writes `local/market-data/alpaca-iex/pilot/access.json`.
Full approval checks the accepted IEX dataset and completed HTTP 200 evidence in
the canonical quota. It records **observed single-session access only**, not
unrestricted coverage of every date. A SIP observation cannot satisfy this check.

### 4. Prepare the full run after the pilot

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --feed iex --manifest local/research/two-year/alpaca-iex-manifest.json --run-plan local/research/two-year/iex-full-run-plan.json
```

This is another dry run. It creates a new full execution plan after the pilot so
that the accepted IEX AAPL partition can be explicitly bound for read-only reuse.
The earlier preflight plan remains unchanged. No SIP partition is reused as IEX.

### 5. Record separate full-run approval

```powershell
.venv/Scripts/python.exe -m atlas.main research-history approve-iex --manifest local/research/two-year/alpaca-iex-manifest.json --run-plan local/research/two-year/iex-full-run-plan.json --record local/research/two-year/iex-full-approval.json --account-plan basic
```

This stops if successful IEX pilot evidence is absent. Otherwise it requires a
separate `APPROVE IEX FULL` confirmation for the exact reviewed run ID.

### 6. Full collection and resume — only after the preceding approval

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --feed iex --manifest local/research/two-year/alpaca-iex-manifest.json --run-plan local/research/two-year/iex-full-run-plan.json --permission-record local/research/two-year/iex-full-approval.json --execute
```

```powershell
.venv/Scripts/python.exe -m atlas.main research-history collect --feed iex --manifest local/research/two-year/alpaca-iex-manifest.json --run-plan local/research/two-year/iex-full-run-plan.json --permission-record local/research/two-year/iex-full-approval.json --execute --resume
```

Approvals bind the feed, manifest, run plan, personal-use evidence, observed-access
scope and confirmation timestamp. They expire after 30 days and cannot authorize
SIP or another run. An explicit execution flag remains necessary even after approval.

## Quality, storage and recovery

IEX is a single-exchange feed. Its prices and volumes are **not consolidated SIP**
and may generate different ORB signals. The 972,450 regular-session minute slots
are an upper bound, not a promise of that many observed bars. Missing minutes
remain unknown: no interpolation, zero-volume placeholder or publication timestamp
is fabricated. New IEX-only quality findings expose limited coverage and missing
interval counts as warnings. Empty sessions, duplicates, malformed OHLCV and
out-of-session data still quarantine and stop collection. SIP quality rules remain
strict and unchanged.

Existing backtests form five-minute candles only from five observed minute bars.
Gaps therefore remove candles and can prevent a valid opening range; strategy/risk
thresholds are not weakened to compensate. Publication availability stays UNKNOWN;
VERIFIED replay excludes it. ASSUMED research remains explicitly exploratory.

Provider/instrument IDs are `alpaca-iex` / `ALPACA-IEX-*`. IEX pages, historical
stores, coordinators and observed-access evidence live under
`local/market-data/alpaca-iex/pilot/` or `/full/`. SIP paths are excluded from IEX
run plans, and cross-feed reuse/backtests are rejected. Accepted sources are immutable.

Both feeds share the existing canonical `alpaca-quota.sqlite3`: 180 attempts per
61 seconds, 0.35-second completion spacing, all retries/pages counted. A separate
feed does not create a second account budget. Existing numeric/date Retry-After,
bounded retries, source-cache checksums, raw import checkpoints, per-partition
coordinator, global worker lock and restart recovery are reused.

Expect about 501 initial full-session batch requests plus the pilot and any
pagination/retries. The earlier 60–90 minute instrumented planning estimate is
provisional; sparse IEX data may reduce row counts and processing time. The existing
storage reserve remains conservative. No performance or complete-coverage guarantee
is inferred from the free plan or one successful pilot.

## Verification

Offline tests cover IEX parameters, sparse bars, strict rejection cases, five-symbol
pagination/restart, feed separation, legacy SIP manifest stability, interactive
approval, stale/wrong-scope rejection, pilot caps and separate full approval after
observed IEX access. The real IEX dry runs completed without credential loading.
The original SIP manifest, run plan, pilot database and cached-page file were
hash-checked unchanged. No actual approval/access record, authenticated request,
collection, broker order or subscription change was made during preparation.
