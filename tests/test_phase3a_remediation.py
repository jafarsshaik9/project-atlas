"""Independent acceptance reproductions; all files are disposable pytest fixtures."""

import gc
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, localcontext
from threading import Barrier

import pytest

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.main import main
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.contracts import (
    CorporateAction,
    DownloadRequest,
    QualityReport,
    canonical,
    digest,
)
from atlas.market_data.providers import FixtureProvider, ProviderError, fixture_instruments
from atlas.market_data.quality import assess
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore, JobOwnershipLost

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


@pytest.fixture
def batch(tmp_path):
    calendar = HistoricalCalendar(Venue.LSE, date(2024, 10, 28), date(2024, 10, 28))
    start = calendar.sessions()[0].open
    request = DownloadRequest(
        provider="fixture-v1",
        instrument=fixture_instruments()[0],
        start=start,
        end=start + timedelta(minutes=10),
    )
    provider = FixtureProvider(calendar, page_size=4, clock=lambda: NOW)
    return (
        request,
        calendar,
        provider,
        HistoricalStore(tmp_path / "data.sqlite3", clock=lambda: NOW),
    )


def change(model, **updates):
    return type(model).model_validate({**model.model_dump(), **updates})


def fetch(provider, request):
    pages, cursor = [], None
    while True:
        page = provider.fetch_page(request, cursor)
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            return tuple(pages)


def context(provider):
    return {
        "provider": provider.descriptor.model_dump(mode="json"),
        "actions": [],
        "action_coverage": "UNKNOWN",
    }


def prepared(batch):
    request, cal, provider, store = batch
    job, owner, _ = store.begin_job(request, cal.provenance(), import_context=context(provider))
    pages = fetch(provider, request)
    for page in pages:
        store.checkpoint(job, owner, page)
    bars, report = assess(request, pages, cal)
    return job, owner, pages, bars, report


@pytest.mark.parametrize(
    "defect", ["cross_unit", "report", "missing_checkpoint", "incomplete_chain"]
)
def test_f01_rejects_inconsistent_publication(defect, batch):
    request, cal, provider, store = batch
    job, owner, _ = store.begin_job(
        request,
        cal.provenance(),
        import_context={
            "provider": provider.descriptor.model_dump(mode="json"),
            "actions": [],
            "action_coverage": "UNKNOWN",
        },
    )
    source_request = (
        change(request, instrument=fixture_instruments()[1]) if defect == "cross_unit" else request
    )
    pages = fetch(provider, source_request)
    bars, report = assess(source_request, pages, cal)
    durable = (
        ()
        if defect == "missing_checkpoint"
        else pages[:1]
        if defect == "incomplete_chain"
        else pages
    )
    for page in durable:
        store.checkpoint(job, owner, page)
    if defect == "report":
        report = change(report, raw_rows=report.raw_rows + 1)
    with pytest.raises(AtlasError):
        store.publish(
            job,
            owner,
            request,
            provider.descriptor,
            cal.provenance(),
            bars,
            report,
            pages,
            (),
            "UNKNOWN",
        )
    assert store.coverage()[1] == 0
    assert store.job(job)["status"] != "COMPLETED"


def test_f02_overlap_rejected(batch):
    request = batch[0]
    first = request.instrument.mappings[0]
    second = change(first, symbol="NEW", effective_from=request.start + timedelta(minutes=5))
    with pytest.raises(ValueError):
        change(request.instrument, mappings=(first, second))


@pytest.mark.parametrize("operation", ["constructor", "backup"])
def test_f03_windows_handles_close_without_collection(operation, batch, tmp_path):
    _, _, _, store = batch
    enabled = gc.isenabled()
    gc.disable()
    try:
        if operation == "backup":
            target = tmp_path / "backup.sqlite3"
            store.backup(target)
        else:
            HistoricalStore(store.path)
            target = store.path
        renamed = target.with_suffix(".renamed")
        target.rename(renamed)
        renamed.unlink()
    finally:
        gc.collect()  # Cleanup after observing a failure, never used to satisfy the assertion.
        if enabled:
            gc.enable()


