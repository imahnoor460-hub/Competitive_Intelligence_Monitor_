"""POST /surfaces/discover and POST /site-summary/refresh as queued jobs.

Both used to run inline. Discovery walks a site's sitemaps and the refresh
fetches one page per active surface — up to 40 after discovery — which is long
enough for an edge proxy to drop the connection while the work carries on
invisibly. They now enqueue and hand back a job to poll, matching briefings,
battlecard updates and the create-competitor discovery path.

With no REDIS_URL configured, app/queue.py falls back to BackgroundTasks,
which TestClient runs before the request returns — so a job is already
terminal by the time these tests poll it.
"""
import pytest

import app.routers.site_summary as site_summary_router
import app.services.check_service as check_service
import app.services.competitor_discovery_service as discovery_service
import app.services.site_summary_service as site_summary_service
from app.models.competitor_discovery_job import CompetitorDiscoveryJob
from app.models.site_summary_job import SiteSummaryJob, SiteSummaryJobStatus
from app.models.surface import Surface, SurfaceType
from app.services.llm.client import LLMCallResult
from app.services.site_summary_service import SiteSummaryDraft


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _FakeSummaryClient:
    def complete(self, system, user, response_model):
        return LLMCallResult(
            value=SiteSummaryDraft(categories=["Women's"], current_offers=["Sale"]),
            model="fake-model", prompt_tokens=20, completion_tokens=10,
        )

    def embed(self, texts):
        raise NotImplementedError


@pytest.fixture()
def workspace(client):
    headers = _register_login(client, "owner@example.com")
    ws = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    return headers, ws["id"]


def _seed(client, headers, workspace_id, url="https://rival.example.com"):
    competitor = client.post(
        f"/workspaces/{workspace_id}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()
    surface = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}/surfaces/",
        json={"surface_type": "other", "url": url, "check_frequency": "daily"},
        headers=headers,
    ).json()
    return competitor["id"], surface["id"]


# --------------------------------------------------------------- discover ---


def test_discover_returns_a_job_and_creates_surfaces(client, workspace, monkeypatch):
    headers, workspace_id = workspace
    competitor_id, _ = _seed(client, headers, workspace_id)

    monkeypatch.setattr(
        discovery_service,
        "discover_surfaces",
        lambda url: [
            (SurfaceType.other, "Home", "https://rival.example.com"),
            (SurfaceType.pricing, "Pricing", "https://rival.example.com/pricing"),
        ],
    )

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/discover",
        headers=headers,
    )

    assert res.status_code == 202
    job_id = res.json()["id"]

    job = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/discovery-jobs/{job_id}",
        headers=headers,
    ).json()
    assert job["status"] == "success", job["error"]

    urls = {
        s["url"]
        for s in client.get(
            f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/",
            headers=headers,
        ).json()
    }
    assert "https://rival.example.com/pricing" in urls


def test_discover_does_not_duplicate_pages_already_tracked(client, workspace, monkeypatch):
    """The create path has no surfaces yet so dedupe is a no-op there; on this
    route it is what stops every run re-inserting the whole site.
    """
    headers, workspace_id = workspace
    competitor_id, _ = _seed(client, headers, workspace_id)

    # The seeded surface's own URL comes back from discovery, as it would in
    # reality — the homepage is always the first result.
    monkeypatch.setattr(
        discovery_service,
        "discover_surfaces",
        lambda url: [
            (SurfaceType.other, "Home", "https://rival.example.com"),
            (SurfaceType.pricing, "Pricing", "https://rival.example.com/pricing"),
        ],
    )

    for _ in range(2):
        client.post(
            f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/discover",
            headers=headers,
        )
        # Resolve the in-flight job so the second call is not deduped by the
        # route's own guard — this test is about surface dedupe, not that.

    surfaces = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/", headers=headers
    ).json()
    urls = [s["url"] for s in surfaces]

    assert len(urls) == len(set(urls)), f"duplicate surfaces created: {urls}"
    assert len(urls) == 2


def test_discover_returns_the_in_flight_job_instead_of_a_second_one(
    client, workspace, monkeypatch, db_session
):
    headers, workspace_id = workspace
    competitor_id, _ = _seed(client, headers, workspace_id)
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: [])

    first = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/discover",
        headers=headers,
    )
    assert first.status_code == 202

    # Force the job back to a non-terminal state to simulate one still running
    # when a second request lands.
    job = db_session.query(CompetitorDiscoveryJob).filter(
        CompetitorDiscoveryJob.id == first.json()["id"]
    ).first()
    job.status = "running"
    db_session.commit()

    second = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/discover",
        headers=headers,
    )

    # 200, not 202: nothing new was created.
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_discover_still_requires_a_seed_surface(client, workspace):
    headers, workspace_id = workspace
    competitor = client.post(
        f"/workspaces/{workspace_id}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}/surfaces/discover",
        headers=headers,
    )

    assert res.status_code == 400


