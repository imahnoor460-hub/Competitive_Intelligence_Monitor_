import app.routers.briefings as briefings_router
import app.services.briefing_service as briefing_service
import app.routers.battlecards as battlecards_router
import app.services.battlecard_service as battlecard_service
import app.services.check_service as check_service
from app.services.llm.client import LLMCallResult
from app.services.briefing_service import BriefingDraft
from app.models.battlecard_update_job import BattlecardUpdateJob
from app.services.battlecard_service import BattlecardDraft
from app.services.llm.scoring import MaterialityResult


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

    return workspace, competitor, check_res["change_log_id"]


class _FakeBriefingLLMClient:
    def complete(self, system, user, response_model):
        return LLMCallResult(
            value=BriefingDraft(title="X", body_markdown="Y"),
            model="fake-model", prompt_tokens=10, completion_tokens=5,
        )

    def embed(self, texts):
        raise NotImplementedError


class _FakeBattlecardLLMClient:
    def complete(self, system, user, response_model):
        return LLMCallResult(
            value=BattlecardDraft(change_summary="s", updated_content_markdown="c"),
            model="fake-model", prompt_tokens=10, completion_tokens=5,
        )

    def embed(self, texts):
        raise NotImplementedError


def test_get_budget_defaults_to_unlimited(client):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()

    res = client.get(f"/workspaces/{workspace['id']}/budget/", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["monthly_cap_usd"] is None
    assert body["estimated_spend_usd"] == 0.0


def test_editor_cannot_set_budget_only_owner_can(client):
    owner_headers = _register_login(client, "owner@example.com")
    _register_login(client, "editor@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=owner_headers).json()
    client.post(
        f"/workspaces/{workspace['id']}/members",
        json={"email": "editor@example.com", "role": "editor"},
        headers=owner_headers,
    )
    editor_headers = _register_login(client, "editor@example.com")

    forbidden = client.put(
        f"/workspaces/{workspace['id']}/budget/",
        json={"monthly_cap_usd": 5.0},
        headers=editor_headers,
    )
    assert forbidden.status_code == 403

    allowed = client.put(
        f"/workspaces/{workspace['id']}/budget/",
        json={"monthly_cap_usd": 5.0},
        headers=owner_headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["monthly_cap_usd"] == 5.0


def test_briefing_generation_blocked_with_402_when_over_budget(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace, _, change_log_id = _seed_workspace_with_change_log(client, headers, monkeypatch)

    client.put(
        f"/workspaces/{workspace['id']}/budget/", json={"monthly_cap_usd": 0.0}, headers=headers
    )
    _patch_briefing_llm(monkeypatch)

    res = client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"change_log_ids": [change_log_id]},
        headers=headers,
    )
    assert res.status_code == 402


def test_battlecard_propose_blocked_with_402_when_over_budget(client, monkeypatch, db_session):
    headers = _register_login(client, "owner@example.com")
    workspace, competitor, change_log_id = _seed_workspace_with_change_log(client, headers, monkeypatch)

    client.put(
        f"/workspaces/{workspace['id']}/budget/", json={"monthly_cap_usd": 0.0}, headers=headers
    )
    fake = _FakeBattlecardLLMClient()
    monkeypatch.setattr(battlecards_router, "get_llm_client", lambda: fake)
    monkeypatch.setattr(battlecard_service, "get_llm_client", lambda: fake)

    # Answered synchronously, like briefings/generate-now: the caller is told
    # 402 up front instead of getting a 202 and having to poll the job to
    # discover it was never going to run.
    res = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/battlecard/updates",
        json={"change_log_ids": [change_log_id]},
        headers=headers,
    )
    assert res.status_code == 402

    # And no job row was enqueued for work the budget had already refused —
    # the point of moving the guard ahead of the enqueue. Checked against the
    # table because the jobs are only readable by id through the API.
    assert db_session.query(BattlecardUpdateJob).count() == 0


def test_insights_trends_degrades_gracefully_when_over_budget(client, monkeypatch):
    import app.routers.insights as insights_router

    headers = _register_login(client, "owner@example.com")
    workspace, _, _ = _seed_workspace_with_change_log(client, headers, monkeypatch)

    client.put(
        f"/workspaces/{workspace['id']}/budget/", json={"monthly_cap_usd": 0.0}, headers=headers
    )
    monkeypatch.setattr(insights_router, "get_llm_client", lambda: _FakeBriefingLLMClient())

    res = client.get(f"/workspaces/{workspace['id']}/insights/trends", headers=headers)
    assert res.status_code == 200
    assert res.json()["summary"] is None


def test_surface_check_leaves_change_unscored_when_over_budget(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
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

    client.put(
        f"/workspaces/{workspace['id']}/budget/", json={"monthly_cap_usd": 0.0}, headers=headers
    )

    class _ScoringClient:
        def complete(self, system, user, response_model):
            return LLMCallResult(
                value=MaterialityResult(score=90, classification="pricing_move", rationale="x"),
                model="fake-model", prompt_tokens=10, completion_tokens=5,
            )

        def embed(self, texts):
            raise NotImplementedError

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=headers)
    monkeypatch.setattr(check_service, "get_llm_client", lambda: _ScoringClient())
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $15")

    res = client.post(check_url, headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "change_detected"

    logs = client.get(f"/workspaces/{workspace['id']}/change-logs/", headers=headers).json()
    assert logs[0]["materiality_score"] is None


def test_budget_spend_by_purpose_breaks_down_real_usage(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace, _, change_log_id = _seed_workspace_with_change_log(client, headers, monkeypatch)

    empty = client.get(f"/workspaces/{workspace['id']}/budget/", headers=headers).json()
    assert empty["spend_by_purpose"] == {}

    _patch_briefing_llm(monkeypatch)
    client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"change_log_ids": [change_log_id]},
        headers=headers,
    )

    after = client.get(f"/workspaces/{workspace['id']}/budget/", headers=headers).json()
    assert after["spend_by_purpose"].get("briefing", 0) > 0
    assert abs(sum(after["spend_by_purpose"].values()) - after["estimated_spend_usd"]) < 1e-9