def test_f04_refresh_resumes_saved_job(batch):
    request, cal, provider, store = batch
    download(provider, request, cal, store)
    real = provider.fetch_page
    cursors = []

    def interrupted(req, cursor=None):
        cursors.append(cursor)
        if len(cursors) == 2:
            raise ProviderError("OFFLINE_INTERRUPTION")
        return real(req, cursor)

    provider.fetch_page = interrupted
    with pytest.raises(ProviderError):
        download(provider, request, cal, store, refresh=True)
    with store.connect() as db:
        job = dict(db.execute("SELECT * FROM md_jobs WHERE status='INTERRUPTED'").fetchone())
    cursors.clear()

    def resumed(req, cursor=None):
        cursors.append(cursor)
        return real(req, cursor)

    provider.fetch_page = resumed
    result = download(provider, request, cal, store, resume_job_id=job["job_id"])
    assert result[1] == job["job_id"] and cursors[0] == "4"
    assert len(store.inspect(result[0])[2]) == 10


def test_f05_quality_independent_of_decimal_context(batch):
    request, cal, provider, _ = batch
    request = change(request, end=request.start + timedelta(minutes=2))
    page = provider.fetch_page(request)
    body = json.loads(json.dumps(page.body))
    for index, row in enumerate(body["values"]):
        price = "100" if index == 0 else "130.00001"
        row.update(open=price, high=price, low=price, close=price)
    page = change(page, body=body)
    reports = []
    for precision, rounding in ((6, ROUND_DOWN), (28, ROUND_UP)):
        with localcontext() as context:
            context.prec, context.rounding = precision, rounding
            reports.append(assess(request, (page,), cal)[1])
    assert reports[0] == reports[1]


