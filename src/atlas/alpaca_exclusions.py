"""Reviewed empty-session exclusions; quarantined evidence never becomes market data."""

from datetime import datetime

from atlas.config import AtlasError
from atlas.market_data.contracts import DownloadRequest, digest
from atlas.market_data.store import HistoricalStore


def load_exclusion(path, manifest, plan):
    from atlas import alpaca_collection_cli as cli

    if path is None:
        return None
    record = cli.public_json(cli.local_path(path))
    content = dict(record)
    identity = content.pop("record_id")
    if (
        digest(content) != identity
        or record["schema"] != 1
        or record["repository"] != str(cli.ROOT.resolve())
        or record["manifest_id"] != manifest["manifest_id"]
        or record["run_id"] != plan["run_id"]
        or manifest["provider"] != "alpaca-iex"
        or plan.get("mode") != "full"
        or record["feed"] != "iex"
        or record["approval"] != "USER_APPROVED_SESSION_EXCLUSION_ONLY"
        or record["execution_authorized"] is not False
        or record["reason"] != "REPEATED_EMPTY_PROVIDER_RESPONSE"
        or not record["approval_reference"].strip()
        or datetime.fromisoformat(record["approved_at"]).tzinfo is None
    ):
        raise AtlasError("Session exclusion approval differs from this collection.")
    task = next((t for t in manifest["tasks"] if t["date"] == record["date"]), None)
    if task is None or set(record["evidence"]) != set(task["symbols"]):
        raise AtlasError("Session exclusion must cover exactly all requested symbols on one date.")
    for symbol, ref in record["evidence"].items():
        proof, quality, bars = HistoricalStore(
            cli.local_path(ref["store"]), read_only=True
        ).inspect(ref["dataset_id"])
        if (
            proof["state"] != "QUARANTINED"
            or digest(proof) != ref["checksum"]
            or DownloadRequest.model_validate(proof["request"])
            != cli.partition_request(task, symbol)
            or proof["calendar"] != task["calendar"]
            or bars
            or quality.raw_rows != 0
            or {f.code for f in quality.findings if f.severity == "FAIL"} != {"NO_VALID_BARS"}
        ):
            raise AtlasError("Session exclusion requires intact empty quarantined IEX evidence.")
    return record
