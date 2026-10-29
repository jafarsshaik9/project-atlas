"""Developer acceptance smoke: artificial fixtures only; no historical source is opened."""

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_forex_execution import META, OPEN  # noqa: E402
from test_forex_research import fixture_frames  # noqa: E402

from atlas.forex.research.design import DESIGN_SHA, cell_id, load_design  # noqa: E402
from atlas.forex.research.runner import offline_worker, research_source_hash, worker  # noqa: E402
from atlas.market_data.contracts import digest  # noqa: E402


def main():
    design = load_design()
    frames = fixture_frames("ORB")
    states = {
        cell_id("EUR_USD", "LONDON", s, c): {"cash": "1000", "known": True}
        for s in design["strategies"]
        for c in ("C0", "C1", "C2", "C3")
    }
    task = (OPEN.date(), "EUR_USD", "LONDON", frames, META, states, design, "ARTIFICIAL_SMOKE", [])
    with ProcessPoolExecutor(max_workers=2, initializer=offline_worker) as pool:
        first, second = list(pool.map(worker, (task, task)))
    assert first[0] == second[0] and len(first[0]) == 16
    result = {
        "kind": "ARTIFICIAL_FIXTURE_ONLY",
        "design_sha": DESIGN_SHA,
        "implementation_source_hash": research_source_hash(),
        "cells": 16,
        "identical_cross_process_results": True,
        "semantic_results_hash": digest(first[0]),
        "peak_worker_bytes": max(first[1], second[1]),
        "historical_sources_opened": False,
    }
    output = (
        ROOT
        / "local/verification/phase6b3-v1"
        / ("smoke-" + result["implementation_source_hash"] + ".json")
    )
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
