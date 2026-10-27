"""Final audit reproductions; only synthetic inputs and disposable databases."""

import gc
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from atlas.config import AtlasError
from atlas.domain import Venue
from atlas.main import main
from atlas.market_data.calendars import HistoricalCalendar
from atlas.market_data.cli import parser
from atlas.market_data.contracts import DownloadRequest
from atlas.market_data.providers import FixtureProvider, fixture_instruments
from atlas.market_data.service import download
from atlas.market_data.store import HistoricalStore


@pytest.fixture
def source(tmp_path):
    store = HistoricalStore(
        tmp_path / "source.sqlite3", clock=lambda: datetime(2026, 10, 27, 12, tzinfo=UTC)
    )
    with store.connect(write=True) as db:
        db.execute("CREATE TABLE synthetic_marker(value TEXT)")
        db.execute("INSERT INTO synthetic_marker VALUES('original')")
    return store


@pytest.fixture
def dataset(source):
    calendar = HistoricalCalendar(Venue.LSE, date(2024, 10, 28), date(2024, 10, 28))
    start = calendar.sessions()[0].open
    request = DownloadRequest(
        provider="fixture-v1",
        instrument=fixture_instruments()[0],
        start=start,
        end=start + timedelta(minutes=2),
    )
    provider = FixtureProvider(calendar, clock=source.clock)
    return download(provider, request, calendar, source)[0]


def marker(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT value FROM synthetic_marker").fetchone()[0]


def invoke(argv):
    try:
        return main(["market-data", *argv])
    except SystemExit as error:
        return error.code


def test_ra01_only_one_simultaneous_backup_can_win(source, tmp_path, monkeypatch):
    other = HistoricalStore(tmp_path / "other.sqlite3")
    with other.connect(write=True) as db:
        db.execute("CREATE TABLE synthetic_marker(value TEXT)")
        db.execute("INSERT INTO synthetic_marker VALUES('other')")
    target = tmp_path / "shared.sqlite3"
    barrier = Barrier(2)
    real_connect = sqlite3.connect
    sources = {s.path.as_uri() + "?mode=ro" for s in (source, other)}

    def synchronized(*args, **kwargs):
        if str(args[0]) in sources:
            barrier.wait(timeout=15)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", synchronized)

    def backup(store, label):
        try:
            store.backup(target)
            return label
        except AtlasError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(backup, s, label) for s, label in ((source, "original"), (other, "other"))
        ]
        results = [f.result(timeout=30) for f in futures]
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert marker(target) == winners[0]


def test_ra01_competing_creator_is_never_overwritten(source, tmp_path, monkeypatch):
    target = tmp_path / "competing.sqlite3"
    real_exists = Path.exists
    created = []

    def racing_exists(path):
        observed = real_exists(path)
        if path == target and not created:
            with closing(sqlite3.connect(target)) as db:
                db.execute("CREATE TABLE synthetic_marker(value TEXT)")
                db.execute("INSERT INTO synthetic_marker VALUES('competitor')")
                db.commit()
            created.append(True)
        return observed

    monkeypatch.setattr(Path, "exists", racing_exists)
    with pytest.raises(AtlasError):
        source.backup(target)
    assert marker(target) == "competitor"


def test_ra01_failed_backup_does_not_leave_a_final_file(source, tmp_path, monkeypatch):
    target = tmp_path / "failed.sqlite3"
    real_connect = sqlite3.connect

    class PartialFailure(sqlite3.Connection):
        def backup(self, destination, *args, **kwargs):
            destination.execute("CREATE TABLE incomplete(value TEXT)")
            destination.commit()
            raise sqlite3.OperationalError("injected synthetic failure")

    def connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=PartialFailure)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(AtlasError):
        source.backup(target)
    assert not target.exists()
    assert not list(tmp_path.glob(".atlas-backup-*"))


