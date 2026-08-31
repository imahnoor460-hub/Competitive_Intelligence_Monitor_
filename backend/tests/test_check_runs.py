from datetime import datetime, timedelta


import app.services.check_service as check_service
from app.models.check_run import CheckRun, CheckRunStatus


def _setup_surface(client, monkeypatch):
    client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "supersecret1", "full_name": "Alice"},
    )
    login_res = client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "supersecret1"}
    )
    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()
    surface = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
        json={"surface_type": "pricing", "url": "https://rival.example.com", "check_frequency": "daily"},
        headers=headers,
    ).json()
    return headers, workspace["id"], competitor["id"], surface["id"]


def test_check_creates_a_successful_check_run(client, monkeypatch):
    headers, workspace_id, competitor_id, surface_id = _setup_surface(client, monkeypatch)
    check_url = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check"
    )

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=headers)

    runs = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check-runs",
        headers=headers,
    ).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["finished_at"] is not None


def test_check_marks_run_failed_on_fetch_error(client, monkeypatch):
    from app.services.snapshot_service import FetchError

    headers, workspace_id, competitor_id, surface_id = _setup_surface(client, monkeypatch)
    check_url = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check"
    )

    def _raise(url):
        raise FetchError("connection refused")

    monkeypatch.setattr(check_service, "capture_clean_snapshot", _raise)
    res = client.post(check_url, headers=headers)
    assert res.status_code == 502

    runs = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check-runs",
        headers=headers,
    ).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "connection refused" in runs[0]["error"]


def test_check_marks_run_failed_on_unexpected_exception(client, monkeypatch):
    """Regression test: run_surface_check previously only caught FetchError,
    so any other unhandled exception (e.g. the numpy/psycopg2 visual-diff
    bug found in the phase 0-6 audit) left the CheckRun stuck at 'running'
    forever instead of being marked 'failed'.
    """
    headers, workspace_id, competitor_id, surface_id = _setup_surface(client, monkeypatch)
    check_url = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check"
    )

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=headers)

    def _raise(old_text, new_text):
        raise RuntimeError("boom — not a FetchError")

    monkeypatch.setattr(check_service, "compute_diff", _raise)
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $15")

    # The app converts an unhandled exception into a consistent JSON 500
    # rather than letting it escape to the caller (see app/core/errors.py), so
    # this asserts what the deployed app actually returns. What matters here is
    # still the CheckRun bookkeeping below.
    failed_res = client.post(check_url, headers=headers)
    assert failed_res.status_code == 500
    assert failed_res.json() == {"detail": "Internal server error"}

    runs = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check-runs",
        headers=headers,
    ).json()
    latest = runs[0]
    assert latest["status"] == "failed"
    assert "boom" in latest["error"]

    # A later check must not be blocked by a phantom "already_running" row.
    monkeypatch.setattr(check_service, "compute_diff", lambda old, new: "real diff")
    followup = client.post(check_url, headers=headers)
    assert followup.json()["status"] == "change_detected"


def test_second_check_blocked_while_one_is_running(client, monkeypatch, db_session):
    headers, workspace_id, competitor_id, surface_id = _setup_surface(client, monkeypatch)
    check_url = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check"
    )

    # Simulate a check that's already in flight (e.g. a crashed/slow run).
    db_session.add(CheckRun(surface_id=surface_id, status=CheckRunStatus.running))
    db_session.commit()

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    res = client.post(check_url, headers=headers)
    assert res.json()["status"] == "already_running"


def test_stale_running_check_is_reclaimed(client, monkeypatch, db_session):
    headers, workspace_id, competitor_id, surface_id = _setup_surface(client, monkeypatch)
    check_url = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface_id}/check"
    )

    stale_started = datetime.utcnow() - timedelta(minutes=30)
    stale_run = CheckRun(surface_id=surface_id, status=CheckRunStatus.running)
    db_session.add(stale_run)
    db_session.commit()
    stale_run.started_at = stale_started
    db_session.commit()

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    res = client.post(check_url, headers=headers)
    assert res.json()["status"] == "baseline_captured"

    db_session.refresh(stale_run)
    assert stale_run.status == CheckRunStatus.failed