def test_f06_json_stdout_is_one_document(batch, capsys):
    request, cal, provider, store = batch
    identifier = download(provider, request, cal, store)[0]
    assert main(["market-data", "validate", identifier, "--json", "--db", str(store.path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "WARNING"


def test_w01_metadata_coverage_does_not_certify_corruption(batch):
    request, cal, provider, store = batch
    identifier = download(provider, request, cal, store)[0]
    with store.connect(write=True) as db:
        db.execute("DELETE FROM md_bars WHERE dataset_id=? AND ordinal=9", (identifier,))
    manifest = store.coverage()[0][0]
    assert manifest["integrity"] == "NOT_VERIFIED"
    with pytest.raises(AtlasError):
        store.inspect(identifier)


def test_w02_read_does_not_create_missing_database(tmp_path, capsys):
    path = tmp_path / "absent" / "data.sqlite3"
    assert main(["market-data", "coverage", "--db", str(path)]) == 1
    assert not path.parent.exists()
    assert "database" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "field,value",
    [
        ("instrument_id", "OTHER-LISTING"),
        ("venue", "XNAS"),
        ("provider", "other-provider"),
        ("unit", "GBP"),
        ("adjustment", "SPLIT_ADJUSTED"),
        ("volume", "0"),
    ],
)
def test_f01_bar_binding_covers_every_identity_field(field, value, batch):
    request, cal, provider, store = batch
    job, owner, pages, bars, report = prepared(batch)
    altered = (change(bars[0], **{field: value}), *bars[1:])
    with pytest.raises(AtlasError):
        store.publish(
            job,
            owner,
            request,
            provider.descriptor,
            cal.provenance(),
            altered,
            report,
            pages,
            (),
            "UNKNOWN",
        )
    assert store.coverage()[1] == 0


@pytest.mark.parametrize(
    "mutation",
    ["request", "descriptor", "calendar", "actions", "coverage", "pages", "bars", "ingested_at"],
)
def test_f01_publication_bound_to_job_and_exact_source(mutation, batch):
    request, cal, provider, store = batch
    job, owner, pages, bars, report = prepared(batch)
    descriptor, calendar, actions, coverage = provider.descriptor, cal.provenance(), (), "UNKNOWN"
    if mutation == "request":
        request = change(request, end=request.end + timedelta(minutes=1))
    elif mutation == "descriptor":
        descriptor = change(descriptor, origin="EXTERNAL_HISTORICAL_MARKET_DATA")
    elif mutation == "calendar":
        calendar["version"] = "DIFFERENT_CALENDAR_POLICY"
    elif mutation == "actions":
        actions = (
            CorporateAction(
                action_id="example",
                instrument_id=request.instrument.instrument_id,
                kind="split",
                effective_date=date(2024, 10, 28),
                ratio="2",
                source="fictional",
            ),
        )
    elif mutation == "coverage":
        coverage = "PARTIAL"
    elif mutation == "pages":
        pages = (change(pages[0], fetched_at=NOW + timedelta(seconds=1)), *pages[1:])
    elif mutation == "bars":
        bars = bars[:-1]
    else:
        bars = (change(bars[0], ingested_at=NOW + timedelta(seconds=1)), *bars[1:])
    with pytest.raises(AtlasError):
        store.publish(
            job, owner, request, descriptor, calendar, bars, report, pages, actions, coverage
        )
    assert store.coverage()[1] == 0


def test_f01_rejects_unbound_publication_context(batch):
    request, cal, provider, store = batch
    job, owner, _ = store.begin_job(request, cal.provenance())
    pages = fetch(provider, request)
    for page in pages:
        store.checkpoint(job, owner, page)
    bars, report = assess(request, pages, cal)
    with pytest.raises(AtlasError, match="stored job context"):
        store.publish(
            job,
            owner,
            request,
            provider.descriptor,
            cal.provenance(),
            bars,
            report,
            pages,
            (),
            "UNKNOWN",
        )


@pytest.mark.parametrize("mutation", ["bar_identity", "report"])
def test_f01_read_rejects_semantic_corruption_even_with_valid_hashes(mutation, batch):
    request, cal, provider, store = batch
    old = download(provider, request, cal, store)[0]
    manifest, report, bars = store.inspect(old)
    if mutation == "bar_identity":
        bars = (change(bars[0], instrument_id="WRONG-LISTING", unit="GBP"), *bars[1:])
    else:
        report = change(report, raw_rows=report.raw_rows + 1)
    keys = (
        "schema_version",
        "normalization_version",
        "kind",
        "execution",
        "origin",
        "provider",
        "provider_descriptor",
        "request",
        "calendar",
        "raw_body_checksums",
        "actions",
        "action_coverage",
        "quality_policy",
    )
    content = {key: manifest[key] for key in keys}
    content.update(bars=[b.content() for b in bars], quality=report.model_dump(mode="json"))
    identifier = digest(content)
    bars = tuple(change(b, dataset_version=identifier) for b in bars)
    manifest.update(
        dataset_id=identifier,
        storage_checksum=digest([b.model_dump(mode="json") for b in bars]),
        quality_checksum=digest(report.model_dump(mode="json")),
    )
    with store.connect(write=True) as db:
        db.execute(
            "INSERT INTO md_datasets VALUES(?,?,?,?)",
            (identifier, canonical(manifest), report.model_dump_json(), digest(manifest)),
        )
        db.executemany(
            "INSERT INTO md_bars VALUES(?,?,?)",
            ((identifier, i, b.model_dump_json()) for i, b in enumerate(bars)),
        )
        db.execute("UPDATE md_jobs SET dataset_id=? WHERE dataset_id=?", (identifier, old))
    with pytest.raises(AtlasError):
        store.inspect(identifier)
    with pytest.raises(AtlasError):
        store.visible_bars(identifier, NOW)


@pytest.mark.parametrize("mutation", ["checksum", "bytes"])
def test_f01_source_changes_during_validation_cannot_commit(mutation, batch, monkeypatch):
    request, cal, provider, store = batch
    job, owner, pages, bars, report = prepared(batch)
    verify = store._verify_batch

    def changed_source(*args, **kwargs):
        result = verify(*args, **kwargs)
        with store.connect(write=True) as db:
            if mutation == "checksum":
                db.execute(
                    "UPDATE md_pages SET checksum=? WHERE job_id=? AND ordinal=0", ("0" * 64, job)
                )
            else:
                row = db.execute(
                    "SELECT content FROM md_pages WHERE job_id=? AND ordinal=0", (job,)
                ).fetchone()
                db.execute(
                    "UPDATE md_pages SET content=? WHERE job_id=? AND ordinal=0",
                    (row[0] + b"tampered", job),
                )
        return result

    monkeypatch.setattr(store, "_verify_batch", changed_source)
    with pytest.raises(AtlasError, match="changed during validation"):
        store.publish(
            job,
            owner,
            request,
            provider.descriptor,
            cal.provenance(),
            bars,
            report,
            pages,
            (),
            "UNKNOWN",
        )
    assert store.coverage()[1] == 0


def test_f01_ownership_transfer_during_validation_cannot_commit(batch, monkeypatch):
    request, cal, provider, store = batch
    job, owner, pages, bars, report = prepared(batch)
    verify = store._verify_batch
    replacement = []

    def lose_ownership(*args, **kwargs):
        result = verify(*args, **kwargs)
        store.clock = lambda: NOW + timedelta(seconds=91)
        replacement.append(
            store.begin_job(request, cal.provenance(), import_context=context(provider))[1]
        )
        return result

    monkeypatch.setattr(store, "_verify_batch", lose_ownership)
    with pytest.raises(JobOwnershipLost):
        store.publish(
            job,
            owner,
            request,
            provider.descriptor,
            cal.provenance(),
            bars,
            report,
            pages,
            (),
            "UNKNOWN",
        )
    assert store.coverage()[1] == 0 and store.job(job)["owner"] == replacement[0]


def test_f01_concurrent_terminal_publications_are_atomic(batch):
    request, cal, provider, store = batch
    job, owner, pages, bars, report = prepared(batch)
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=5)
        try:
            store.publish(
                job,
                owner,
                request,
                provider.descriptor,
                cal.provenance(),
                bars,
                report,
                pages,
                (),
                "UNKNOWN",
            )
            return "COMPLETED"
        except JobOwnershipLost:
            return "OWNERSHIP_LOST"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish) for _ in range(2)]
        outcomes = sorted(future.result(timeout=10) for future in futures)
    assert outcomes == ["COMPLETED", "OWNERSHIP_LOST"]
    assert store.coverage()[1] == 1 and store.job(job)["status"] == "COMPLETED"


