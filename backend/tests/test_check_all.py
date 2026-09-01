from datetime import datetime, timedelta

import app.routers.competitor as competitor_router
import app.services.check_service as check_service
from app.models.battlecard_update_job import (
    BattlecardUpdateJob,
    BattlecardUpdateJobStatus,
)
from app.models.check_run import CheckRun, CheckRunStatus
from app.models.check_sweep import CheckSweep, CheckSweepStatus


def _setup(client, monkeypatch, surface_count=3):
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

    surface_ids = []
    for i in range(surface_count):
        surface = client.post(
            f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
            json={
                "surface_type": "pricing",
                "url": f"https://rival.example.com/p{i}",
                "check_frequency": "daily",
            },
            headers=headers,
        ).json()
        surface_ids.append(surface["id"])

    monkeypatch.setattr(
        check_service, "capture_clean_snapshot", lambda url: f"content of {url}"
    )
    return headers, workspace["id"], competitor["id"], surface_ids


def test_check_all_creates_one_sweep_covering_every_active_surface(client, monkeypatch):
    headers, workspace_id, _competitor_id, surface_ids = _setup(client, monkeypatch)

    res = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers)

    assert res.status_code == 202
    sweep = res.json()
    assert sweep["total"] == len(surface_ids)

    # Without a queue configured, dispatch falls back to BackgroundTasks, which
    # TestClient drains before returning — so by now every check has run.
    final = client.get(
        f"/workspaces/{workspace_id}/check-sweeps/{sweep['id']}", headers=headers
    ).json()
    assert final["status"] == "success"
    assert final["finished"] == len(surface_ids)
    assert final["failed_count"] == 0
    assert final["finished_at"] is not None


def test_check_all_returns_the_running_sweep_instead_of_starting_a_second(
    client, monkeypatch, db_session
):
    """Two tabs, or a double click, must not check every surface twice."""

    headers, workspace_id, _competitor_id, _surface_ids = _setup(client, monkeypatch)

    first = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers).json()

    # Force the first sweep back to in-flight; it completed inline above.
    sweep = db_session.query(CheckSweep).filter(CheckSweep.id == first["id"]).one()
    sweep.status = CheckSweepStatus.running
    sweep.finished_at = None
    db_session.commit()

    res = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers)

    assert res.status_code == 200          # 200, not 202 — nothing new was queued
    assert res.json()["id"] == first["id"]
    assert db_session.query(CheckSweep).count() == 1


def test_check_all_does_not_duplicate_a_surface_already_being_checked(
    client, monkeypatch, db_session
):
    headers, workspace_id, _competitor_id, surface_ids = _setup(client, monkeypatch)

    db_session.add(
        CheckRun(surface_id=surface_ids[0], status=CheckRunStatus.running)
    )
    db_session.commit()

    sweep = client.post(
        f"/workspaces/{workspace_id}/check-all", headers=headers
    ).json()

    # The busy surface is skipped, so the sweep's total reflects what it
    # actually claimed rather than how many surfaces exist.
    assert sweep["total"] == len(surface_ids) - 1


def test_check_all_closes_immediately_when_there_is_nothing_to_check(client, monkeypatch):
    headers, workspace_id, _competitor_id, _surface_ids = _setup(
        client, monkeypatch, surface_count=0
    )

    sweep = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers).json()

    # A sweep with no children has nothing that could ever finish it, so it
    # must not be left queued forever.
    assert sweep["total"] == 0
    assert sweep["status"] == "success"
    assert sweep["finished_at"] is not None


def test_check_all_never_reaches_another_workspaces_surfaces(client, monkeypatch):
    headers, workspace_id, _competitor_id, surface_ids = _setup(client, monkeypatch)

    other = client.post("/workspaces/", json={"name": "Other"}, headers=headers).json()
    other_competitor = client.post(
        f"/workspaces/{other['id']}/competitors/", json={"name": "Foe"}, headers=headers
    ).json()
    client.post(
        f"/workspaces/{other['id']}/competitors/{other_competitor['id']}/surfaces/",
        json={"surface_type": "pricing", "url": "https://foe.example.com", "check_frequency": "daily"},
        headers=headers,
    )

    sweep = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers).json()

    assert sweep["total"] == len(surface_ids)


