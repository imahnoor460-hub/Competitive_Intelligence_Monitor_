import app.services.check_service as check_service
from app.services.llm.client import LLMCallResult
from app.services.llm.baseline_summary import BaselineFact, BaselineSummaryResult
from app.services.llm.scoring import MaterialityResult
from app.services.site_summary_service import SiteSummaryDraft


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _ScoringClient:
    """Fake client for the surface-check pipeline.

    A check does not make only the scoring call: capturing a surface's first
    snapshot also runs summarize_baseline_snapshot (BaselineSummaryResult)
    and generate_site_summary (SiteSummaryDraft). Returning a
    MaterialityResult for those too used to blow up on `result.facts` in
    check_service._apply_baseline_summary — outside that function's
    try/except, so it surfaced as a 500 rather than degrading. Dispatch on
    response_model and hand each caller the shape it asked for.
    """

    def __init__(self, score, classification):
        self._score = score
        self._classification = classification

    def complete(self, system, user, response_model):
        if response_model is MaterialityResult:
            value = MaterialityResult(
                score=self._score, classification=self._classification, rationale="x"
            )
        elif response_model is BaselineSummaryResult:
            value = BaselineSummaryResult(
                headline="Baseline captured",
                facts=[BaselineFact(label="Plan A", value="$10")],
            )
        elif response_model is SiteSummaryDraft:
            value = SiteSummaryDraft(categories=["Pricing"], current_offers=[])
        else:
            raise AssertionError(f"unexpected response_model {response_model!r}")

        return LLMCallResult(
            value=value, model="fake-model", prompt_tokens=10, completion_tokens=5,
        )

    def embed(self, texts):
        raise NotImplementedError


def _seed_scored_change(client, headers, workspace_id, competitor_id, monkeypatch, score, classification):
    surface = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/",
        json={"surface_type": "pricing", "url": f"https://rival{competitor_id}.example.com", "check_frequency": "daily"},
        headers=headers,
    ).json()
    check_url = f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface['id']}/check"

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=headers)
    monkeypatch.setattr(check_service, "get_llm_client", lambda: _ScoringClient(score, classification))
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $15")
    return client.post(check_url, headers=headers).json()


def test_comparison_shape_without_own_site(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()
    _seed_scored_change(client, headers, workspace["id"], competitor["id"], monkeypatch, 80, "pricing_move")

    res = client.get(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/comparison", headers=headers
    )
    assert res.status_code == 200
    body = res.json()

    assert body["competitor"]["id"] == competitor["id"]
    assert body["benchmark"] is None
    assert body["profile"] is None
    assert body["battlecard"] is None
    assert body["traffic"] is None

    summary = body["change_summary"]
    assert summary["total_changes"] == 1
    assert summary["material_count"] == 1
    assert summary["avg_materiality"] == 80
    assert summary["classification_counts"] == {"pricing_move": 1}
    assert summary["last_change_at"] is not None
    assert len(summary["trend"]) == 30


def test_comparison_includes_own_site_when_configured(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()
    _seed_scored_change(client, headers, workspace["id"], competitor["id"], monkeypatch, 80, "pricing_move")

    client.put(
        f"/workspaces/{workspace['id']}/own-site/",
        json={"url": "https://acme.example.com"},
        headers=headers,
    )
    own_site = client.get(f"/workspaces/{workspace['id']}/own-site/", headers=headers).json()

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    client.post(
        f"/workspaces/{workspace['id']}/competitors/{own_site['competitor_id']}"
        f"/surfaces/{own_site['surface_id']}/check",
        headers=headers,
    )
    monkeypatch.setattr(check_service, "get_llm_client", lambda: _ScoringClient(40, "other"))
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v2")
    client.post(
        f"/workspaces/{workspace['id']}/competitors/{own_site['competitor_id']}"
        f"/surfaces/{own_site['surface_id']}/check",
        headers=headers,
    )

    res = client.get(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/comparison", headers=headers
    )
    assert res.status_code == 200
    body = res.json()

    assert body["benchmark"] is not None
    assert body["benchmark"]["competitor"]["id"] == own_site["competitor_id"]
    assert body["benchmark"]["change_summary"]["total_changes"] == 1


def test_comparison_falls_back_to_compare_to_competitor_without_own_site(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor_a = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival A"}, headers=headers
    ).json()
    competitor_b = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival B"}, headers=headers
    ).json()
    _seed_scored_change(client, headers, workspace["id"], competitor_a["id"], monkeypatch, 80, "pricing_move")
    _seed_scored_change(client, headers, workspace["id"], competitor_b["id"], monkeypatch, 30, "other")

    res = client.get(
        f"/workspaces/{workspace['id']}/competitors/{competitor_a['id']}/comparison"
        f"?compare_to={competitor_b['id']}",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()

    assert body["benchmark"] is not None
    assert body["benchmark"]["competitor"]["id"] == competitor_b["id"]
    assert body["benchmark"]["change_summary"]["total_changes"] == 1


def test_own_site_takes_priority_over_compare_to(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor_a = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival A"}, headers=headers
    ).json()
    competitor_b = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival B"}, headers=headers
    ).json()
    client.put(
        f"/workspaces/{workspace['id']}/own-site/",
        json={"url": "https://acme.example.com"},
        headers=headers,
    )
    own_site = client.get(f"/workspaces/{workspace['id']}/own-site/", headers=headers).json()

    res = client.get(
        f"/workspaces/{workspace['id']}/competitors/{competitor_a['id']}/comparison"
        f"?compare_to={competitor_b['id']}",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["benchmark"]["competitor"]["id"] == own_site["competitor_id"]


def test_comparison_rejects_foreign_competitor_id(client, monkeypatch):
    a_headers = _register_login(client, "a@example.com")
    b_headers = _register_login(client, "b@example.com")
    workspace_a = client.post("/workspaces/", json={"name": "A Co"}, headers=a_headers).json()
    workspace_b = client.post("/workspaces/", json={"name": "B Co"}, headers=b_headers).json()
    competitor_a = client.post(
        f"/workspaces/{workspace_a['id']}/competitors/", json={"name": "Rival"}, headers=a_headers
    ).json()

    res = client.get(
        f"/workspaces/{workspace_b['id']}/competitors/{competitor_a['id']}/comparison", headers=b_headers
    )
    assert res.status_code == 404


def test_comparison_404_for_missing_competitor(client):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()

    res = client.get(f"/workspaces/{workspace['id']}/competitors/999999/comparison", headers=headers)
    assert res.status_code == 404