def test_f01_corrupt_cached_version_cannot_complete_a_refresh(batch):
    request, cal, provider, store = batch
    identifier = download(provider, request, cal, store)[0]
    with store.connect(write=True) as db:
        db.execute("DELETE FROM md_bars WHERE dataset_id=? AND ordinal=0", (identifier,))
    with pytest.raises(AtlasError):
        download(provider, request, cal, store, refresh=True)
    with store.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM md_jobs WHERE status='COMPLETED'").fetchone()[0] == 1
        )
        assert (
            db.execute("SELECT count(*) FROM md_jobs WHERE status='INTERRUPTED'").fetchone()[0] == 1
        )


def test_f01_legacy_v1_revalidated_without_rewriting(batch):
    request, cal, provider, store = batch
    identifier = download(provider, request, cal, store)[0]
    manifest, report, bars = store.inspect(identifier)
    keys = (
        "schema_version",
        "normalization_version",
        "kind",
        "execution",
        "origin",
        "provider",
        "provider_descriptor",
        "request",
        "calendar",
        "raw_body_checksums",
        "actions",
        "action_coverage",
        "quality_policy",
    )
    manifest["normalization_version"] = "historical-normalization-v1"
    manifest["quality_policy"].pop("arithmetic")
    manifest.pop("source_job_id")
    manifest.pop("source_page_checksums")
    content = {key: manifest[key] for key in keys}
    content.update(bars=[bar.content() for bar in bars], quality=report.model_dump(mode="json"))
    legacy = digest(content)
    legacy_bars = tuple(change(bar, dataset_version=legacy) for bar in bars)
    manifest.update(
        dataset_id=legacy, storage_checksum=digest([b.model_dump(mode="json") for b in legacy_bars])
    )
    legacy_key = digest(
        {
            "request": request.model_dump(mode="json"),
            "calendar": cal.provenance(),
            "normalizer": "historical-normalization-v1",
            "import_context": context(provider),
        }
    )
    with store.connect(write=True) as db:
        db.execute(
            "INSERT INTO md_datasets VALUES(?,?,?,?)",
            (legacy, canonical(manifest), report.model_dump_json(), digest(manifest)),
        )
        db.executemany(
            "INSERT INTO md_bars VALUES(?,?,?)",
            ((legacy, i, b.model_dump_json()) for i, b in enumerate(legacy_bars)),
        )
        db.execute(
            "UPDATE md_jobs SET dataset_id=?,request_key=? WHERE dataset_id=?",
            (legacy, legacy_key, identifier),
        )
    reader = HistoricalStore(store.path, read_only=True)
    assert reader.validate(legacy) == report
    with reader.connect() as db:
        assert db.execute(
            "SELECT manifest FROM md_datasets WHERE dataset_id=?", (legacy,)
        ).fetchone()[0] == canonical(manifest)


