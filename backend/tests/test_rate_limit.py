import app.routers.briefings as briefings_router
import app.services.briefing_service as briefing_service
import app.services.check_service as check_service
from app.core.config import settings
from app.services.llm.client import LLMCallResult
from app.services.briefing_service import BriefingDraft


def _patch_briefing_llm(monkeypatch):
    """Both names: the router's get_llm_client only gates the 400, while the
    briefing itself is generated inside the queued job, which resolves its
    client from app.services.briefing_service.
    """

    fake = _FakeBriefingLLMClient()
    monkeypatch.setattr(briefings_router, "get_llm_client", lambda: fake)
    monkeypatch.setattr(briefing_service, "get_llm_client", lambda: fake)


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _FakeBriefingLLMClient:
    def complete(self, system, user, response_model):
        return LLMCallResult(
            value=BriefingDraft(title="X", body_markdown="Y"),
            model="fake-model", prompt_tokens=1, completion_tokens=1,
        )

    def embed(self, texts):
        raise NotImplementedError


def _seed_workspace_with_change_log(client, headers, monkeypatch):
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()
    surface = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
        json={"surface_type": "pricing", "url": "https://rival.example.com", "check_frequency": "daily"},
        headers=headers,
    ).json()

    check_url = f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/{surface['id']}/check"
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=headers)
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $15")
    check_res = client.post(check_url, headers=headers).json()

    return workspace, check_res["change_log_id"]


def test_briefing_generate_returns_429_once_limit_exceeded(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_llm_requests", 2)
    monkeypatch.setattr(settings, "rate_limit_llm_window_seconds", 60.0)

    headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_change_log(client, headers, monkeypatch)
    _patch_briefing_llm(monkeypatch)

    url = f"/workspaces/{workspace['id']}/briefings/generate-now"
    body = {"change_log_ids": [change_log_id]}

    first = client.post(url, json=body, headers=headers)
    second = client.post(url, json=body, headers=headers)
    third = client.post(url, json=body, headers=headers)

    # 202: generate-now enqueues a job and returns the job id — the
    # briefing itself is generated asynchronously.
    assert first.status_code == 202
    assert second.status_code == 202
    assert third.status_code == 429


def test_rate_limit_scopes_are_independent_per_endpoint(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_change_log(client, headers, monkeypatch)
    _patch_briefing_llm(monkeypatch)

    # Lower the limit only after seeding — seeding itself hits the
    # surface-check scope twice (baseline + change), which a limit of 1
    # would otherwise clobber.
    monkeypatch.setattr(settings, "rate_limit_llm_requests", 1)
    monkeypatch.setattr(settings, "rate_limit_llm_window_seconds", 60.0)

    briefing_url = f"/workspaces/{workspace['id']}/briefings/generate-now"
    body = {"change_log_ids": [change_log_id]}

    # Exhaust the briefing-generate bucket for this workspace.
    client.post(briefing_url, json=body, headers=headers)
    exhausted = client.post(briefing_url, json=body, headers=headers)
    assert exhausted.status_code == 429

    # A different scope (surface-check) against the same workspace must be
    # unaffected — separate buckets, not one shared per-workspace counter.
    trends_res = client.get(f"/workspaces/{workspace['id']}/insights/trends", headers=headers)
    assert trends_res.status_code == 200


def test_rate_limit_is_per_workspace_not_global(client, monkeypatch):
    headers_a = _register_login(client, "a@example.com")
    headers_b = _register_login(client, "b@example.com")
    workspace_a, change_log_id_a = _seed_workspace_with_change_log(client, headers_a, monkeypatch)
    workspace_b, change_log_id_b = _seed_workspace_with_change_log(client, headers_b, monkeypatch)
    _patch_briefing_llm(monkeypatch)

    monkeypatch.setattr(settings, "rate_limit_llm_requests", 1)
    monkeypatch.setattr(settings, "rate_limit_llm_window_seconds", 60.0)

    url_a = f"/workspaces/{workspace_a['id']}/briefings/generate-now"
    url_b = f"/workspaces/{workspace_b['id']}/briefings/generate-now"

    first_a = client.post(url_a, json={"change_log_ids": [change_log_id_a]}, headers=headers_a)
    exhausted_a = client.post(url_a, json={"change_log_ids": [change_log_id_a]}, headers=headers_a)
    first_b = client.post(url_b, json={"change_log_ids": [change_log_id_b]}, headers=headers_b)

    assert first_a.status_code == 202
    assert exhausted_a.status_code == 429
    assert first_b.status_code == 202