def test_check_sweep_requires_membership(client, monkeypatch):
    headers, workspace_id, _competitor_id, _surface_ids = _setup(client, monkeypatch)
    sweep = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers).json()

    client.post(
        "/auth/register",
        json={"email": "mallory@example.com", "password": "supersecret1", "full_name": "M"},
    )
    intruder = client.post(
        "/auth/login", json={"email": "mallory@example.com", "password": "supersecret1"}
    ).json()
    intruder_headers = {"Authorization": f"Bearer {intruder['access_token']}"}

    res = client.get(
        f"/workspaces/{workspace_id}/check-sweeps/{sweep['id']}", headers=intruder_headers
    )
    assert res.status_code in (403, 404)


def test_a_single_check_response_always_carries_its_run_id(client, monkeypatch):
    """One response shape whether the check ran inline or was queued, so the
    frontend has a single code path."""

    headers, workspace_id, competitor_id, surface_ids = _setup(client, monkeypatch)

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/surfaces/{surface_ids[0]}/check",
        headers=headers,
    )

    assert res.status_code == 200          # inline: no queue configured
    body = res.json()
    assert body["status"] == "baseline_captured"
    assert isinstance(body["check_run_id"], int)

    run = client.get(
        f"/workspaces/{workspace_id}/check-runs/{body['check_run_id']}", headers=headers
    ).json()
    assert run["status"] == "success"


def test_active_jobs_lists_in_flight_work_so_a_refresh_can_recover_it(
    client, monkeypatch, db_session
):
    headers, workspace_id, _competitor_id, surface_ids = _setup(client, monkeypatch)

    # Nothing running yet.
    empty = client.get(f"/workspaces/{workspace_id}/jobs/active", headers=headers).json()
    assert empty["check_runs"] == []
    assert empty["check_sweeps"] == []

    queued = CheckRun(surface_id=surface_ids[0], status=CheckRunStatus.queued)
    running = CheckRun(surface_id=surface_ids[1], status=CheckRunStatus.running)
    done = CheckRun(surface_id=surface_ids[2], status=CheckRunStatus.success)
    sweep = CheckSweep(workspace_id=workspace_id, status=CheckSweepStatus.running, total=2)
    db_session.add_all([queued, running, done, sweep])
    db_session.commit()

    active = client.get(f"/workspaces/{workspace_id}/jobs/active", headers=headers).json()

    returned = {r["id"] for r in active["check_runs"]}
    assert returned == {queued.id, running.id}      # the finished one is excluded
    assert [s["id"] for s in active["check_sweeps"]] == [sweep.id]


def test_active_jobs_never_leaks_another_workspaces_runs(client, monkeypatch, db_session):
    headers, workspace_id, _competitor_id, surface_ids = _setup(client, monkeypatch)

    other = client.post("/workspaces/", json={"name": "Other"}, headers=headers).json()
    other_competitor = client.post(
        f"/workspaces/{other['id']}/competitors/", json={"name": "Foe"}, headers=headers
    ).json()
    other_surface = client.post(
        f"/workspaces/{other['id']}/competitors/{other_competitor['id']}/surfaces/",
        json={"surface_type": "pricing", "url": "https://foe.example.com", "check_frequency": "daily"},
        headers=headers,
    ).json()

    db_session.add(CheckRun(surface_id=other_surface["id"], status=CheckRunStatus.running))
    db_session.add(CheckRun(surface_id=surface_ids[0], status=CheckRunStatus.running))
    db_session.commit()

    active = client.get(f"/workspaces/{workspace_id}/jobs/active", headers=headers).json()

    assert [r["surface_id"] for r in active["check_runs"]] == [surface_ids[0]]