@pytest.mark.parametrize("bounds", [(None, 5, None), (8, 2, 7), (7, 1, 6)])
def test_f02_nested_and_open_ended_overlap(bounds, batch):
    request = batch[0]
    end_first, start_second, end_second = bounds
    first = request.instrument.mappings[0]
    first = change(
        first,
        effective_from=request.start,
        effective_to=None if end_first is None else request.start + timedelta(minutes=end_first),
    )
    second = change(
        first,
        symbol="NEW",
        effective_from=request.start + timedelta(minutes=start_second),
        effective_to=None if end_second is None else request.start + timedelta(minutes=end_second),
    )
    with pytest.raises(ValueError, match="Overlapping"):
        change(request.instrument, mappings=(first, second))


def test_f02_adjacent_mappings_allowed_transition_request_refused(batch):
    request = batch[0]
    boundary = request.start + timedelta(minutes=5)
    first = change(request.instrument.mappings[0], effective_to=boundary)
    second = change(first, symbol="NEW", effective_from=boundary, effective_to=None)
    instrument = change(request.instrument, mappings=(second, first))
    assert instrument.mapping(request.provider, request.start, boundary).symbol == first.symbol
    assert instrument.mapping(request.provider, boundary, request.end).symbol == "NEW"
    with pytest.raises(ValueError, match="unique effective"):
        change(request, instrument=instrument)


def test_f03_exception_paths_release_connections(batch, tmp_path, monkeypatch):
    _, _, _, store = batch
    with store.connect(write=True) as db:
        db.execute("UPDATE md_meta SET version=999")
    enabled = gc.isenabled()
    gc.disable()
    try:
        with pytest.raises(AtlasError):
            HistoricalStore(store.path)
        store.path.rename(tmp_path / "invalid-schema.sqlite3")
        real_connect, connections = sqlite3.connect, []

        class FailingBackup(sqlite3.Connection):
            def backup(self, *args, **kwargs):
                raise sqlite3.OperationalError("injected offline failure")

        def tracked(*args, **kwargs):
            connection = real_connect(*args, **kwargs, factory=FailingBackup)
            connections.append(connection)
            return connection

        # Restore a valid source independently, then inject a backup failure.
        source = HistoricalStore(tmp_path / "valid.sqlite3")
        monkeypatch.setattr(sqlite3, "connect", tracked)
        with pytest.raises(AtlasError):
            source.backup(tmp_path / "failed-backup.sqlite3")
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
    finally:
        gc.collect()
        if enabled:
            gc.enable()


@pytest.mark.parametrize(
    "mismatch", ["instrument", "interval", "adjustment", "provider", "calendar"]
)
def test_f04_resume_context_must_match_exactly(mismatch, batch):
    request, cal, provider, store = batch
    job, owner, _ = store.begin_job(
        request, cal.provenance(), refresh=True, import_context=context(provider)
    )
    store.checkpoint(job, owner, provider.fetch_page(request))
    store.interrupted(job, owner, "OFFLINE_INTERRUPTION")
    other, calendar, policy = request, cal.provenance(), context(provider)
    if mismatch == "instrument":
        other = change(request, instrument=fixture_instruments()[1])
    elif mismatch == "interval":
        other = change(request, end=request.end + timedelta(minutes=1))
    elif mismatch == "adjustment":
        other = change(request, adjustment="SPLIT_ADJUSTED")
    elif mismatch == "provider":
        policy["provider"]["provider_id"] = "different-provider"
    else:
        calendar["version"] = "different-calendar-version"
    with pytest.raises(AtlasError, match="mismatch"):
        store.begin_job(other, calendar, import_context=policy, resume_job_id=job)
    assert store.job(job)["cursor"] == "4" and store.job(job)["status"] == "INTERRUPTED"


