# Alpaca data-use record — scoped Support confirmation

Recorded 28 October 2026 from the existing Alpaca Support correspondence,
case **383583**, subject **Clarification on historical SIP market data for
personal backtesting**. Both substantive replies were read directly through the
connected mailbox. No emails were sent, labels changed or account APIs contacted.
Customer addresses, raw message headers and mailbox identifiers are not committed.

## Personal-use permission: confirmed within the stated scope

The 17:26:53 UTC reply from Alpaca Support confirms that individual retail users
may use their data package for personal, non-commercial research, backtesting and
development on their own account, and may download/store data locally in personal
files, databases and tools. The 17:41:03 UTC follow-up reiterates the personal/retail
scope. This records Support's existing confirmation; it is not a new grant by Atlas.

The restrictions remain: no business-entity or commercial use, redistribution,
resale, sublicensing, customer-facing products or third-party display of the data.
Local storage permission does not expand the feeds or dates supplied by the plan.
Existing multi-recipient synthetic notification tests do not authorize redistribution
of actual Alpaca data. No Telegram messages are authorized by this preparation task.

## Historical SIP entitlement: unresolved for the full collection

The follow-up was responding specifically to the question of free Basic historical
SIP data older than 15 minutes across the two-year period. It states:

> SIP market data requires the paid subscription.

This is not a confirmation that Basic includes the requested two-year SIP dataset.
It must not be omitted or reinterpreted as only a real-time restriction. Clarification
or applicable entitlement evidence is still needed before full SIP collection.
No subscription status was queried or changed during this task.

The earlier AAPL 27 October 2026 pilot remains genuine observed endpoint access:
one historical SIP GET, HTTP 200, 390 bars, accepted dataset
`31059db0b3252dbd15168065aaae011ac75364ba2fee21cb2ead5b964b1d5f3a`.
That proves the single request succeeded, not unrestricted SIP entitlement, a
verified current subscription tier or successful coverage of all symbols/dates.
No additional authenticated requests were made to resolve the distinction.

## Execution approval: not granted

The current instruction explicitly prohibits full collection. The existing
`--execute` and separately bound permission-record checks remain unchanged.

The scoped private evidence record is:
`local/research/two-year/alpaca-personal-use-evidence.json` (Git-ignored).
It records the two Support source references, permitted personal-use scope, the
unresolved SIP entitlement and the absence of execution approval. It contains no
credentials and deliberately is **not** the runtime gate's approval file.

`local/research/two-year/alpaca-account-permissions.json` has not been created.
The CLI's aggregate `account_permissions: UNVERIFIED` therefore remains correct
for collection activation: recording personal-use permission alone cannot unlock
a feed whose applicable full-history entitlement remains unresolved.

| Check | Status |
|---|---|
| Personal/non-commercial research and local storage | Support-confirmed, restricted scope |
| One-session historical SIP endpoint access | Previously observed: HTTP 200 / 390 bars |
| Full two-year historical SIP entitlement | Unconfirmed; latest Support reply says paid required |
| Real-time or unrestricted SIP entitlement | Not claimed |
| Full collection execution | Not approved; safeguards unchanged |

After entitlement clarification and separate execution approval, the existing
start/resume commands in [full collection readiness](alpaca_full_collection_readiness.md)
apply. They are not authorized to run by this record.
