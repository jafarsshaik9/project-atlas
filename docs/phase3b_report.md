# Phase 3B implementation report

Status: implementation complete; independent acceptance audit still required.

## Delivered

- Read-only accepted-dataset selection and immutable source revalidation.
- Separate VERIFIED and explicitly ASSUMED availability modes.
- Deterministic one-minute chronological projection and five-minute ORB compatibility.
- Strict post-decision, next-minute-open fill model with reported-volume participation cap.
- Explicit USD/GBP rate policy and fully configured Decimal transaction costs.
- Reuse of the existing ORB, independent risk engine and GBP portfolio ledger.
- Equity, P/L, drawdown, costs, trade history, risk decisions and audit trail through
  the existing `BacktestReport`.
- Reproducible dataset/configuration/run fingerprints.
- IN_SAMPLE/OUT_OF_SAMPLE role and pre-window parameter-selection cutoff.
- Offline CLI and example configuration.

## Safety

The implementation imports no broker, notification or HTTP client. It does not call
Massive, mutate accepted datasets, persist dashboard activity, place simulated broker
orders, or send Telegram messages. `VERIFIED` visibility was not weakened. The accepted
AAPL pilot remains unavailable to strict replay because all 390 bars have UNKNOWN
source publication availability.

## Verification

Focused tests cover look-ahead prevention, next-bar fills, strict versus assumed
availability, missing bars/quarantine, zero-volume no-fill behavior, DST/session
boundaries, fees, slippage, FX rejection, risk/accounting reconciliation,
reproducibility, source immutability, out-of-sample separation, CLI behavior and
static no-network/no-broker boundaries.

Final verification on 27 October 2026 completed successfully:

- `pytest -q`: 864 passed (846 accepted Phase 3A-M tests plus 18 Phase 3B tests).
- `ruff check .`: passed.
- `ruff format --check .`: 91 files already formatted.
- `python -m pip check`: no broken requirements.
- Existing `research-check` and `synthetic-backtest` offline smoke commands passed.
- The `historical-backtest` command help and its isolated successful CLI regression
  passed without provider, broker or notification access.
- A read-only CLI smoke against the accepted 390-row AAPL pilot completed in ASSUMED
  mode: all 390 rows were selected, the result remained explicitly exploratory and
  not point-in-time verified, and no opportunity or simulated trade was fabricated.
- The same AAPL dataset in strict VERIFIED mode selected zero of 390 UNKNOWN-
  availability rows and produced no opportunity or trade.
- A deterministic artificial one-minute ORB acceptance fixture completed one full
  simulated round trip. The 15-minute opening range was 99.99-100.16 USD; risk
  approved three shares with GBP 1.5220 planned risk; entry was 100.25 USD on the
  next actionable minute and exit was 100.46 USD. Gross P/L was GBP 0.5040, costs
  were GBP 1.09, realised net P/L was GBP -0.5860, ending cash/equity was GBP
  999.4140, maximum drawdown was GBP 0.6540, and the final ledger reconciled with no
  open position. Repeating the run produced the identical run ID and report.

The successful artificial-data CLI regression creates its database under pytest's
temporary directory and opts in with `--allow-artificial-historical`; it leaves no
project runtime data. Phase 3B changes were kept uncommitted throughout implementation
and verification.

The AAPL smoke also confirmed that valid provider aggregates can contain sub-penny
OHLC values. Atlas now applies the configured tick to its modeled executable quote and
fills rather than incorrectly requiring every historical source trade aggregate to
align to the order tick; a regression covers this distinction.

## Interpretation limitations

The execution model is a conservative research assumption, not proof that a historical
order would fill. A bar-open price plus configured spread/slippage cannot reconstruct
the order book. ASSUMED availability cannot establish point-in-time validity. One AAPL
session cannot support statistical conclusions, parameter selection or profitability
claims. Multi-session portfolio continuity, sourced FX time series, corporate-action
completeness, broader instruments and walk-forward evaluation remain future work.
