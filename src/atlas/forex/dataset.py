"""Streaming JSONL export/reload with partition proofs, never a whole-dataset buffer."""

import json
from datetime import timedelta
from pathlib import Path

from atlas.config import AtlasError
from atlas.forex.collection import CollectionLedger, validate_plan
from atlas.forex.history import ForexStore, windows
from atlas.forex.models import CandlePlan, FxCandle
from atlas.forex.quality import evaluate
from atlas.market_data.contracts import canonical, digest


def partitions(plan):
    validate_plan(plan)
    ledger = CollectionLedger(plan["output"], read_only=True)
    for row in plan["partitions"]:
        item, manifest, bars = ledger.verified(row)
        yield item["dataset"], manifest, item["quality"], bars


def export_dataset(plan, output):
    """Header per partition avoids repeating metadata/BAM observations on disk."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(
            canonical(
                {
                    "type": "archive",
                    "schema": "oanda-forex-jsonl-v1",
                    "plan_hash": plan["plan_hash"],
                    "partitions": len(plan["partitions"]),
                }
            )
            + "\n"
        )
        count = 0
        chain = plan["plan_hash"]
        for identity, manifest, quality, bars in partitions(plan):
            handle.write(
                canonical(
                    {
                        "type": "partition",
                        "dataset_id": identity,
                        "manifest": manifest,
                        "quality": quality,
                    }
                )
                + "\n"
            )
            for bar in bars:
                handle.write(
                    canonical({"type": "candle", "value": bar.model_dump(mode="json")}) + "\n"
                )
            handle.write(canonical({"type": "end_partition", "dataset_id": identity}) + "\n")
            count += 1
            chain = digest([chain, identity, quality["checksum"]])
        handle.write(canonical({"type": "end_archive", "partitions": count, "chain": chain}) + "\n")


def reload_dataset(path):
    """Yield (dataset_id, manifest, quality, bars) only after partition validation.

    Consumers must exhaust the iterator to validate the final archive trailer.
    Incomplete exports are never silently treated as complete datasets.
    """
    with Path(path).open(encoding="utf-8") as handle:

        def read():
            line = handle.readline(1_000_001)
            if not line or len(line) > 1_000_000:
                raise AtlasError("FOREX_EXPORT_TRUNCATED_OR_OVERSIZED")
            return json.loads(line)

        try:
            header = read()
            if header["type"] != "archive" or header["schema"] != "oanda-forex-jsonl-v1":
                raise ValueError
            chain, count, seen = header["plan_hash"], 0, set()
            while True:
                item = read()
                if item["type"] == "end_archive":
                    if (
                        item["chain"] != chain
                        or item["partitions"] != count
                        or count != header["partitions"]
                        or handle.read(1)
                    ):
                        raise ValueError
                    return
                if item["type"] != "partition":
                    raise ValueError
                identity, manifest, quality = item["dataset_id"], item["manifest"], item["quality"]
                if identity in seen or digest(manifest) != identity:
                    raise ValueError
                seen.add(identity)
                plan = CandlePlan.model_validate(manifest["plan"])
                if plan.end - plan.start > timedelta(days=1):
                    raise ValueError
                bars = []
                while True:
                    record = read()
                    if record["type"] == "end_partition":
                        if record["dataset_id"] != identity:
                            raise ValueError
                        break
                    if record["type"] != "candle" or len(bars) >= 1440:
                        raise ValueError
                    bar = FxCandle.model_validate(record["value"])
                    if (
                        not plan.start <= bar.start < plan.end
                        or not bar.complete
                        or (bars and bar.start <= bars[-1].start)
                    ):
                        raise ValueError
                    bars.append(bar)
                hashes = [
                    digest([b.model_dump(mode="json") for b in bars if a <= b.start < z])
                    for a, z in windows(plan)
                ]
                if (
                    ForexStore._manifest(plan, hashes, bars) != manifest
                    or evaluate(plan, bars) != quality
                    or quality["critical"]
                ):
                    raise ValueError
                count += 1
                chain = digest([chain, identity, quality["checksum"]])
                yield identity, manifest, quality, tuple(bars)
        except (ValueError, TypeError, KeyError):
            raise AtlasError("FOREX_EXPORT_INTEGRITY_FAILED") from None
