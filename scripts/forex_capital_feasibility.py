"""Deterministic static artificial books only; never opens broker or historical databases."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from atlas.forex.feasibility import InstrumentContract, Policy, Scenario, _json, allocate, estimate
from atlas.market_data.contracts import digest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "examples/forex-capital-phase6b5.json"


def run(spec):
    contracts = {c["identity"]: InstrumentContract.model_validate(c) for c in spec["contracts"]}
    if len(contracts) != len(spec["contracts"]):
        raise ValueError("Duplicate contract identity")
    rows, estimates = [], []
    for book in spec["books"]:
        requests = tuple(
            (contracts[r["contract"]], Scenario.model_validate(r["scenario"]))
            for r in book["requests"]
        )
        for c, s in requests:
            estimates.append({"book": book["name"], **_json(estimate(c, s))})
        for limit in spec["position_limits"]:
            for name in spec["policies"]:
                p = Policy(
                    name=name,
                    max_positions=limit,
                    initial_capital_gbp=spec["initial_capital_gbp"],
                    trade_risk_gbp=spec["trade_risk_gbp"],
                    aggregate_risk_gbp=spec["aggregate_risk_gbp"],
                )
                rows.append(
                    {"book": book["name"], "position_limit": limit, **allocate(requests, p)}
                )
    result = {
        "version": spec["version"],
        "origin": "ARTIFICIAL_ONLY",
        "historical_strategy_runs": 0,
        "broker_api_requests": 0,
        "executable": False,
        "cases": rows,
        "estimates": estimates,
    }
    return result | {"checksum": digest(result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / "local/verification/phase6b5"):
        raise ValueError("Output must be a new Phase 6B-5 local verification directory")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    first, second = run(spec), run(spec)
    if first != second:
        raise ValueError("Nondeterministic artificial result")
    output.mkdir(exist_ok=False)
    (output / "artificial-results.json").write_text(
        json.dumps(first, indent=2) + "\n", encoding="utf-8"
    )
    seal = {
        "result_checksum": first["checksum"],
        "reproduction_identical": True,
        "fixture_sha256": hashlib.sha256(SPEC.read_bytes()).hexdigest(),
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), ROOT / "src/atlas/forex/feasibility.py")
        },
    }
    (output / "seal.json").write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    keys = [
        "book",
        "position_limit",
        "policy",
        "feasible_positions",
        "reserved_capital_gbp",
        "available_capital_gbp",
        "capital_utilization",
        "notional_exposure_gbp",
        "initial_margin_gbp",
        "planned_risk_gbp",
        "planned_risk_utilization",
        "stress_loss_gbp",
        "post_stress_equity_gbp",
        "post_stress_margin_gbp",
        "stress_free_margin_gbp",
        "stress_margin_sufficient",
        "stress_budget_exceeded",
        "legacy_notional_cap_breached_in_stress",
        "account_closeout_threshold_breached",
        "executable",
        "rejections",
    ]
    with (output / "allocation-summary.csv").open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        for row in first["cases"]:
            values = {k: row.get(k) for k in keys}
            values["policy"] = row["policy"]["name"]
            values["rejections"] = "|".join(
                sorted({r for d in row["decisions"] for r in d["allocation_reasons"]})
            )
            w.writerow(values)
    fields = sorted(set(k for r in first["estimates"] for k in r))
    with (output / "contract-estimates.csv").open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for row in first["estimates"]:
            w.writerow(
                {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()}
            )
    print(
        json.dumps(
            {
                "cases": len(first["cases"]),
                "checksum": first["checksum"],
                "identical_reproduction": True,
                "historical_strategy_runs": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
