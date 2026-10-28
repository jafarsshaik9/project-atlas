"""Authenticated paper views open only a read-only account connection."""

import pytest
from fastapi.testclient import TestClient

from atlas.dashboard_security import DashboardAuth
from atlas.history import HistoryStore
from atlas.paper_execution.cli import run_session
from atlas.web.app import create_app


@pytest.mark.parametrize("account", ["completed", "no-opportunities", "missing", "unconfigured"])
def test_paper_view_auth_read_only_and_empty_states(tmp_path, account):
    path = tmp_path / "paper.sqlite3"
    if account in ("completed", "no-opportunities"):
        run_session(path, account)
    before = path.read_bytes() if path.exists() else None
    history = HistoryStore(tmp_path / "history.sqlite3")
    password = "fictional-test-password-only"
    DashboardAuth(history).set_password(password)
    app = create_app(history, paper_db=None if account == "unconfigured" else path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/paper").status_code == 401
        assert client.get("/paper").status_code == 401
        assert (
            client.post(
                "/auth/login",
                json={"password": password},
                headers={"Origin": "http://127.0.0.1:8000"},
            ).status_code
            == 200
        )
        assert client.get("/paper").status_code == 200
        response = client.get("/api/paper")
        if account == "missing":
            assert response.status_code == 503
        elif account == "unconfigured":
            assert response.json()["configured"] is False
        else:
            result = response.json()
            assert result["reconciled"] and result["configured"]
            assert result["cash_gbp"] == ("1002.424" if account == "completed" else "1000.00")
        assert (
            client.post(
                "/api/paper", json={}, headers={"Origin": "http://127.0.0.1:8000"}
            ).status_code
            == 405
        )
    assert (path.read_bytes() if path.exists() else None) == before
