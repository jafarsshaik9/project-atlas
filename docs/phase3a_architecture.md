# Phase 3A historical market-data architecture

27 October 2026. Integration plan recorded **before production changes**.

Gate 0: HEAD `8252b6de92ccb28716813d8e2ed8d726c1a4b6e0`, clean working tree,
505 tests passed (one existing warning, 49.05s); Ruff lint/formatting passed.
Reviewed the existing architecture, roadmap, replay assumptions, market contracts,
CLI, dependency configuration, activity persistence and regression safeguards.

## Integration plan and boundaries

1. Add `atlas.market_data`, independent of broker, research history, strategies,
   ledger, risk and Telegram. Reuse UTC validation, venue/currency/quote-unit
   enums and existing Decimal conventions. Do not change their behavior.
2. Introduce historical contracts with nullable original availability and explicit
   KNOWN/UNKNOWN/ASSUMED policy. Existing `MarketBar` requires a received-time
   visibility boundary and cannot truthfully represent unknown historical
   availability, so historical bars remain a separate type. No replay adapter
   or strategy backtest is added in this phase.
3. Add a maintained calendar adapter, subject to dependency installation approval.
   Preserve the existing fixture calendar and kernel interfaces unchanged.
   Record library/version, bounded coverage and effective schedule fingerprints.
   NASDAQ uses the library's explicit US-cash-calendar alias, with that limitation
   visible in provenance rather than pretending a distinct exchange schedule.
4. Implement an original artificial offline provider and a documented external
   REST adapter only behind explicit configuration/invocation. No licence is
   accepted, account purchased, key created or real data requested automatically.
   User confirmed no dedicated provider account. External access remains blocked
   until separate approval and verified entitlements, not a blocker to offline work.
5. Validate raw pages into immutable historical bars, preserving originals and
   reporting malformed rows, gaps, duplicates, partial sessions, unit/session
   errors and potential action-related discontinuities. Quarantine FAIL datasets.
6. Persist raw sanitized pages, normalized rows, manifests, quality reports and
   resumable download jobs transactionally in a **separate** SQLite file under
   ignored `local/market-data/`. Publish a dataset only after the complete job.
   Stable content/version fingerprints exclude operational ingestion time;
   first ingestion timestamps remain truthful on idempotent reload.
7. Extend `atlas.main` with a `market-data` command family. Default commands are
   offline; dashboard startup never downloads data. No dashboard redesign or
   historical ingestion into Phase 2C activity/notification tables.
8. Capture exact dependencies, correct full-extra installation instructions, and
   verify a separate ignored environment if installation is approved. Preserve
   all 505 tests, add isolated historical tests and run all required checks.

## Reusable foundation and explicit limitations

Canonical identity/provider mappings, strict timezone-aware UTC, Decimal values,
GBP/GBX factor-of-100 conversion, availability-aware replay, injectable sessions
and deterministic calendar fingerprints already exist. Research SQLite/events
are architectural references only; historical ingestion must never create fills,
change cash, update research status or queue alerts. A bar is market data, not
evidence of simulated execution or live broker activity.

Historical instruments validate supplied metadata rather than infer unit/currency
from ticker or venue. GBX, GBP and USD remain explicit. Corporate-action and
effective-dated symbol metadata are retained without pretending complete action
coverage, delisted-universe coverage or point-in-time universe membership.

Unknown availability is unavailable to strict replay consumers. Any explicitly
configured bar-end-plus-delay assumption creates different provenance/version
and remains an assumption; ingestion time is not substituted for original
market availability. Conservative delay cannot prove freedom from provider
revisions or future corporate-action adjustments.

Phase 3B/3C, broker execution, strategy/risk/accounting changes and automatic
Telegram delivery are excluded. Provider access, licensing and calendar decisions
will be recorded with their verified documentation and implementation evidence.

## Implemented data path

