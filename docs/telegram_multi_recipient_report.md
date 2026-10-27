# Telegram multi-recipient extension

27 October 2026. **PASS — 505 tests, Ruff and offline smoke checks.**
SYNTHETIC RESEARCH ONLY. No real notifications or broker orders were sent during
implementation. No Phase 3 work or dependency installation. Real multi-phone
delivery remains NOT VERIFIED until the owner explicitly tests it.

## Implemented boundaries

- One BotFather bot, up to 50 private recipient records. Fresh nonce verification
  creates PENDING; separate local owner/password approval is mandatory.
- The existing dashboard scrypt password, persisted login limits and short-lived
  authenticated session authorize administration. Each registry mutation checks
  that session inside its SQLite transaction. No Telegram admin/trading commands.
- Persistent private IDs/preferences, explicit synthetic/card/category opt-ins,
  owner disable/remove/resume, and owner-invoked private `/stop` synchronization.
  `/start` cannot enroll/re-enable. There is no background inbox listener.
- Per-recipient/event delivery IDs, retry counters, leases, outcomes, photo
  uncertainty receipts and audit records. One recipient's failure does not mark
  another's delivery sent/failed. A shared one-second bot rate gate remains.
- Existing HTML/PNG rendering and bounded text retry/backoff are reused. Unknown
  photo outcomes stop without auto-resend/fallback, including after restart or
  card-setting changes. Successful durable photo receipts reconcile without a
  second send. Explicit rejection schedules text fallback for that recipient.
- Removal erases the private ID/alias association and cancels unsent jobs;
  historical audit keeps opaque references. An already in-flight HTTP request
  cannot be recalled. Preferences/resume affect future events, not old history.
- Legacy single-chat configuration remains until explicit persistent activation.
  Registry mode never silently falls back to legacy, including during pause or
  after all recipients are removed. Stop older loaded senders before upgrading.

## Persistence and transaction design

Core `HistoryStore`, its schema version 3, research-worker fencing, immutable
source event IDs and event/outbox commit paths are **unchanged**. The extension
uses an independently versioned schema namespace (version 1) in the same private
SQLite database. Unknown recipient-schema versions fail closed.

| Table | Responsibility |
| --- | --- |
| `telegram_config` | Routing activation/pause, public-bot identity digest, sequence boundary, opt-out update cursor |
| `telegram_recipients` | Private chat ID, non-secret alias, PENDING/APPROVED/REMOVED, individual preferences and verification/approval boundaries |
| `telegram_batches` | Fixed fanout decision per queued source event, preventing repeated import/broadcast |
| `telegram_deliveries` | Unique `(event_id, recipient_id)` jobs, stable SHA-256 delivery key, independent attempts/lease/status/media outcome |
| `telegram_delivery_audit` | Idempotent per-delivery/attempt/phase records; no raw API responses or chat IDs |
| `telegram_admin_audit` | UTC owner/subscription actions with opaque recipient references |

The original event outbox remains the immutable publication request stream;
its rows are not rewritten to pretend one global delivery outcome represents
every recipient. Fanout/claims and delivery-result/audit mutations use short
`BEGIN IMMEDIATE` transactions. Network calls/rendering run outside transactions.
The fanout transaction processes at most 25 source events and 50 eligible
recipients each time. No microservice, daemon auto-start or global Python lock.

Activation and approvals capture ingestion-sequence boundaries, independently
of synthetic event time. No earlier source requests are backfilled. Recipient
sets are fixed when a source request is expanded, even if nobody is eligible.
New approval/resume cannot accidentally replay history. Source requests with
prior legacy attempts are not converted into new multicast deliveries.

Each job's photo state is durable before HTTP. An absent outcome after crash
means uncertain, not permission to resend. Job ownership/subscription checks
protect claims, media receipts and finishing; disable/removal cancels jobs and
invalidates an older sender's database authority. Delivery status and audit
finish together or roll back together. Raw HTTP cannot be transactionally undone.

## Privacy and authorization

Chat IDs/preferences are intentionally persisted only in the private registry,
as required by this extension. They are not embedded in research event payloads,
public dashboard status or alerts to other recipients. Dashboard status uses
aggregate recipient-job counts; detailed audit is owner CLI only. The token stays
in local process memory and authenticates only to the official Telegram origin.
The registry binds to a digest of the token's public bot-ID prefix, allowing a
secret rotation for the same bot without enrolling a different bot by accident.

No credential/response body is logged or persisted. All existing payload
allowlists, redaction and secret-shaped-value guards remain. Database backups
now include private IDs/preferences, are not encrypted by Atlas, and must remain
private. Filesystem/account access is trusted; application passwords do not
protect against someone with direct local code/database access.

