"""Offline ingestion benchmark over original artificial fixture bars only."""

import argparse
import ctypes
import json
import os
import time
import tracemalloc
from datetime import date, timedelta
from pathlib import Path

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import DownloadRequest, RawPage
from atlas.market_data.providers import FixtureProvider, fixture_instruments
from atlas.market_data.quality import assess
from atlas.market_data.store import HistoricalStore


def process_peak_bytes():
    if os.name != "nt":
        return None

    class Memory(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "working",
                "pool_peak",
                "pool",
                "nonpool_peak",
                "nonpool",
                "page",
                "page_peak",
            )
        ]

    memory = Memory()
    memory.cb = ctypes.sizeof(memory)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = ctypes.c_void_p
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.c_void_p(process()), ctypes.byref(memory), memory.cb
    ):
        return None
    return memory.peak


def benchmark(folder, *, sessions=2, symbols=5, target=972450):
    if not 1 <= sessions <= 5 or not 1 <= symbols <= 5:
        raise AtlasError("Use one to five fixture sessions and instruments.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "synthetic-ingestion.sqlite3"
    if path.exists():
        raise AtlasError("Benchmark requires a new isolated database directory.")
    as_of = HistoricalCalendar(Venue.NASDAQ, date(2026, 10, 26), date(2026, 10, 26)).sessions()[
        0
    ].close + timedelta(days=1)

    def clock():
        return as_of

    store = HistoricalStore(path, clock=clock)
    template = next(i for i in fixture_instruments() if i.venue == Venue.NASDAQ)
    timers = {
        "json_and_contract_parse_seconds": 0.0,
        "quality_seconds": 0.0,
        "persistence_with_revalidation_seconds": 0.0,
        "reinspection_seconds": 0.0,
    }
    rows = 0
    tracemalloc.start()
    started = time.perf_counter()
    for offset in range(sessions):
        day = date(2026, 10, 19) + timedelta(days=offset)
        calendar = HistoricalCalendar(Venue.NASDAQ, day, day)
        session = calendar.sessions()[0]
        provider = FixtureProvider(calendar, page_size=5000, clock=clock)
        for index in range(symbols):
            inst = template.model_copy(
                update={"instrument_id": template.instrument_id + f"-BENCH-{index}"}
            )
            request = DownloadRequest(
                provider="fixture-v1", instrument=inst, start=session.open, end=session.close
            )
            source = provider.fetch_page(request, None)
            encoded = source.model_dump_json()
            start = time.perf_counter()
            page = RawPage.model_validate(json.loads(encoded))
            timers["json_and_contract_parse_seconds"] += time.perf_counter() - start
            start = time.perf_counter()
            bars, quality = assess(request, [page], calendar)
            timers["quality_seconds"] += time.perf_counter() - start
            start = time.perf_counter()
            context = {
                "provider": provider.descriptor.model_dump(mode="json"),
                "actions": [],
                "action_coverage": "UNKNOWN",
            }
            job, owner, cached = store.begin_job(
                request, calendar.provenance(), import_context=context
            )
            if cached:
                raise AtlasError("Unexpected benchmark partition reuse.")
            store.checkpoint(job, owner, page)
            identity = store.publish(
                job,
                owner,
                request,
                provider.descriptor,
                calendar.provenance(),
                bars,
                quality,
                (page,),
                (),
                "UNKNOWN",
            )
            timers["persistence_with_revalidation_seconds"] += time.perf_counter() - start
            start = time.perf_counter()
            accepted, _, verified = store.inspect(identity)
            timers["reinspection_seconds"] += time.perf_counter() - start
            if accepted["state"] != "ACCEPTED" or len(verified) != 390:
                raise AtlasError("Synthetic benchmark dataset validation failed.")
            rows += len(verified)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    size = sum(p.stat().st_size for p in folder.glob("synthetic-ingestion.sqlite3*"))
    return {
        "scope": "ARTIFICIAL_OFFLINE_FIXTURES_NOT_MARKET_DATA",
        "rows": rows,
        "sessions": sessions,
        "symbols": symbols,
        "timings": timers,
        "elapsed_seconds": elapsed,
        "bars_per_second": rows / elapsed,
        "python_peak_traced_bytes": peak,
        "process_peak_working_set_bytes": process_peak_bytes(),
        "database_bytes": size,
        "bytes_per_bar": size / rows,
        "target_bars": target,
        "projected_ingestion_seconds": elapsed * target / rows,
        "projected_database_bytes": int(size * target / rows),
        "bottleneck": max(timers, key=timers.get),
        "limitations": [
            "Tracemalloc enabled: timings include instrumentation overhead",
            "Small warm-cache local fixture sample; not a full-scale runtime guarantee",
            "Fixture source evidence differs from Alpaca multi-symbol page evidence",
            "Process peak includes interpreter/imports; traced peak covers Python allocations",
            "Publication and reinspection deliberately repeat validation; no optimisation applied",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline synthetic ingestion benchmark")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--symbols", type=int, default=5)
    args = parser.parse_args(argv)
    result = benchmark(args.output_dir, sessions=args.sessions, symbols=args.symbols)
    (args.output_dir / "benchmark-report.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
