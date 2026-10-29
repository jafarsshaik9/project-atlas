# Phase 6B-5 next-phase authorization roadmap

**Recommendation: REVISE CONTRACT MODEL.** The artificial funding model passes
technical acceptance; real broker/product terms remain incomplete. This is not a
recommendation to open, fund, automate or trade an account.

The proposed next phase, if separately authorized, is **contract-evidence
completion and artificial execution-contract integration**. It must not start
automatically. Phase 6B-6 has not been initiated.

## Gates before a new strategy study

| Gate | Present status | Required evidence |
| --- | --- | --- |
| Specific legal product and account | Unresolved for executable use | Named UK retail entity, product/platform, GBP denomination and documented eligibility without assuming account acceptance |
| Quantity normalization | Partial public facts | Binding minimum, step, lot multiplier, point increment, order/position limits and fee/settlement precision |
| Funding | Artificial model validated | Product margin tiers, leverage ceiling, extra charges, margin-change/close-out semantics; physical currency inventory if deliverable spot |
| Stops and guarantees | Partial descriptions | Exact minimum distance, applicable guarantee, premium, reservation/refund, restrictions and conversion effects |
| Costs | Illustrative or partial | Pair/size-specific spread semantics, commission minima, conversion markup, funding cutoff and charge rules |
| API/automation and data rights | Public capability only | Specific product/platform entitlement, licence, limits, historical retention/export/use rights |
| Price information availability | Unverified | Matched bid/ask size/timestamps, publication/receipt latency, incomplete/gap behavior and historical/prospective provenance |
| Integration | Not authorized or implemented | Artificial engine parity and independent risk/funding tests; no broker order path |
| Fresh OOS | No fresh holdout reserved | Timestamped preregistration before genuinely later observations; complete testing-family declaration |

Public documentation or user-supplied nonsecret contract documents can help close
these gates. No credential, account ID or token should be requested as a substitute
for evidence. Any future broker API request, including unauthenticated requests,
would require separate explicit authorization; none is implied by this roadmap.
Likewise, data collection/history downloads, paper/demo orders, deployment and
leveraged execution remain prohibited.

## Exact proposed offline scope

1. Select one product based on verified specifications and viable size/cost, not
   the old holdout's best performance. Keep all missing fields and source dates
   visible. If no suitable product can be verified, stop research rather than
   filling unknowns with optimistic assumptions.
2. Define a new versioned execution/funding contract using separately verified
   inputs. Retain fixed GBP 1,000, GBP 5/10 planned-risk budgets, one position per
   pair, stress headroom, deterministic priority and no automatic risk increase.
   Any change from full-notional to derivative margin must be explicit, evidenced
   and artificial-only until separately approved.
3. Prove cash encumbrance, fee/premium reservation, GBP principal and P/L conversion,
   gap stress, dynamic margin, multi-position limits and sticky unknown-capital
   behavior with artificial data. Preserve the old engine and all historical
   outcomes as frozen evidence rather than rewriting them for better returns.
4. Resolve publication/latency and quote semantics before claiming executable fills.
   Even a verified API licence does not supply tick-level fill history or justify
   a favorable intrabar path.
5. Only after a separate review of those deliverables should a new strategy study
   be considered. Preregister at most two primary hypotheses and a baseline, one
   fixed testing family and fresh chronological OOS as described in the
   [strategy redesign plan](phase6b5_strategy_redesign.md). It remains a new
   authorization decision, not an automatic continuation.

## Stop conditions

Stop if minimum viable quantity exceeds planned risk/funding, verified fees make
the intended opportunity implausible, data or automation rights are unavailable,
or a defensible contract requires unauthorized account access, leverage activation
or overnight exposure. A successful software test is not a profitability result.

Stock code, datasets and reports remain preserved legacy artifacts. No new stock
strategy or stock-buying/selling research is in the active scope. No real/demo
trades, account changes or Telegram delivery are authorized.
