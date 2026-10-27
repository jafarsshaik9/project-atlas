# Telegram setup and delivery

## Multiple authorised private recipients

Verified with **505 passing tests**, Ruff and mocked offline delivery checks.
No real message was automatically sent; actual two-phone delivery awaits your
explicit local test.

Atlas now supports one BotFather token with multiple individually verified,
owner-approved private chats. No `/start`, unsolicited message or forwarded
alert automatically grants a subscription. Adding a verified chat saves it as
PENDING; a separate authenticated owner command is required to approve it.

The owner is the local workspace administrator. Recipient commands require your
existing Atlas dashboard password at a masked prompt and use its persisted login
limits. Your friend needs only the bot's public username and their own Telegram
account; never share your bot token, dashboard password or broker credentials.
No administrator or trading-control commands are accepted from Telegram chats.

### First-time setup for yourself and one friend

Stop old notification workers before activating this mode. Use the same history
database for all commands below (the default is `local/atlas-history.sqlite3`).
If using a custom database, append `--history-db local/your-history.sqlite3` to
**every** recipient, research, setup and sender command.

```powershell
Set-Location "D:\Jafar's Work\project-atlas"
```

Use your existing dashboard password. **Only if this database has no workspace
password**, create one interactively:

```powershell
.\.venv\Scripts\python.exe -m atlas.main dashboard-setup
```

Register your own chat:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_recipients add owner
```

Enter the owner password and bot token at their masked local prompts. Send the
printed `/atlas_setup <random-challenge>` in your private conversation with your
bot, then press Enter locally. The response must come from that private chat's
matching human sender. Neither the token nor chat ID is printed. Registration
alone sends nothing and cannot receive alerts. Explicitly approve it:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_recipients approve owner --synthetic --cards --alerts trades,risk,system,summary
```

For your friend, ask them to open the same bot by its **public username** and
send `/start`. In your owner terminal run:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_recipients add friend
```

Enter your owner password/token locally. Privately give only the new printed
challenge to the intended friend. They send that exact `/atlas_setup ...` text
to the bot from their own private chat. Press Enter in your owner terminal once
they confirm. Verify with your friend that they sent the challenge; then separately
approve the saved candidate:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_recipients approve friend --synthetic --cards --alerts trades,risk,system,summary
.\.venv\Scripts\python.exe -m atlas.telegram_recipients list
.\.venv\Scripts\python.exe -m atlas.telegram_recipients enable-mode
```

Aliases allow 1–40 letters, digits, underscores or hyphens. Listing shows aliases,
opaque internal references, approval state and preferences, never chat IDs or
tokens. Verification expires after 24 hours if not approved; remove/re-register
an expired candidate. Approval/administration never sends a message.

### Queue new synthetic research and explicitly start delivery

Synthetic alerts require **all** of: explicit research queuing, enabled sender,
runtime synthetic opt-in, and each recipient's `--synthetic` preference. Ordinary
backtests and dashboard startup still do not send alerts. PNG cards additionally
require the sender flag below and that recipient's `--cards` preference.

```powershell
$env:ATLAS_TELEGRAM_ENABLED = 'true'
$env:ATLAS_TELEGRAM_CARDS_ENABLED = 'true'
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --new-run --notify-synthetic --notify-summary
.\.venv\Scripts\python.exe -m atlas.telegram_setup --recipients --action worker
```

The sender prompts only for the bot token; approved chat IDs/preferences survive
restarts in SQLite. It does not ask your friend for any secrets. Stop with Ctrl+C.
For one due **recipient/event delivery**, use `--action send` instead of worker:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_setup --recipients --action send
```

An explicitly invoked HTML connection test is also available:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_setup --recipients --action test
```

This processes one eligible recipient delivery. Use the worker to drain remaining
recipient/event deliveries; it does not claim delivery to both just because the
first succeeded. Connection tests use the `system` subscription category.
The environment-configured equivalents (`notifications-send/test/worker
--allow-synthetic`) automatically detect persistent recipient mode and require
only `ATLAS_TELEGRAM_BOT_TOKEN` plus enabled settings, not `ATLAS_TELEGRAM_CHAT_ID`.

### Disable, remove, resume or change subscriptions

All these commands require the masked owner password:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_recipients disable friend
.\.venv\Scripts\python.exe -m atlas.telegram_recipients preferences friend --synthetic --cards --alerts trades,risk,system
.\.venv\Scripts\python.exe -m atlas.telegram_recipients preferences friend --alerts none
.\.venv\Scripts\python.exe -m atlas.telegram_recipients remove friend
.\.venv\Scripts\python.exe -m atlas.telegram_recipients deliveries
```

`preferences` explicitly resumes an approved recipient and **replaces** the whole
subscription: omitted `--synthetic` or `--cards` means off. Categories are `trades`
(confirmed fills), `risk` (breaches/halts), `system` (errors/tests), and `summary`.
`--alerts none` opts out of all categories. Disable/removal/preferences changes
cancel outstanding jobs for that recipient; completed deliveries are not resent.
Removal erases the private chat ID and alias association, retaining opaque audit
references. Re-adding requires fresh verification and approval. No older queued
history is backfilled to new/re-enabled recipients.
This is logical removal, not forensic secure erasure; old backups, WAL or unused
SQLite pages can retain earlier values.

Recipients may send `/stop` or `/atlas_stop` privately. The owner applies those
requests explicitly, without sending any reply:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_recipients sync-opt-outs
```