def test_discover_does_not_launch_a_browser_in_the_request(client, workspace, monkeypatch):
    """The whole point of the migration: the request enqueues and returns."""
    headers, workspace_id = workspace
    competitor_id, _ = _seed(client, headers, workspace_id)

    import app.services.surface_discovery_service as sds

    monkeypatch.setattr(
        sds, "sync_playwright", lambda *a, **k: pytest.fail("Chromium launched")
    )
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: [])

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/discover",
        headers=headers,
    )

    assert res.status_code == 202


# ---------------------------------------------------------- site summary ---


def _baseline(client, headers, workspace_id, competitor_id, surface_id, monkeypatch):
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Women's wear, sale")
    client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/surfaces/{surface_id}/check",
        headers=headers,
    )


def test_refresh_returns_a_job_and_generates_the_summary(client, workspace, monkeypatch):
    headers, workspace_id = workspace
    competitor_id, surface_id = _seed(client, headers, workspace_id)
    _baseline(client, headers, workspace_id, competitor_id, surface_id, monkeypatch)

    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSummaryClient())
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSummaryClient())

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    )
    assert res.status_code == 202

    job = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/site-summary/jobs/{res.json()['id']}",
        headers=headers,
    ).json()
    assert job["status"] == "success", job["error"]

    summary = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/", headers=headers
    ).json()
    assert summary["categories"] == ["Women's"]


def test_refresh_returns_the_in_flight_job_instead_of_a_second_one(
    client, workspace, monkeypatch, db_session
):
    headers, workspace_id = workspace
    competitor_id, surface_id = _seed(client, headers, workspace_id)
    _baseline(client, headers, workspace_id, competitor_id, surface_id, monkeypatch)

    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSummaryClient())
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSummaryClient())

    first = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    )
    assert first.status_code == 202

    job = db_session.query(SiteSummaryJob).filter(
        SiteSummaryJob.id == first.json()["id"]
    ).first()
    job.status = SiteSummaryJobStatus.running
    db_session.commit()

    second = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    )

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_refresh_still_rejects_a_deployment_with_no_llm(client, workspace, monkeypatch):
    """Checked in the request rather than the job, so the failure is immediate
    instead of queueing work that can only fail.
    """
    headers, workspace_id = workspace
    competitor_id, _ = _seed(client, headers, workspace_id)
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: None)

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    )

    assert res.status_code == 400


def test_refresh_job_records_its_failure(client, workspace, monkeypatch):
    headers, workspace_id = workspace
    competitor_id, _ = _seed(client, headers, workspace_id)

    # No baseline check, so there is no snapshot to summarize.
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSummaryClient())
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSummaryClient())

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    )
    job = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/site-summary/jobs/{res.json()['id']}",
        headers=headers,
    ).json()

    assert job["status"] == "failed"
    assert "no captured snapshot" in job["error"]


def test_refresh_job_is_scoped_to_its_workspace_and_competitor(client, workspace, monkeypatch):
    headers, workspace_id = workspace
    competitor_id, surface_id = _seed(client, headers, workspace_id)
    _baseline(client, headers, workspace_id, competitor_id, surface_id, monkeypatch)
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSummaryClient())
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSummaryClient())

    job_id = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    ).json()["id"]

    other = client.post(
        f"/workspaces/{workspace_id}/competitors/", json={"name": "Other"}, headers=headers
    ).json()

    res = client.get(
        f"/workspaces/{workspace_id}/competitors/{other['id']}"
        f"/site-summary/jobs/{job_id}",
        headers=headers,
    )
    assert res.status_code == 404


def test_reviewer_cannot_queue_a_refresh(client, workspace, monkeypatch):
    owner_headers, workspace_id = workspace
    _register_login(client, "reviewer@example.com")
    competitor_id, _ = _seed(client, owner_headers, workspace_id)
    client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": "reviewer@example.com", "role": "reviewer"},
        headers=owner_headers,
    )
    reviewer_headers = _register_login(client, "reviewer@example.com")

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=reviewer_headers,
    )

    assert res.status_code == 403


# ------------------------------------------------------------ rehydration ---


def test_active_jobs_includes_site_summary_jobs(client, workspace, monkeypatch, db_session):
    """A reload must be able to re-attach a poller to a running refresh."""
    headers, workspace_id = workspace
    competitor_id, surface_id = _seed(client, headers, workspace_id)
    _baseline(client, headers, workspace_id, competitor_id, surface_id, monkeypatch)
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSummaryClient())
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSummaryClient())

    job_id = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    ).json()["id"]

    # Terminal jobs must not come back — only work still in flight.
    assert client.get(
        f"/workspaces/{workspace_id}/jobs/active", headers=headers
    ).json()["site_summary_jobs"] == []

    job = db_session.query(SiteSummaryJob).filter(SiteSummaryJob.id == job_id).first()
    job.status = SiteSummaryJobStatus.running
    db_session.commit()

    active = client.get(f"/workspaces/{workspace_id}/jobs/active", headers=headers).json()
    assert active["site_summary_jobs"] == [
        {"id": job_id, "competitor_id": competitor_id}
    ]