`market_data.cli -> service.download -> HistoricalProvider.fetch_page -> RawPage`
checkpoints -> `quality.assess -> HistoricalBar + QualityReport -> store.publish`.
`inspect`, `validate`, `coverage`, `visible_bars` and online `backup` read the
historical database; none feeds the research ledger, dashboard or Telegram.
Only the root CLI dispatcher and dependency metadata changed in existing code.

Contracts are frozen validated Pydantic models, using existing Venue, Currency,
QuoteUnit and UTC validation. Providers implement a typed protocol and versioned
capability descriptor. Fixture generation is deterministic and original; its
specification is checked against tests/fixtures/market_data/fixture_specification.json.
Twelve Data is an explicit US-only, mocked external adapter; verified public
exchange documentation lists LSE as EOD, so UK minute requests fail closed.

Separate SQLite schema v1 uses compressed JSON source checkpoints and normalized
Decimal-string bars. Short owner-fenced transactions isolate page checkpoints
from atomic dataset publication. Request job identity includes calendar, provider
descriptor, actions and coverage assumptions. Dataset identity adds actual source
content/quality, while excluding operational ingestion times. Restart/reimport
and corruption/rollback regressions verify these boundaries.

Historical calendars use the approved exchange-calendars extra. Full effective
schedules and source versions are embedded; revalidation uses those schedules
rather than today's possibly revised rules. The calendar is compatible with the
existing session shape, but synthetic defaults and replay are unchanged.

The local exact-version snapshot is requirements/phase3a-windows-py314.txt,
including every approved extra and an explicit build backend. README documents
both ordinary extra installation and the separate verified rebuild procedure.
Version pinning is a platform snapshot, not a cryptographic supply-chain lock.

See [provider/access decisions](market_data_providers.md),
[quality/storage policy](historical_data_quality.md) and
[acceptance evidence](phase3a_acceptance_report.md). Phase 3B requires a separate
gate for actual provider data, availability/revision policy and strategy replay
integration; this phase does not make real-history research claims.

## Acceptance remediation: publication and recovery boundaries

The independent audit's F01–F06 and W01–W02 were reproduced before production
changes using 12 failing regression cases. Remediation stays in historical data;
no risk, accounting, research-worker, broker or notification behavior changes.

Publication snapshots the job and durable pages, validates their contiguous
terminal checkpoint chain and rederives bars/quality from those pages and the
recorded calendar. Supplied bars, ingestion times and reports must match exactly.
The stored request key binds request, calendar, provider descriptor, corporate
actions and coverage declaration. Unbound low-level jobs cannot publish; normal
service calls already supply this context. A final short BEGIN IMMEDIATE checks
ownership, lease, request, cursor, page count and compressed-page fingerprints
again before atomically inserting the dataset and completing the job. No network,
decompression or quality normalization runs inside that write transaction.

Manifest/schema v1 remain readable when their original source job and metadata
satisfy the stricter checks. New normalization version
`historical-normalization-v2` fingerprints the arithmetic policy; manifests also
record the original source job and page checksums. Operational source references
are protected by the manifest checksum, outside semantic content identity.
Refresh deduplication retains the first truthful ingestion timestamps. Read
validation checks source provenance and semantics as well as hashes; legacy or
corrupt datasets that cannot verify fail closed and require reviewed re-import
or restore rather than automatic repair or manifest rewriting.

Explicit `resume_job_id` / `--resume-job` reclaims a compatible interrupted or
expired job with a fresh owner, preserving pages/cursor. Request, provider,
calendar or policy mismatches fail without creating another job. Active workers
remain fenced; completed compatible jobs return the cached dataset. New refresh
and explicit resumption are mutually exclusive.

Read-only CLI paths use URI read-only SQLite connections and never initialize or
migrate missing databases. Coverage is metadata-only with
`integrity=NOT_VERIFIED`; inspect/validate fully verify. Constructor, normal,
backup and exceptional paths close connections deterministically.