This asks for the owner password and token, reads at most 100 updates, and accepts
only current, matching private-sender opt-outs from approved recipients. Repeat
to drain a larger backlog. **There is no background inbox poller:** an opt-out
takes effect when this sync runs, or immediately when the owner uses `disable`.
Ask the owner to disable directly if immediate suspension is needed. `/start`
never re-enables a disabled subscription; the owner must explicitly resume it.
Stop competing `getUpdates` consumers while verifying chats or syncing opt-outs.

Pause/resume delivery globally without falling back to legacy targeting:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_recipients pause
.\.venv\Scripts\python.exe -m atlas.telegram_recipients enable-mode
```

### Persistence, privacy and compatibility

Recipient mode is an explicit, persistent **one-way routing choice for this
history database**. Pause or removal never silently falls back to an environment
chat ID. Existing single-recipient commands remain compatible until you activate
recipient mode. Previously transient single-chat discovery cannot be recovered
across restarts; verify/approve yourself once to enroll persistently. Use
`--recipients` thereafter, rather than `--discover-chat` on the sender.

Activation and approval record event-sequence boundaries. Existing queued/sent
legacy events are not automatically broadcast; queue a fresh `--new-run` after
enrollment. Each new eligible source event gets a fixed recipient set and unique
`(event, recipient)` delivery key. Retries, failures and uncertain photo receipts
are independent; one friend's rejected delivery cannot mark yours sent/failed.
The global bot rate gate still applies. Retries can arrive out of order.

Private recipient IDs/preferences and delivery/admin audit live in the same
Git-ignored SQLite file, under separate version-1 `telegram_*` tables; core history
schema remains 3. The bot token is never persisted. Only a digest of the bot's
public identity binds the registry, so token rotation for that bot preserves
subscriptions. Do not use a different bot's token with the same registry.
Public research events, dashboard responses and recipients' messages do not
include other chat IDs/subscriptions. Dashboard notification counts reflect
recipient deliveries in this mode; detailed delivery audit is owner CLI only.

IDs are private data stored locally, **not encrypted at rest**. Restrict access
to the Windows account/database and protect backups; Git-ignore alone is not
access control. Existing history-backup uses SQLite's online backup and includes
these tables. Local users with direct database/code access remain trusted; the
password protects the application workflow, not a hostile operating-system user.
Stop older loaded workers before this upgrade: an old interpreter cannot be
retrofitted with the new routing guard. Cancellation cannot recall an HTTP send
already in flight. Token/response details are never included in the audit.

All original bounded retries/HTML fallback/photo uncertainty rules below apply
**per recipient**. No real notification was sent during implementation. See
[the multi-recipient report](telegram_multi_recipient_report.md) for verification.

## Earlier single-recipient and visual-upgrade instructions

The sections below describe the compatible single-recipient path before enabling
registry mode. For multiple recipients, use the owner-controlled procedure above.

**Ownership remediation (27 October 2026): PASS.** Superseded/expired research
workers cannot append terminal run events or enqueue misleading critical alerts.
Both original regressions and the 398-test remediation suite passed. See
[the acceptance audit](phase2c_acceptance_audit.md). The subsequent visual upgrade
adds HTML alerts and opt-in PNG cards; **463 tests now pass**. See the
[visual upgrade report](telegram_visual_upgrade_report.md).

The user has confirmed dashboard and Telegram operation. This audit did not
repeat a real send; ownership remediation was verified offline with mocked delivery.
This visual upgrade was verified offline with mocked Telegram responses. No real
HTML/card message was automatically sent. Notification enablement still defaults
to false, and cards have an additional explicit opt-in.

## Designs and optional dependency

Alerts use Telegram HTML: bold titles/financial figures, readable sections,
Unicode status indicators, escaped text and full event/run IDs. BUY uses green;
confirmed SELL and losses use red; profitable closures use green; risk/halts
use amber. System errors and research summaries have distinct titles. All retain
the SYNTHETIC label. Missing P/L/FX shows N/A; partial SELL fills never claim the
whole position is closed. Signal/intent events are not trade notifications.

PNG cards cover confirmed fills/full SELL closures, risk warnings, halts and
errors. Optional summary/connection-test delivery remains HTML. Cards use local
event values, navy backgrounds, large figures and original drawn branding;
they contain no invented price/equity curves. Full details accompany the card
in its HTML caption. Long captions select a complete text message instead.

Install the project-local optional extra on a new checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev,dashboard,cards]'
```

