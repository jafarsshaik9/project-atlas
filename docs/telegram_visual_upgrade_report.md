# Telegram visual upgrade

27 October 2026. **PASS — offline/software verification.**

HTML alerts, local PNG cards and an offline fictional gallery are implemented.
The full suite passes: **463 passed, 1 existing warning**. All 398 baseline tests
remain unchanged; 65 presentation/delivery cases were added. No real Telegram
message was sent, so phone receipt of these new designs is **NOT VERIFIED**.
Phase 3 has not started and no broker execution was added.

## Designs

| Event | Presentation |
| --- | --- |
| Confirmed BUY fill | Green BUY FILLED, quantity/price/unit, GBP gross where known and costs |
| Confirmed SELL, missing P/L | Red SELL FILLED; P/L N/A with accounting reason; no profit claim |
| Partial BUY/SELL | Explicit PARTIAL FILL; a partial SELL never claims position closure |
| Full SELL with confirmed positive/negative P/L | Green PROFIT / red LOSS closure; large signed GBP net P/L |
| Risk breach | Amber warning, actual loss and planned risk independently shown when present |
| Kill-switch event | Trading halt title; never implies a dashboard action enabled trading |
| Critical system error | Distinct critical-error heading and static event code |
| Research completion | Summary with supplied equity, P/L and trade count; missing values N/A |
| Explicit connection test | Clearly labelled HTML test; never presented as a trade |

Every delivery retains SYNTHETIC, UTC timestamp, strategy, full event ID and run
ID. Instrument/venue/native currency/quantity/fill/costs appear when available.
GBP reporting comes from the event; no missing FX is invented. GBX prices convert
to GBP major units by exactly 100, separately from USD/GBP reporting. Tick-price
precision is retained; reported GBP amounts are displayed to two decimal places
without changing ledger values. Null accounting P/L is never labelled final.

The existing schema still allows only confirmed simulated fills, and the research
notification allowlist is unchanged. Position-closure alerts use the confirmed
full SELL fill's accounting values; a bare position-transition event cannot
supply a P/L or execution price. The minimal runner's partial-SELL lifecycle
limitation remains; presentation handles constructed validated partial-SELL events.