The intended recipient receives a challenge through a trusted private channel,
never the bot token. Only a matching private human sender satisfies enrollment.
Approval must follow within 24 hours; persisted approval does not expire on
restart. Remote opt-out processing accepts only current private sender-matched
`/stop` or `/atlas_stop` messages from already approved recipients. A durable
update cursor prevents old `/stop` updates undoing a later explicit owner resume.
Opt-outs take effect when the owner runs `sync-opt-outs`; use `disable` for
immediate suspension. Coordinate `getUpdates` consumers during verification/sync.
See the official [updates contract](https://core.telegram.org/bots/api#getupdates)
and [private chat contract](https://core.telegram.org/bots/api#chat).

## Commands and files

Exact two-person setup and all controls are in
[telegram_setup.md](telegram_setup.md#multiple-authorised-private-recipients).

New modules:

- `src/atlas/telegram_recipients_store.py`: schema, authenticated registry, private subscriptions and opt-outs.
- `src/atlas/telegram_recipients.py`: masked owner administration CLI.
- `src/atlas/telegram_fanout.py`: fixed fanout, recipient-job lifecycle, media receipts and aggregate read model.

Updated modules:

- `notifications.py`: registry routing/settings and recipient delivery context.
- `telegram_media.py`: recipient-specific photo receipt delegation; legacy receipts unchanged.
- `telegram_setup.py`: token-only `--recipients` sender and stricter human/private discovery.
- `dashboard_cli.py`: persistent mode detection without requiring an environment chat ID.
- `dashboard_models.py`: aggregate multi-recipient status without private data.

Added `tests/test_telegram_recipients.py`. Original tests were not weakened,
skipped, deleted or edited. Updated the setup guide, Phase 2C architecture/report
and this verification report. No new dependencies or broker endpoints.

## Verification

Initial baseline: **463 passed, 1 existing warning in 40.41s**. New coverage has
42 offline cases: two-chat HTML/PNG delivery, ownership approval/auth rejection,
private-only verification, defaults, categories, disable/remove/restart, delayed
friend enrollment, immutable fanout/deduplication, independent HTTP failures,
five-attempt bounds, uncertain/crashed photo outcomes, text fallback, atomic
audit rollback, concurrent claims, schema guard, bot binding/rotation, opt-out
replay/malformed messages, CLI masking and token-only sender restart.

Final commands used `.\.venv\Scripts\python.exe` from the project root:

| Command | Actual result |
| --- | --- |
| `-m pytest -q tests/test_telegram_recipients.py` | 42 passed in 21.72s |
| `-m pytest -q` | **505 passed, 1 existing warning in 48.40s** |
| `-m ruff check .` | All checks passed |
| `-m ruff format --check .` | 64 files already formatted |
| `local/telegram_multi_smoke.py` | Two verified test profiles, two mocked PNG deliveries, restart/deduplication PASS, portfolio unchanged, real sends 0 |
| `-m atlas.telegram_recipients --help` | All owner commands/options available; exit 0 |
| `-m atlas.main research-check` | GBP 1,000 capital; GBP 5/10/10 provisional risk budgets; offline checks pass |
| `-m atlas.main synthetic-backtest --no-persist` | Reconciled original synthetic result; no broker/network/credentials |
| `-m atlas.main notifications-design-preview` | Nine fictional offline HTML/PNG/text examples; no send |

All 463 baseline tests remain and pass; 42 new cases were added. The existing
warning is Starlette's HTTPX TestClient compatibility deprecation and was not
suppressed or fixed through unrelated dependency changes.
The existing autouse fixture blocks real HTTP; Telegram uses MockTransport.
The ignored `local/telegram_multi_smoke.py` uses a temporary database, artificial
IDs/token, and two mocked photo deliveries, then verifies restart/deduplication
and unchanged authoritative portfolio reporting. No actual credentials or user
database are used by the smoke test.

Existing offline research output remains GBP 1,000 starting capital, GBP 1,004.56
ending equity, GBP 4.56 net P/L and GBP 0.34 costs, reconciled. These synthetic
figures are not evidence of strategy profitability. SHA-256 source comparisons
confirm unchanged backtest, portfolio, risk, strategy, research, sessions,
broker, history, research_history and activity modules.

## Remaining limits

Delivery is best effort. Text retries can duplicate a message after a lost
response; uncertain photo sends deliberately favor no duplicate over guaranteed
receipt. Retries can arrive out of order. Bot-wide service/rate/database failures
can affect all recipients despite separate jobs. Owner opt-out sync is explicit,
not a continuously running command poller. Private SQLite is not encrypted by
Atlas. No real multi-phone receipt was tested in this implementation.
No trading/risk/accounting/research-ownership logic or broker execution changed.
Phase 3 remains separately authorized work and has not started.