Pillow is the only new runtime dependency (`cards` extra, tested with 13.0.0).
Rendering reads an already-installed Segoe UI/DejaVu font when available; fonts
are never downloaded or redistributed. The portable bundled-font fallback uses
explicit ASCII currency codes when a currency glyph is unavailable.

## Create and verify your private recipient

1. Open the official [BotFather](https://t.me/BotFather) in Telegram.
2. Create your own dedicated bot with /newbot. Keep its token private.
3. Open a private conversation with that new bot and send /start.
4. First persist a synthetic run locally, then invoke the secure helper:

```powershell
Set-Location "D:\Jafar's Work\project-atlas"
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest
.\.venv\Scripts\python.exe -m atlas.telegram_setup --action test --discover-chat
```

Enter the token at the masked prompt **in your terminal only**. The helper prints
a random /atlas_setup challenge. Send that exact challenge in your private
conversation with your bot, then press Enter in the terminal. Discovery accepts
only one matching private chat whose sender ID matches the private chat ID.
It never displays or saves the token, chat ID or raw Telegram response.

The helper temporarily sets `ATLAS_TELEGRAM_ENABLED`,
`ATLAS_TELEGRAM_BOT_TOKEN` and `ATLAS_TELEGRAM_CHAT_ID` in its own process,
invokes the requested command, and restores the previous environment in finally.
It does not create a credential file or change system/user environment settings.
The standalone notifications commands accept these same locally configured
environment variables. Atlas does not load .env automatically.

The test sends **one clearly labelled synthetic connection-test message**, not a
trade. It records delivery activity without changing cash/positions. It reports
DELIVERED only after the API returns a valid successful message result. Also
check receipt on your phone. A queued/rate-limited/failed test is not success.

Use a dedicated new bot without a webhook or other getUpdates consumer. Discovery
reads recent unconfirmed updates without publishing a raw token-bearing URL.
If it cannot identify the challenge, it stops without choosing another recipient.
Do not paste a Bot API URL containing your token into a browser, issue or chat.
The [official getUpdates contract](https://core.telegram.org/bots/api#getupdates)
explains its webhook exclusion and update behavior.

If you already securely know your private chat ID, omit --discover-chat; both
token and ID are entered in masked local prompts.

## Offline preview

```powershell
.\.venv\Scripts\python.exe -m atlas.main notifications-preview
.\.venv\Scripts\python.exe -m atlas.main notifications-design-preview
Invoke-Item '.\local\telegram-previews\index.html'
```

This formats the latest confirmed simulated fill or critical alert in the selected
latest run. It does not send, claim an outbox item or read Telegram credentials.
Signals and unfilled order intents are never labelled executed trades.

`notifications-design-preview` is independent of the history database and creates
nine **fictional** designs: BUY, SELL with missing P/L, PROFIT, LOSS, PARTIAL, RISK,
HALT, ERROR and SUMMARY. Open the index on your laptop to compare cards and HTML;
the layout adapts to narrow screens. PNG, `.telegram.html` and readable `.txt`
files are saved under Git-ignored `local/telegram-previews`. It works without
credentials, internet, broker access or a Telegram account. Re-running overwrites
only these generated design previews; it does not alter research history.

## Explicitly test HTML and cards after preview

These commands are **instructions for you**, not actions already run by this
upgrade. The helper requests secrets only at masked local terminal prompts.
This separate database avoids sending an old pending notification from your
normal research history. Creating the run queues two simulated-fill alerts;
it does not place an order or send a message.

```powershell
Set-Location "D:\Jafar's Work\project-atlas"
$env:ATLAS_TELEGRAM_ENABLED = 'true'
$env:ATLAS_TELEGRAM_CARDS_ENABLED = 'true'
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --history-db local/telegram-design-check.sqlite3 --new-run --notify-synthetic
```

First send one HTML connection test, then one due confirmed-fill card:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_setup --history-db local/telegram-design-check.sqlite3 --action test --discover-chat
.\.venv\Scripts\python.exe -m atlas.telegram_setup --history-db local/telegram-design-check.sqlite3 --action send --discover-chat
```

Verify receipt on your phone. The connection test intentionally stays text; it
never masquerades as a fill. Repeat the explicit send command for the second due
fill. A known photo rejection schedules text fallback through the existing
backoff; wait for its due time and invoke send again, or explicitly run the worker.
No redesigned-message or card delivery is claimed proven until this real test
succeeds. Do not paste tokens/chat IDs into chat, Git or a token-bearing URL.

To return future deliveries to HTML only, set
`$env:ATLAS_TELEGRAM_CARDS_ENABLED = 'false'`. Stop an already-running sender with
Ctrl+C and restart it to adopt changed settings. This flag does not enable
notifications by itself. The standard setup/sender commands below remain valid.

## Explicitly enable synthetic alerts

Ordinary backtests and server startup do not send or enqueue notifications.
In the research terminal, enable only the non-secret boolean and intentionally
create an alert-enabled independent experiment:

```powershell
$env:ATLAS_TELEGRAM_ENABLED = 'true'
.\.venv\Scripts\python.exe -m atlas.main synthetic-backtest --new-run --notify-synthetic
```

The optional `--notify-summary` adds a run-completion summary when used together
with --notify-synthetic. Repeating an already persisted run does not add new
notifications. --new-run explicitly creates a separate GBP 1,000 experiment,
not cumulative portfolio performance.

In a second terminal, securely configure the sender and run it:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_setup --action worker --discover-chat
```

For one due outbox delivery rather than a persistent worker:

```powershell
.\.venv\Scripts\python.exe -m atlas.telegram_setup --action send --discover-chat
```

The corresponding commands for an already locally configured process are
`notifications-test --allow-synthetic`, `notifications-send --allow-synthetic`
and `notifications-worker --allow-synthetic`. These are explicit network actions;
valid token/ID, enabled configuration and synthetic opt-in are all required.

Stop a worker with **Ctrl+C**, then set the research terminal's boolean back to
false to stop queuing future runs. Environment changes in another terminal do
not stop an already running worker. Old opted-in pending messages remain in the
outbox until an explicitly enabled worker handles them.

## Delivery guarantees and privacy

Supported notifications: confirmed simulated entry/exit, partial fill and realized
net P/L, risk breach (including loss beyond planned risk), kill-switch activation,
critical system error, and optional completion summary. They carry synthetic
labels, instrument/unit, GBP reporting, side/quantity/price where applicable,
strategy, UTC timestamp, event/run IDs, actual fees and realized P/L.
The current engine never fabricates a kill-switch event or a market scan.

The provider calls only official HTTPS sendMessage/sendPhoto/getUpdates methods, verifies
TLS, refuses redirects and proxy discovery, applies timeouts, and shows static
sanitized errors. Token-bearing transport logs are redacted; never enable
unreviewed HTTP/body debugging. HTML uses the
[supported tags](https://core.telegram.org/bots/api#html-style). Complete text
stays below the [sendMessage limit](https://core.telegram.org/bots/api#sendmessage);
cards use multipart [sendPhoto](https://core.telegram.org/bots/api#sendphoto) with
captions limited conservatively to 1,024 UTF-16 units after parsing. No HTML is
blindly sliced. Images and local render inputs are never uploaded anywhere else.

Outbox entries are unique by event. Transactional claims, a one-second global
gate, leases and five maximum attempts survive restarts. Persistent exponential
backoff honors Telegram retry_after. A final crashed attempt becomes an explicit
uncertain failure rather than an unbounded retry.

**Exactly-once network delivery cannot be guaranteed:** Telegram might accept a
text message and lose its response. Existing bounded text retries can duplicate
that message. Notification retries never rerun ledger fills or alter accounting.

Photo routing adds stable notification-audit receipts, without changing the
outbox schema or research ownership. An upload-start receipt commits before HTTP.
If a valid successful photo response is durably recorded but outbox completion
is interrupted, restart reconciles it without sending again. A missing/uncertain
photo outcome (timeout, 5xx, malformed success or crash) becomes
`PHOTO_OUTCOME_UNCERTAIN`: **no photo retry and no automatic text fallback**.
Check the phone and audit history; do not blindly reset/requeue that event.
This deliberately favors avoiding duplicate alerts over guaranteed delivery.

Local generation failures fall back to complete HTML immediately. Explicit
photo rejections schedule HTML fallback at the next bounded outbox attempt,
respecting the existing backoff/rate gate. A 429 honors retry_after, then selects
HTML. Missing Pillow or captions exceeding the photo limit also select HTML.
Once a photo was rejected, retries stay text. Turning cards off cannot cause
an uncertain orphaned upload to be resent as text. The rare crash before HTTP
but after its start receipt may therefore lose an alert; this is documented
best-effort behavior, not an exactly-once guarantee.

Bot tokens and raw responses are not stored in SQLite, logs or Git. The legacy
single-recipient flow keeps chat IDs only in process memory; explicitly enrolled
multi-recipient IDs/preferences are stored in the private registry described above.
Public status records store only configuration presence, static codes, counts and
event references. A last observed enabled configuration is not evidence of a
currently active worker. A broken/unavailable database cannot provide durable
outbox delivery; the CLI fails clearly rather than claiming success.

Do not share the bot/token with other automation or give it trading authority.
No broker credential is needed for any Telegram command.