HTML escapes text before markup, uses supported bold/italic/code tags and never
cuts tags/entities to satisfy limits. Source identifiers remain schema-validated.
Descriptions are bounded with an explicit shortened marker. Oversize image
captions select the complete HTML message, not incomplete financial information.
See the official [HTML contract](https://core.telegram.org/bots/api#html-style),
[text endpoint](https://core.telegram.org/bots/api#sendmessage) and
[photo/caption contract](https://core.telegram.org/bots/api#sendphoto).

## Cards and offline previews

1080×1120 PNG cards use navy surfaces, rounded panels, original geometric branding,
large financial figures, and green/red/amber accents. No charts, returns or
history are fabricated. Event cards are generated in memory from the same
presentation model as their complete HTML caption. Full IDs/details remain in
the caption; cards abbreviate long identifiers for readability.

Pillow 13.0.0 was installed with user approval in `.venv`, under the optional
`cards = ["pillow>=12,<14"]` extra. No Node/browser renderer/GPU dependency was
added. Rendering reads an existing system font or uses Pillow's bundled fallback;
no proprietary font was downloaded/copied/distributed. The fallback retains
explicit currency codes when a glyph is missing.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m atlas.main notifications-design-preview
Invoke-Item '.\local\telegram-previews\index.html'
```

This was actually executed successfully. The fixed Git-ignored output directory
is `D:\Jafar's Work\project-atlas\local\telegram-previews`:

- `index.html`: responsive side-by-side gallery of all nine designs.
- `buy`, `sell`, `profit`, `loss`, `partial`, `risk`, `halt`, `error`, `summary`:
  each has `.png`, `.telegram.html` and `.txt` previews.
- All fixtures are explicitly fictional, including the PNG footer, gallery and
  text-file headings. The preview creates no database/outbox and reads no
  Telegram credentials. It sends nothing and uses no external assets.

Example excerpt from the fictional PROFIT fixture (not research performance):

```text
🧪 PROJECT ATLAS
SYNTHETIC RESEARCH ONLY

🟢 POSITION CLOSED · PROFIT
REALISED NET P/L: +£4.80 GBP
Instrument: FICTIONAL-EQUITY
Exchange: XLON
Side: SELL
Filled quantity: 2 shares
Fill price: £25.50 GBP
Fees / costs: £0.20 GBP
```

The actual Telegram version uses bold figures/headings and code-formatted IDs.
The PNG counterpart is `local/telegram-previews/profit.png`. These invented
values are design inputs, not broker fees, FX recommendations or strategy results.

## Delivery behavior and ownership preservation

Existing stable source events, outbox uniqueness, five-attempt maximum, backoff,
rate gate, leases, delivery audit, token redaction and disabled defaults remain.
HTML is the enabled provider's normal text format. PNGs require the separate
`ATLAS_TELEGRAM_CARDS_ENABLED=true` opt-in; setting it does not enable alerts.
Direct provider `send` remains HTML-only; cards use the durable outbox path.

`telegram_media.py` adds immutable visual-routing receipts using existing
notification event types. It introduces no schema migration or ownership change.
Receipt insertion verifies the existing outbox owner, attempt and SENDING state
inside its short write transaction. Upload happens outside SQLite transactions.
Receipt IDs are separate stable IDs; existing event-ID algorithms are unchanged.

| Outcome | Action |
| --- | --- |
| Card generation fails before upload | Record PHOTO_NOT_SENT; send complete HTML in the same attempt |
| Caption exceeds 1,024 parsed UTF-16 units | Select complete HTML without image upload |
| Telegram explicitly rejects photo | Persist rejection; use existing backoff/rate gate, then HTML on next attempt |
| Telegram 429 | Honor retry_after; next due attempt uses HTML |
| Valid photo success | Persist success then finish original outbox delivery |
| Timeout, 5xx, malformed/ambiguous response | PHOTO_OUTCOME_UNCERTAIN; final failure with no automatic fallback or image retry |
| Crash with upload-start receipt but no durable outcome | Treat as uncertain on restart; send nothing further |
| Crash after durable photo success but before outbox finish | Reconcile SENT on next claim; no second upload/message |

This deliberate exception to automatic fallback avoids duplicating an image
Telegram may already have accepted. Existing bounded text retries can still
duplicate messages after uncertain text responses. Exactly-once delivery is not
claimed. A crash after the start receipt but before the HTTP call can lose an
alert; inspect the phone/audit rather than blindly resetting/requeuing it.
Changing card settings after restart cannot bypass an orphaned upload receipt.

Research ownership and accounting are untouched: SHA-256 comparisons confirmed
no changes to `backtest`, `portfolio`, `risk`, `strategy`, `research`, `sessions`,
`broker`, `history`, `research_history` or `activity`. All baseline ownership
regressions pass. Delivery cannot generate fills, approve risk, restart replay
or place orders.

## Verification actually completed

Commands used `.\.venv\Scripts\python.exe` from the repository root.

| Command | Result |
| --- | --- |
| `-m pytest -q tests/test_telegram_visuals.py tests/test_notifications.py tests/test_phase2c_acceptance.py tests/test_worker_ownership.py` | 110 passed in 17.59s |
| `-m pytest -q`, final | **463 passed, 1 warning in 33.72s** |
| `-m ruff check .` | All checks passed |
| `-m ruff format --check .` | 59 files already formatted, including this report |
| `-m atlas.main research-check` | GBP 1,000, GBP 5/10/10 provisional risk settings; offline checks pass |
| `-m atlas.main synthetic-backtest --no-persist` | Reconciled; GBP 1,004.56 equity, GBP 4.56 net synthetic P/L, GBP 0.34 costs; unchanged output |
| `-m atlas.main notifications-design-preview` | Nine fictional card/HTML/text fixtures and local gallery created; no message sent |
| `-m pip check` | No broken requirements found |
| Git check-ignore for `.env` and preview artifacts | Ignored |

Synthetic backtest figures above are actual offline test output, not fictional
design examples and not evidence of profitability. The one existing warning is
Starlette's HTTPX TestClient deprecation; no dependency/test was weakened to
hide it. Existing tests were not edited, skipped or removed. External HTTP remains
forbidden by the existing autouse test fixture; Telegram responses are mocked.

The 65 new cases cover HTML validity/escaping/limits, GBP/GBX/USD and sub-cent
quote precision, P/L/missing values, partial fills, currency glyph fallback,
PNG values/dimensions, disabled mode, offline preview isolation, multipart photo
uploads, redaction, text fallback, retry/deduplication, interrupted receipts,
uncertain/crashed responses and restart/card-toggle behavior. Profit/risk PNGs
were visually inspected; the first render's missing glyphs were corrected using
already-installed fonts and a portable explicit-code fallback.

Installation notes: the first sandboxed PyPI attempt was network-blocked; the
approved wheel install then succeeded with network permission. An optional
offline editable-metadata refresh failed because setuptools was not installed
in the runtime venv; normal build isolation subsequently succeeded using the
already-declared build dependency. Final editable metadata exposes the cards
extra and `pip check` passes. No system-wide installation/settings change occurred.

## Files changed

- Added `src/atlas/telegram_presentation.py`: shared typed notice projection,
  currency formatting, escaped HTML and parsed-length helpers.
- Added `src/atlas/telegram_cards.py`: local PNG renderer and font fallback.
- Added `src/atlas/telegram_media.py`: durable notification-only routing receipts.
- Added `src/atlas/telegram_preview.py`: fictional fixtures and local gallery.
- Updated `src/atlas/notifications.py`: HTML requests, multipart photo support,
  safe selection/fallback and receipt reconciliation.
- Updated `src/atlas/main.py` and `src/atlas/dashboard_cli.py`: offline design
  preview command, before any history/database initialization.
- Added `tests/test_telegram_visuals.py`: 65 new cases.
- Updated `pyproject.toml` and `.env.example`: optional cards dependency and
  disabled-by-default image flag; no real credentials.
- Updated `docs/telegram_setup.md`, `docs/phase2c_report.md`; created this report.

## Enablement and remaining limits

Follow the [exact PowerShell procedure](telegram_setup.md#explicitly-test-html-and-cards-after-preview):
inspect the gallery, explicitly enable the two non-secret booleans, create an
isolated alert-enabled synthetic run, then invoke the masked local helper for
one connection test and one confirmed-fill delivery. No token/chat ID goes in
chat, command arguments, Git or this report. Stop/restart existing senders to
adopt the upgrade or changed card settings. Existing pending events are not
automatically resent or deleted.

Readiness: **ready for user preview and explicit real Telegram testing**.
Real HTML/photo receipt, physical phone rendering and Telegram client-specific
appearance remain unverified by mocks. Very long captions use HTML only; cards
are summaries, not complete audit records. Default GBP display rounds to pennies.
No durable in-flight ledger recovery, continuous scanner, broker execution or
new market-data integration was introduced. No real/Practice orders or automatic
notifications occurred. Phase 3 requires a separate authorized scope and is not
started by this presentation upgrade.