@pytest.mark.parametrize(
    "argv",
    [
        ["validate", "0" * 64, "--json", "--db"],
        ["inspect", "0" * 64, "--json", "--unknown=fictional-sensitive-input"],
        ["validate", "--json"],
    ],
)
def test_ra02_json_syntax_errors_are_one_document(argv, capsys):
    assert invoke(argv) == 2
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["status"] == "ERROR" and document["error"] == "INVALID_ARGUMENTS"
    assert "fictional-sensitive-input" not in output.out + output.err


def test_ra01_existing_destination_and_source_are_preserved(source, tmp_path):
    target = tmp_path / "existing.sqlite3"
    source.backup(target)
    before = target.read_bytes()
    with pytest.raises(AtlasError):
        source.backup(target)
    assert target.read_bytes() == before
    with pytest.raises(AtlasError):
        source.backup(source.path)
    assert marker(source.path) == "original"


@pytest.mark.parametrize("failure", ["source", "destination"])
def test_ra01_connection_failures_close_handles_and_clean_staging(
    failure, source, tmp_path, monkeypatch
):
    real_connect = sqlite3.connect
    target, opened = tmp_path / "failed-connection.sqlite3", []
    source_uri = source.path.as_uri() + "?mode=ro"

    def connect(*args, **kwargs):
        is_source = str(args[0]) == source_uri
        if is_source == (failure == "source"):
            raise sqlite3.OperationalError("injected offline connection failure")
        value = real_connect(*args, **kwargs)
        opened.append(value)
        return value

    monkeypatch.setattr(sqlite3, "connect", connect)
    enabled = gc.isenabled()
    gc.disable()
    try:
        with pytest.raises(AtlasError):
            source.backup(target)
        assert not target.exists() and not list(tmp_path.glob(".atlas-backup-*"))
        for value in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                value.execute("SELECT 1")
        source.path.rename(tmp_path / "source-closed.sqlite3")
    finally:
        gc.collect()  # Cleanup only; not part of the success assertions.
        if enabled:
            gc.enable()


