import app.routers.site_summary as site_summary_router
import app.services.check_service as check_service
import app.services.site_summary_service as site_summary_service
from app.services.llm.client import LLMCallResult
from app.services.rendered_content_service import RenderedContentError
from app.services.site_summary_service import SiteSummaryDraft


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _FakeSiteSummaryClient:
    def complete(self, system, user, response_model):
        return LLMCallResult(
            value=SiteSummaryDraft(
                categories=["Men's", "Women's", "Perfumes"],
                current_offers=["Azadi Sale — flat 40% off"],
            ),
            model="fake-model", prompt_tokens=20, completion_tokens=10,
        )

    def embed(self, texts):
        raise NotImplementedError


def _seed_competitor_with_surface(client, headers, workspace_id, name="Rival"):
    competitor = client.post(
        f"/workspaces/{workspace_id}/competitors/", json={"name": name}, headers=headers
    ).json()
    surface = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}/surfaces/",
        json={"surface_type": "other", "url": f"https://{name.lower()}.example.com", "check_frequency": "daily"},
        headers=headers,
    ).json()
    return competitor, surface


def test_get_404_before_generated(client):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor, _ = _seed_competitor_with_surface(client, headers, workspace["id"])

    res = client.get(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/site-summary/", headers=headers
    )
    assert res.status_code == 404


def _refresh(client, headers, workspace_id, competitor_id):
    """POST the refresh and return (response, finished job).

    The refresh is queued now. With no REDIS_URL configured the queue falls
    back to BackgroundTasks, which TestClient runs before the request returns,
    so the job is already terminal by the time the poll below happens.
    """
    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    )
    if res.status_code >= 400:
        return res, None
    job = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/site-summary/jobs/{res.json()['id']}",
        headers=headers,
    ).json()
    return res, job


def test_refresh_fails_without_snapshot(client, monkeypatch):
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSiteSummaryClient())
    # Job-side too: run_site_summary_job resolves its own client.
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSiteSummaryClient())

    def _raise(url):
        raise RenderedContentError("simulated render failure")
    monkeypatch.setattr(site_summary_service, "capture_rendered_text", _raise)

    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor, _ = _seed_competitor_with_surface(client, headers, workspace["id"])

    res, job = _refresh(client, headers, workspace["id"], competitor["id"])

    # Queued work reports its outcome on the job row rather than as an HTTP
    # status — same shape as briefing and discovery jobs.
    assert res.status_code == 202
    assert job["status"] == "failed"
    assert "no captured snapshot" in job["error"]


def test_refresh_fails_without_llm_configured(client, monkeypatch):
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: None)
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor, _ = _seed_competitor_with_surface(client, headers, workspace["id"])

    res = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/site-summary/refresh",
        headers=headers,
    )
    assert res.status_code == 400
    assert "is configured" in res.json()["detail"]


def test_refresh_generates_and_upserts(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor, surface = _seed_competitor_with_surface(client, headers, workspace["id"])

    check_url = (
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}"
        f"/surfaces/{surface['id']}/check"
    )
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Homepage: Men's, Women's, Perfumes. Azadi Sale 40% off!")
    baseline = client.post(check_url, headers=headers)
    assert baseline.json()["status"] == "baseline_captured"

    monkeypatch.setattr(
        site_summary_service, "capture_rendered_text",
        lambda url: "Homepage: Men's, Women's, Perfumes. Azadi Sale 40% off!"
    )
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSiteSummaryClient())
    # Job-side too: run_site_summary_job resolves its own client.
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSiteSummaryClient())
    res, job = _refresh(client, headers, workspace["id"], competitor["id"])
    assert res.status_code == 202
    assert job["status"] == "success", job["error"]

    # The summary itself is read back from its own endpoint now that the
    # refresh returns a job rather than the finished row.
    get_res = client.get(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/site-summary/", headers=headers
    )
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["categories"] == ["Men's", "Women's", "Perfumes"]
    assert body["current_offers"] == ["Azadi Sale — flat 40% off"]
    first_generated_at = body["generated_at"]

    # Refreshing again must upsert the same row, not create a second one.
    res2, job2 = _refresh(client, headers, workspace["id"], competitor["id"])
    assert res2.status_code == 202
    assert job2["status"] == "success", job2["error"]
    again = client.get(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/site-summary/", headers=headers
    ).json()
    assert again["competitor_id"] == body["competitor_id"]


def test_refresh_blocked_with_402_when_over_budget(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor, surface = _seed_competitor_with_surface(client, headers, workspace["id"])

    check_url = (
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}"
        f"/surfaces/{surface['id']}/check"
    )
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Some homepage content")
    client.post(check_url, headers=headers)

    client.put(
        f"/workspaces/{workspace['id']}/budget/", json={"monthly_cap_usd": 0.0}, headers=headers
    )
    monkeypatch.setattr(site_summary_service, "capture_rendered_text", lambda url: "Some homepage content")
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSiteSummaryClient())
    # Job-side too: run_site_summary_job resolves its own client.
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSiteSummaryClient())

    res, job = _refresh(client, headers, workspace["id"], competitor["id"])

    # Budget refusal is reported on the job now, not as a 402 on the request.
    assert res.status_code == 202
    assert job["status"] == "failed"
    assert "monthly cap" in job["error"].lower()


def test_reviewer_cannot_refresh_but_editor_can(client, monkeypatch):
    owner_headers = _register_login(client, "owner@example.com")
    _register_login(client, "reviewer@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=owner_headers).json()
    competitor, surface = _seed_competitor_with_surface(client, owner_headers, workspace["id"])
    client.post(
        f"/workspaces/{workspace['id']}/members",
        json={"email": "reviewer@example.com", "role": "reviewer"},
        headers=owner_headers,
    )
    reviewer_headers = _register_login(client, "reviewer@example.com")

    check_url = (
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}"
        f"/surfaces/{surface['id']}/check"
    )
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Some homepage content")
    client.post(check_url, headers=owner_headers)

    monkeypatch.setattr(site_summary_service, "capture_rendered_text", lambda url: "Some homepage content")
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSiteSummaryClient())
    # Job-side too: run_site_summary_job resolves its own client.
    monkeypatch.setattr(site_summary_service, "get_llm_client", lambda: _FakeSiteSummaryClient())
    forbidden = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/site-summary/refresh",
        headers=reviewer_headers,
    )
    assert forbidden.status_code == 403

    allowed = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/site-summary/refresh",
        headers=owner_headers,
    )
    assert allowed.status_code == 202


def test_site_summary_rejects_foreign_competitor_id(client, monkeypatch):
    a_headers = _register_login(client, "a@example.com")
    b_headers = _register_login(client, "b@example.com")
    workspace_a = client.post("/workspaces/", json={"name": "A Co"}, headers=a_headers).json()
    workspace_b = client.post("/workspaces/", json={"name": "B Co"}, headers=b_headers).json()
    competitor_a, _ = _seed_competitor_with_surface(client, a_headers, workspace_a["id"])

    res = client.get(
        f"/workspaces/{workspace_b['id']}/competitors/{competitor_a['id']}/site-summary/",
        headers=b_headers,
    )
    assert res.status_code == 404