@pytest.mark.parametrize("identity", ["0" * 64, "../not-a-job"])
def test_f04_unknown_or_invalid_resume_id_creates_no_job(identity, batch):
    request, cal, provider, store = batch
    with pytest.raises(AtlasError):
        download(provider, request, cal, store, resume_job_id=identity)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM md_jobs").fetchone()[0] == 0


def test_f04_restart_resume_deduplicates_completed_job(batch):
    request, cal, provider, store = batch
    job, owner, _ = store.begin_job(
        request, cal.provenance(), refresh=True, import_context=context(provider)
    )
    store.checkpoint(job, owner, provider.fetch_page(request))
    store.interrupted(job, owner, "OFFLINE_INTERRUPTION")
    restarted = HistoricalStore(store.path, clock=lambda: NOW)
    result = download(provider, request, cal, restarted, resume_job_id=job)
    assert result[1] == job and not result[2]
    assert download(provider, request, cal, restarted, resume_job_id=job) == (result[0], job, True)
    assert len(restarted.pages(job)) == 3 and restarted.coverage()[1] == 1


def test_f04_resume_fences_active_and_obsolete_owners(batch):
    request, cal, provider, store = batch
    job, old, _ = store.begin_job(
        request, cal.provenance(), refresh=True, import_context=context(provider)
    )
    first = provider.fetch_page(request)
    store.checkpoint(job, old, first)
    with pytest.raises(AtlasError, match="active worker"):
        download(provider, request, cal, store, resume_job_id=job)
    store.clock = lambda: NOW + timedelta(seconds=91)
    _, replacement, _ = store.begin_job(
        request, cal.provenance(), import_context=context(provider), resume_job_id=job
    )
    assert replacement != old and store.job(job)["cursor"] == "4"
    with pytest.raises(JobOwnershipLost):
        store.checkpoint(job, old, provider.fetch_page(request, "4"))


def test_f04_refresh_and_resume_are_mutually_exclusive(batch):
    request, cal, provider, store = batch
    with pytest.raises(AtlasError, match="not both"):
        download(provider, request, cal, store, refresh=True, resume_job_id="0" * 64)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM md_jobs").fetchone()[0] == 0


def test_f04_cli_reports_job_and_resumes_after_restart(batch, monkeypatch, capsys):
    _, _, _, store = batch
    real = FixtureProvider.fetch_page

    def interrupted(self, request, cursor=None):
        if cursor is not None:
            raise ProviderError("OFFLINE_INTERRUPTION")
        page = real(self, request, cursor)
        return change(page, body={**page.body, "values": page.body["values"][:4]}, next_cursor="4")

    with monkeypatch.context() as patch:
        patch.setattr(FixtureProvider, "fetch_page", interrupted)
        assert main(["market-data", "download", "--refresh", "--db", str(store.path)]) == 1
    output = capsys.readouterr()
    match = re.search(r"Resume job: ([a-f0-9]{64})", output.err)
    assert match is not None
    identity = match[1]
    assert HistoricalStore(store.path, read_only=True).job(identity)["cursor"] == "4"
    assert main(["market-data", "download", "--resume-job", identity, "--db", str(store.path)]) == 0
    capsys.readouterr()
    reopened = HistoricalStore(store.path, read_only=True)
    result = reopened.job(identity)
    assert result["status"] == "COMPLETED"
    assert len(reopened.inspect(result["dataset_id"])[2]) == 10