def test_ra01_failure_cleanup_preserves_a_competitors_destination(source, tmp_path, monkeypatch):
    target, real_connect = tmp_path / "competitor-on-failure.sqlite3", sqlite3.connect

    def fail(*args, **kwargs):
        if str(args[0]) == source.path.as_uri() + "?mode=ro":
            with closing(real_connect(target)) as competitor:
                competitor.execute("CREATE TABLE synthetic_marker(value TEXT)")
                competitor.execute("INSERT INTO synthetic_marker VALUES('competitor')")
                competitor.commit()
            raise sqlite3.OperationalError("injected connection failure after competing creation")
        return real_connect(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", fail)
        with pytest.raises(AtlasError):
            source.backup(target)
    assert marker(target) == "competitor"
    assert not list(tmp_path.glob(".atlas-backup-*"))


def test_ra01_competitor_at_atomic_publication_is_preserved(source, tmp_path, monkeypatch):
    target = tmp_path / "publication-race.sqlite3"
    method = "rename" if os.name == "nt" else "link"
    real_publish = getattr(os, method)

    def race(staged, destination):
        with closing(sqlite3.connect(destination)) as competitor:
            competitor.execute("CREATE TABLE synthetic_marker(value TEXT)")
            competitor.execute("INSERT INTO synthetic_marker VALUES('atomic-competitor')")
            competitor.commit()
        real_publish(staged, destination)

    monkeypatch.setattr(os, method, race)
    with pytest.raises(AtlasError):
        source.backup(target)
    assert marker(target) == "atomic-competitor"
    assert not list(tmp_path.glob(".atlas-backup-*"))


def test_ra01_publication_failure_removes_only_staging(source, tmp_path, monkeypatch):
    target = tmp_path / "publication-failed.sqlite3"

    def denied(*args, **kwargs):
        raise PermissionError("injected publication failure")

    monkeypatch.setattr(os, "rename" if os.name == "nt" else "link", denied)
    with pytest.raises(AtlasError):
        source.backup(target)
    assert not target.exists() and not list(tmp_path.glob(".atlas-backup-*"))


def test_ra01_integrity_failure_is_not_published(source, tmp_path, monkeypatch):
    target, real_connect = tmp_path / "invalid.sqlite3", sqlite3.connect

    class InvalidBackup(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA integrity_check":
                return super().execute("SELECT 'synthetic integrity failure'")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=InvalidBackup)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(AtlasError, match="integrity"):
        source.backup(target)
    assert not target.exists() and not list(tmp_path.glob(".atlas-backup-*"))


def test_ra01_restores_wal_snapshot_and_releases_windows_handles(source, dataset, tmp_path):
    enabled = gc.isenabled()
    gc.disable()
    try:
        with closing(sqlite3.connect(source.path)) as writer:
            assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
            writer.execute("INSERT INTO synthetic_marker VALUES('committed-in-wal')")
            writer.commit()
            target = tmp_path / "restored.sqlite3"
            source.backup(target)
            assert HistoricalStore(target, read_only=True).inspect(dataset) == source.inspect(
                dataset
            )
            with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as restored:
                assert restored.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
                assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert restored.execute("SELECT count(*) FROM synthetic_marker").fetchone()[0] == 2
            moved = target.with_suffix(".renamed")
            target.rename(moved)
            moved.unlink()
        assert not list(tmp_path.glob(".atlas-backup-*"))
    finally:
        gc.collect()
        if enabled:
            gc.enable()


@pytest.mark.parametrize("command", ["inspect", "validate"])
def test_ra02_valid_json_whole_stdout(command, source, dataset, capsys):
    assert invoke([command, dataset, "--json", "--db", str(source.path)]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value.get("state", value.get("status")) in ("ACCEPTED", "WARNING")


@pytest.mark.parametrize("command", ["inspect", "validate"])
def test_ra02_missing_database_is_one_json_error(command, tmp_path, capsys):
    target = tmp_path / "absent" / "db.sqlite3"
    assert invoke([command, "0" * 64, "--json", "--db", str(target)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"
    assert not target.parent.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["inspect", "0" * 64, "--unknown=fictional-sensitive-input", "--json"],
        ["validate", "--json", "0" * 64, "extra-fictional-sensitive-input"],
        ["inspect", "0" * 64, "--json=fictional-sensitive-input"],
        ["validate", "--db", "--json", "0" * 64],
        ["--unknown=fictional-sensitive-input", "validate", "0" * 64, "--json"],
    ],
)
def test_ra02_parser_errors_redact_all_raw_values(argv, capsys):
    assert invoke(argv) == 2
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "ERROR", "error": "INVALID_ARGUMENTS"}
    assert "fictional-sensitive-input" not in output.out + output.err
    assert "Traceback" not in output.out + output.err


@pytest.mark.parametrize(
    "argv",
    [
        ["validate", "0" * 64, "--db"],
        ["inspect", "0" * 64, "--unknown"],
        ["providers", "--json"],
        ["inspect", "0" * 64, "--db=--json", "--unknown"],
        ["inspect", "0" * 64, "--db=embedded--json", "--unknown"],
    ],
)
def test_ra02_non_json_and_argument_values_keep_argparse_behavior(argv, capsys):
    assert invoke(argv) == 2
    output = capsys.readouterr()
    assert output.out == "" and "usage:" in output.err


def test_ra02_after_terminator_json_is_a_positional_value(source, capsys):
    assert invoke(["inspect", "--db", str(source.path), "--", "--json"]) == 1
    output = capsys.readouterr()
    assert output.out.startswith("HISTORICAL MARKET DATA")
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.out)


def test_ra02_parser_reuse_does_not_leak_json_mode(capsys):
    arguments = parser()
    assert arguments.parse_args(["validate", "0" * 64, "--json"]).json
    with pytest.raises(SystemExit) as error:
        arguments.parse_args(["providers", "--unknown"])
    assert error.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_ra02_help_remains_human_readable(capsys):
    assert invoke(["validate", "--help"]) == 0
    output = capsys.readouterr()
    assert "usage:" in output.out and output.err == ""
