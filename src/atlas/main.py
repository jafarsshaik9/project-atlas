"""Demo diagnostics and offline synthetic research; no broker execution."""

import argparse
import sys

from atlas.config import AtlasError, Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atlas demo-only, read-only diagnostics")
    parser.add_argument(
        "command", choices=("health", "broker-check", "research-check", "synthetic-backtest")
    )
    parser.add_argument("--scenario", default="orb")
    parser.add_argument("--venue", choices=("XLON", "XNYS", "XNAS"), default="XLON")
    parser.add_argument("--opening-range-minutes", type=int, default=15)
    parser.add_argument("--session-date", default="2026-10-26")
    parser.add_argument("--research-config")
    parser.add_argument("--risk-config")
    parser.add_argument("--cost-config")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not (3, 13) <= sys.version_info[:2] < (3, 15):
            raise AtlasError("Python 3.13 or 3.14 is required.")
        if args.command in ("research-check", "synthetic-backtest"):
            from atlas.research_cli import run

            return run(args)
        settings = Settings.from_environment()
        if args.command == "health":
            from atlas.research import ResearchSettings, VirtualLedger

            research = ResearchSettings.from_environment()
            ledger = VirtualLedger(research)
            print(f"Python {sys.version.split()[0]}; virtualenv: {sys.prefix != sys.base_prefix}")
            print(
                f"Environment: demo; planned simulated capital: "
                f"GBP {research.starting_capital_gbp:,.2f}"
            )
            print(f"Initial virtual ledger cash: GBP {ledger.cash_balance_gbp:,.2f}")
            print(
                f"Planned risk per trade: {research.planned_risk_per_trade_pct}% "
                f"(GBP {research.planned_risk_per_trade_gbp:,.2f}); "
                f"daily loss threshold: {research.daily_loss_threshold_pct}% "
                f"(GBP {research.daily_loss_threshold_gbp:,.2f}); "
                f"maximum concurrent planned risk: {research.max_concurrent_planned_risk_pct}% "
                f"(GBP {research.max_concurrent_planned_risk_gbp:,.2f})"
            )
            print(
                "Provisional research settings; not proven optimal or guaranteed loss limits. "
                "Independent of the Practice account balance."
            )
            print(
                "Credentials: "
                + (
                    "present (unverified)"
                    if settings.api_key and settings.api_secret
                    else "missing"
                )
            )
            print("Broker not contacted. Orders unavailable.")
        else:
            from atlas.broker import Trading212Client

            summary = Trading212Client(settings).account_summary()
            print(f"Demo connection OK; currency: {summary.currency}")
            print(
                f"Total value: {summary.total_value:.2f}; "
                f"available cash: {summary.available_cash:.2f}"
            )
        return 0
    except AtlasError as exc:
        print(f"Atlas: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