@pytest.mark.parametrize("precision,rounding", [(3, ROUND_DOWN), (6, ROUND_UP), (64, ROUND_DOWN)])
def test_f05_dataset_identity_and_source_do_not_depend_on_context(
    precision, rounding, batch, tmp_path
):
    request, cal, provider, store = batch
    expected = download(provider, request, cal, store)[0]
    with localcontext() as arithmetic:
        arithmetic.prec, arithmetic.rounding = precision, rounding
        other = HistoricalStore(tmp_path / "second.sqlite3", clock=lambda: NOW)
        actual = download(provider, request, cal, other)[0]
    assert actual == expected
    assert other.inspect(actual) == store.inspect(expected)


@pytest.mark.parametrize("price", ["1e65", "1e-65", "9" * 65])
def test_f05_unbounded_decimal_values_quarantined(price, batch):
    request, cal, provider, _ = batch
    page = provider.fetch_page(request)
    body = json.loads(json.dumps(page.body))
    body["values"][0].update(open=price, high=price, low=price, close=price)
    report = assess(request, (change(page, body=body),), cal)[1]
    assert report.status == "FAIL"
    assert "INVALID_NUMBER_OR_MISSING_FIELD" in {finding.code for finding in report.findings}


def test_f05_scientific_notation_serialization_is_context_independent(batch, tmp_path):
    request, cal, provider, store = batch
    real = provider.fetch_page

    def scientific(req, cursor=None):
        page = real(req, cursor)
        body = json.loads(json.dumps(page.body))
        for row in body["values"]:
            row.update(open="1E+3", high="1.01E+3", low="9.99E+2", close="1.001E+3")
        return change(page, body=body, wire_sha256=digest(body))

    provider.fetch_page = scientific
    identifiers = []
    for capitals in (0, 1):
        with localcontext() as arithmetic:
            arithmetic.capitals = capitals
            target = HistoricalStore(tmp_path / f"caps-{capitals}.sqlite3", clock=lambda: NOW)
            identifiers.append(download(provider, request, cal, target)[0])
    assert identifiers[0] == identifiers[1]


@pytest.mark.parametrize("command", ["inspect", "validate"])
@pytest.mark.parametrize("scenario", ["normal", "missing"])
def test_f06_json_warning_and_quarantine_are_parseable(command, scenario, batch, capsys):
    request, cal, provider, store = batch
    identifier = download(provider, change(request, fixture_scenario=scenario), cal, store)[0]
    code = main(["market-data", command, identifier, "--json", "--db", str(store.path)])
    output = json.loads(capsys.readouterr().out)
    assert code == (1 if command == "validate" and scenario == "missing" else 0)
    assert output.get("state", output.get("status")) in (
        "ACCEPTED",
        "QUARANTINED",
        "WARNING",
        "FAIL",
    )


def test_f06_json_pass_schema_and_corrupt_error(batch, capsys, monkeypatch):
    request, cal, provider, store = batch
    identifier = download(provider, request, cal, store)[0]
    with monkeypatch.context() as mock:
        mock.setattr(
            HistoricalStore,
            "validate",
            lambda *_: QualityReport(
                status="PASS", findings=(), expected_bars=10, valid_bars=10, raw_rows=10
            ),
        )
        assert main(["market-data", "validate", identifier, "--json", "--db", str(store.path)]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    with store.connect(write=True) as db:
        db.execute("DELETE FROM md_bars WHERE dataset_id=?", (identifier,))
    assert main(["market-data", "validate", identifier, "--json", "--db", str(store.path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


@pytest.mark.parametrize("command", ["inspect", "validate", "coverage"])
def test_w02_all_read_commands_refuse_missing_paths(command, tmp_path, capsys):
    path = tmp_path / "missing" / "data.sqlite3"
    args = ["market-data", command]
    if command != "coverage":
        args += ["0" * 64, "--json"]
    assert main([*args, "--db", str(path)]) == 1
    assert not path.parent.exists()
    if command != "coverage":
        assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def test_w02_read_only_store_refuses_writes_and_does_not_migrate(batch):
    _, _, _, store = batch
    before = store.path.read_bytes()
    reader = HistoricalStore(store.path, read_only=True)
    assert reader.coverage()[1] == 0
    with pytest.raises(AtlasError, match="read-only"):
        with reader.connect(write=True):
            pytest.fail("Read-only connection allowed a write transaction")
    assert store.path.read_bytes() == before
