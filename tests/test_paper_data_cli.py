"""Offline CLI diagnostics and enforced network/credential/execution isolation."""

import ast
import builtins
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from atlas.config import Settings
from atlas.main import main
from atlas.paper_data.mock import mock_config


@pytest.fixture
def isolated(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Paper diagnostics attempted network, credentials or execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(Settings, "from_environment", forbidden)
    original_get = os.environ.get

    def getenv(key, *args):
        if any(
            word in key.upper() for word in ("SECRET", "API_KEY", "TOKEN", "TELEGRAM", "PASSWORD")
        ):
            forbidden()
        return original_get(key, *args)

    monkeypatch.setattr(os.environ, "get", getenv)
    original_open = builtins.open
    original_read_text = Path.read_text

    def guarded_open(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).name.startswith(".env"):
            forbidden()
        return original_open(path, *args, **kwargs)

    def guarded_read_text(path, *args, **kwargs):
        if path.name.startswith(".env"):
            forbidden()
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)


@pytest.mark.parametrize("venue", ["XNYS", "XNAS"])
def test_all_diagnostics_are_offline_and_restart_replay_does_not_duplicate(
    isolated, tmp_path, capsys, venue
):
    database = str(tmp_path / "paper.sqlite3")
    for command in ("validate-feed", "entitlement"):
        assert main(["paper-data", command, "--venue", venue, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["scope"] == "MOCK_ONLY" and result["valid"]
    replay = [
        "paper-data",
        "replay",
        "--db",
        database,
        "--venue",
        venue,
        "--scenario",
        "duplicate",
        "--json",
    ]
    assert main(replay) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["counts"] == {"ELIGIBLE": 2, "REJECTED": 1}
    assert first["status"] == "STOPPED" and not first["execution_enabled"]
    assert main(replay) == 0
    restarted = json.loads(capsys.readouterr().out)
    assert restarted["counts"] == first["counts"]
    assert restarted["fence"] == 2
    for command in ("state", "ownership", "freshness"):
        assert main(["paper-data", command, "--db", database, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["stream_id"] == "mock-paper"
        if command == "freshness":
            assert "DUPLICATE_OBSERVATION" in result["recent_failures"][0]["reasons"]
        if command == "ownership":
            assert result["owner"] is None and not result["owner_active"]


@pytest.mark.parametrize(
    ("scenario", "options", "mode", "reason"),
    [
        ("delayed", ["--allow-delayed"], "PAPER_DELAYED", None),
        ("delayed", [], "PAPER_UNAVAILABLE", "DELAYED_FEED_DISABLED"),
        ("ordered", ["--unverified"], "PAPER_UNAVAILABLE", "ENTITLEMENT_NOT_VERIFIED"),
        ("stale", [], "PAPER_UNAVAILABLE", "STALE_EVENT"),
        ("missing-availability", [], "PAPER_UNAVAILABLE", "PROVIDER_AVAILABILITY_MISSING"),
        ("unknown-delay", [], "PAPER_UNAVAILABLE", "FEED_DELAY_UNKNOWN_OR_MISMATCH"),
        ("halt", [], "PAPER_REPLAY", "MARKET_HALTED"),
        ("connection-failure", [], "PAPER_REPLAY", "PROVIDER_DISCONNECTED"),
    ],
)
def test_cli_fault_diagnostics(isolated, tmp_path, capsys, scenario, options, mode, reason):
    assert (
        main(
            [
                "paper-data",
                "replay",
                "--db",
                str(tmp_path / "paper.sqlite3"),
                "--scenario",
                scenario,
                *options,
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["results"][0]["mode"] == mode
    if reason:
        assert any(reason in item["reasons"] for item in result["results"])


def test_cli_unverified_entitlement_returns_nonzero(isolated, capsys):
    assert main(["paper-data", "entitlement", "--unverified", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["valid"]
    assert result["entitlement"]["reasons"] == ["ENTITLEMENT_NOT_VERIFIED"]


def test_configuration_file_and_errors_are_safe(isolated, tmp_path, capsys):
    path = tmp_path / "mock.json"
    path.write_text(json.dumps({"feed": mock_config().model_dump(mode="json")}), encoding="utf-8")
    assert main(["paper-data", "validate-feed", "--config", str(path)]) == 0
    capsys.readouterr()
    path.write_text('{"feed":{"api_key":"PRIVATE-MARKER"}}', encoding="utf-8")
    assert main(["paper-data", "validate-feed", "--config", str(path)]) == 1
    output = capsys.readouterr()
    assert "PRIVATE-MARKER" not in output.err
    assert json.loads(output.err)["error"] == "PAPER_DIAGNOSTIC_FAILED"
    # The safety boundary rejects credential paths before any content read.
    assert main(["paper-data", "validate-feed", "--config", str(tmp_path / ".env")]) == 1


def test_missing_database_diagnostics_do_not_create_it(isolated, tmp_path, capsys):
    path = tmp_path / "missing.sqlite3"
    assert main(["paper-data", "state", "--db", str(path)]) == 1
    assert not path.exists()
    assert json.loads(capsys.readouterr().err)["scope"] == "MOCK_ONLY"


def test_closed_session_retains_pending_cursor(isolated, tmp_path, capsys):
    assert (
        main(
            [
                "paper-data",
                "replay",
                "--db",
                str(tmp_path / "closed.sqlite3"),
                "--scenario",
                "session-transition",
                "--at",
                "2026-10-28T19:59:59Z",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["cursor"] == 1
    assert result["counts"] == {"ELIGIBLE": 1}
    assert result["status"] == "STOPPED"


def test_fresh_process_import_boundary_and_offline_cli(tmp_path):
    script = """
import builtins, socket, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('atlas.broker','atlas.notifications','atlas.market_data.massive',
                        'atlas.market_data.providers','atlas.market_data.massive_credentials')):
        raise AssertionError('Forbidden paper dependency: '+name)
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
def no_network(*args, **kwargs):
    raise AssertionError('Network forbidden')
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network
from atlas.main import main
assert main(['paper-data','replay','--db',sys.argv[1]]) == 0
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "subprocess.sqlite3")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["counts"] == {"ELIGIBLE": 2}


def test_paper_package_has_no_network_execution_or_credential_imports():
    package = Path(__file__).resolve().parents[1] / "src/atlas/paper_data"
    forbidden = (
        "httpx",
        "requests",
        "socket",
        "urllib",
        "atlas.broker",
        "atlas.notifications",
        "atlas.portfolio",
        "atlas.risk",
        "atlas.strategy",
        "atlas.backtest",
        "atlas.market_data.massive",
        "atlas.market_data.providers",
    )
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.startswith(forbidden) for name in names), path