def test_queued_runs_do_not_count_as_finished_in_the_crawl_success_rate(
    client, monkeypatch, db_session
):
    """A queued run has no outcome yet, exactly like a running one — counting
    it as finished would understate the dashboard's success rate."""

    headers, workspace_id, _competitor_id, surface_ids = _setup(client, monkeypatch)

    db_session.add_all([
        CheckRun(surface_id=surface_ids[0], status=CheckRunStatus.success),
        CheckRun(surface_id=surface_ids[1], status=CheckRunStatus.queued),
        CheckRun(surface_id=surface_ids[2], status=CheckRunStatus.running),
    ])
    db_session.commit()

    latest = client.get(
        f"/workspaces/{workspace_id}/check-runs/latest", headers=headers
    ).json()

    assert latest["total_runs"] == 3
    assert latest["finished_runs"] == 1
    assert latest["successful_runs"] == 1


def test_a_stale_queued_run_does_not_block_the_surface_forever(
    client, monkeypatch, db_session
):
    """A queue message that never arrives would otherwise leave the surface
    permanently un-checkable, because the in-flight guard counts it as busy."""

    headers, workspace_id, competitor_id, surface_ids = _setup(client, monkeypatch)

    stale = CheckRun(
        surface_id=surface_ids[0],
        status=CheckRunStatus.queued,
        started_at=datetime.utcnow() - timedelta(minutes=30),
    )
    db_session.add(stale)
    db_session.commit()

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/surfaces/{surface_ids[0]}/check",
        headers=headers,
    )

    assert res.status_code == 200
    assert res.json()["status"] == "baseline_captured"

    db_session.refresh(stale)
    assert stale.status == CheckRunStatus.failed


def test_active_jobs_carry_the_competitor_id_their_poll_url_needs(
    client, monkeypatch, db_session
):
    """Battlecard and discovery jobs are polled at a competitor-nested URL.

    Returning bare ids would make them unrecoverable after a refresh: the
    frontend rebuilds the poll URL from this payload, and
    `/competitors/{competitor_id}/discovery-jobs/{id}` cannot be built from an
    id alone. Briefing jobs stay bare ids because their poll path is
    workspace-level.
    """

    headers, workspace_id, _competitor_id, _surface_ids = _setup(client, monkeypatch)

    # A website on the competitor is what queues discovery — see
    # routers/competitor.py. Patched on the router, which is the namespace
    # `dispatch_job` reads the callable out of; patching the service module
    # would leave the router holding its own already-imported reference and
    # the BackgroundTasks fallback would drive a real browser.
    monkeypatch.setattr(
        competitor_router, "run_competitor_discovery_job", lambda job_id: None
    )
    created = client.post(
        f"/workspaces/{workspace_id}/competitors/",
        json={"name": "Newcomer", "website_url": "https://newcomer.example.com"},
        headers=headers,
    ).json()

    db_session.add(
        BattlecardUpdateJob(
            workspace_id=workspace_id,
            competitor_id=created["id"],
            status=BattlecardUpdateJobStatus.running,
            change_log_ids=[],
        )
    )
    db_session.commit()

    active = client.get(f"/workspaces/{workspace_id}/jobs/active", headers=headers).json()

    assert [j["competitor_id"] for j in active["competitor_discovery_jobs"]] == [
        created["id"]
    ]
    assert [j["competitor_id"] for j in active["battlecard_update_jobs"]] == [
        created["id"]
    ]
    assert all(j["id"] > 0 for j in active["competitor_discovery_jobs"])


def test_a_finished_run_records_what_the_check_concluded(client, monkeypatch):
    """`status` only says the run finished; `outcome` says what it found.

    A worker-executed check has no response body to put "change detected" in,
    so without this the queued path could only ever report "done" — a visible
    downgrade from the inline path, and the queued path is the default
    wherever a queue is configured.
    """

    headers, workspace_id, competitor_id, surface_ids = _setup(
        client, monkeypatch, surface_count=1
    )
    surface_id = surface_ids[0]

    posted = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/surfaces/{surface_id}/check",
        headers=headers,
    ).json()

    # First check of a surface captures the baseline rather than diffing.
    assert posted["status"] == "baseline_captured"
    run_id = posted["check_run_id"]

    runs = client.get(
        f"/workspaces/{workspace_id}/jobs/active", headers=headers
    ).json()["check_runs"]
    assert runs == [], "the inline check finished, so nothing is still active"

    run = client.get(
        f"/workspaces/{workspace_id}/check-runs/{run_id}", headers=headers
    ).json()
    assert run["status"] == "success"
    assert run["outcome"] == "baseline_captured"
