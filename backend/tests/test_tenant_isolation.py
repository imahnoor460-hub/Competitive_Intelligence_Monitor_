"""Cross-tenant isolation sweep — parametrized across every workspace-scoped
endpoint. The `get_current_workspace`/`require_role` dependencies already
block a non-member from touching a workspace_id they don't belong to; the
real risk this file targets is the *other* shape of the bug: a legitimate
member of workspace B passing a sub-resource id (competitor_id, briefing_id,
approval_item_id, etc.) that actually belongs to workspace A into one of
workspace B's own URLs, and a query that forgot to also filter by
workspace_id returning A's data anyway.
"""

import app.routers.briefings as briefings_router
import app.services.briefing_service as briefing_service
import app.routers.battlecards as battlecards_router
import app.services.battlecard_service as battlecard_service
import app.services.check_service as check_service
from app.services.llm.client import LLMCallResult
from app.services.briefing_service import BriefingDraft
from app.services.battlecard_service import BattlecardDraft
from app.services.llm.scoring import MaterialityResult


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _FakeScoringClient:
    def complete(self, system, user, response_model):
        if response_model is MaterialityResult:
            return LLMCallResult(
                value=MaterialityResult(score=80, classification="pricing_move", rationale="r"),
                model="fake-model", prompt_tokens=1, completion_tokens=1,
            )
        raise NotImplementedError

    def embed(self, texts):
        raise NotImplementedError


class _FakeBriefingLLMClient:
    def complete(self, system, user, response_model):
        return LLMCallResult(
            value=BriefingDraft(title="T", body_markdown="B"),
            model="fake-model", prompt_tokens=1, completion_tokens=1,
        )

    def embed(self, texts):
        raise NotImplementedError


class _FakeBattlecardLLMClient:
    def complete(self, system, user, response_model):
        return LLMCallResult(
            value=BattlecardDraft(change_summary="s", updated_content_markdown="c"),
            model="fake-model", prompt_tokens=1, completion_tokens=1,
        )

    def embed(self, texts):
        raise NotImplementedError


def _seed_full_workspace(client, monkeypatch, email, name):
    headers = _register_login(client, email)
    workspace = client.post("/workspaces/", json={"name": name}, headers=headers).json()
    wid = workspace["id"]

    competitor = client.post(
        f"/workspaces/{wid}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()
    cid = competitor["id"]

    slug = name.replace(" ", "-").lower()
    surface = client.post(
        f"/workspaces/{wid}/competitors/{cid}/surfaces/",
        json={"surface_type": "pricing", "url": f"https://{slug}.example.com", "check_frequency": "daily"},
        headers=headers,
    ).json()
    sid = surface["id"]

    check_url = f"/workspaces/{wid}/competitors/{cid}/surfaces/{sid}/check"
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=headers)
    monkeypatch.setattr(check_service, "get_llm_client", lambda: _FakeScoringClient())
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $15")
    check_res = client.post(check_url, headers=headers).json()
    change_log_id = check_res["change_log_id"]

    # Both names — the briefing is generated inside the queued job, which
    # resolves its client from app.services.briefing_service, not the router.
    fake_llm = _FakeBriefingLLMClient()
    monkeypatch.setattr(briefings_router, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(briefing_service, "get_llm_client", lambda: fake_llm)
    briefing = client.post(
        f"/workspaces/{wid}/briefings/generate-now",
        json={"change_log_ids": [change_log_id]},
        headers=headers,
    ).json()
    approval = client.get(f"/workspaces/{wid}/approvals/", headers=headers).json()[0]

    # Both names — the proposal is drafted inside the queued job, which
    # resolves its client from app.services.battlecard_service.
    fake_bc = _FakeBattlecardLLMClient()
    monkeypatch.setattr(battlecards_router, "get_llm_client", lambda: fake_bc)
    monkeypatch.setattr(battlecard_service, "get_llm_client", lambda: fake_bc)
    battlecard_update = client.post(
        f"/workspaces/{wid}/competitors/{cid}/battlecard/updates",
        json={"change_log_ids": [change_log_id]},
        headers=headers,
    ).json()

    response_item = client.post(
        f"/workspaces/{wid}/response-library/",
        json={"title": "T", "body_markdown": "B", "competitor_id": cid, "tags": ["x"]},
        headers=headers,
    ).json()

    client.put(
        f"/workspaces/{wid}/competitors/{cid}/profile/",
        json={"industry": "SaaS"},
        headers=headers,
    )

    client.put(
        f"/workspaces/{wid}/integrations/",
        json={"provider": "slack", "config": {"webhook_url": "https://hooks.example.com/x"}, "enabled": True},
        headers=headers,
    )

    members = client.get(f"/workspaces/{wid}/members", headers=headers).json()
    member_id = members[0]["id"]

    return {
        "headers": headers,
        "workspace_id": wid,
        "competitor_id": cid,
        "surface_id": sid,
        "change_log_id": change_log_id,
        "briefing_id": briefing["id"],
        "approval_item_id": approval["id"],
        "battlecard_update_id": battlecard_update["id"],
        "response_item_id": response_item["id"],
        "member_id": member_id,
    }


def _two_workspaces(client, monkeypatch):
    a = _seed_full_workspace(client, monkeypatch, "a-owner@example.com", "A Co")
    b = _seed_full_workspace(client, monkeypatch, "b-owner@example.com", "B Co")
    return a, b


def test_competitor_delete_across_workspace_is_404(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    res = client.delete(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}", headers=b["headers"]
    )
    assert res.status_code == 404


def test_surface_endpoints_reject_foreign_competitor_id(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)

    list_res = client.get(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}/surfaces/",
        headers=b["headers"],
    )
    assert list_res.status_code == 404

    check_res = client.post(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}"
        f"/surfaces/{a['surface_id']}/check",
        headers=b["headers"],
    )
    assert check_res.status_code == 404

    delete_res = client.delete(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}"
        f"/surfaces/{a['surface_id']}",
        headers=b["headers"],
    )
    assert delete_res.status_code == 404


def test_surface_check_runs_reject_foreign_surface_within_own_competitor(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)

    # b's own competitor/workspace, but a's surface_id — the surface belongs
    # to a different competitor entirely, so this must still 404.
    res = client.get(
        f"/workspaces/{b['workspace_id']}/competitors/{b['competitor_id']}"
        f"/surfaces/{a['surface_id']}/check-runs",
        headers=b["headers"],
    )
    assert res.status_code == 404


def test_change_logs_list_does_not_leak_other_workspace(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)

    logs = client.get(f"/workspaces/{b['workspace_id']}/change-logs/", headers=b["headers"]).json()
    ids = [log["id"] for log in logs]
    assert a["change_log_id"] not in ids


def test_briefing_get_across_workspace_is_404(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    res = client.get(
        f"/workspaces/{b['workspace_id']}/briefings/{a['briefing_id']}", headers=b["headers"]
    )
    assert res.status_code == 404


def test_briefings_list_does_not_leak_other_workspace(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    briefings = client.get(f"/workspaces/{b['workspace_id']}/briefings/", headers=b["headers"]).json()
    ids = [briefing["id"] for briefing in briefings]
    assert a["briefing_id"] not in ids


def test_approval_decide_across_workspace_is_404(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    res = client.post(
        f"/workspaces/{b['workspace_id']}/approvals/{a['approval_item_id']}/approve",
        json={},
        headers=b["headers"],
    )
    assert res.status_code == 404


def test_approvals_list_does_not_leak_other_workspace(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    approvals = client.get(f"/workspaces/{b['workspace_id']}/approvals/", headers=b["headers"]).json()
    ids = [item["id"] for item in approvals]
    assert a["approval_item_id"] not in ids


def test_battlecard_endpoints_reject_foreign_competitor_id(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)

    get_res = client.get(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}/battlecard/",
        headers=b["headers"],
    )
    assert get_res.status_code == 404

    propose_res = client.post(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}/battlecard/updates",
        json={"change_log_ids": [a["change_log_id"]]},
        headers=b["headers"],
    )
    assert propose_res.status_code == 404


def test_response_library_update_and_delete_across_workspace_is_404(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)

    update_res = client.patch(
        f"/workspaces/{b['workspace_id']}/response-library/{a['response_item_id']}",
        json={"title": "hijacked"},
        headers=b["headers"],
    )
    assert update_res.status_code == 404

    delete_res = client.delete(
        f"/workspaces/{b['workspace_id']}/response-library/{a['response_item_id']}",
        headers=b["headers"],
    )
    assert delete_res.status_code == 404


def test_response_library_list_does_not_leak_other_workspace(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    items = client.get(f"/workspaces/{b['workspace_id']}/response-library/", headers=b["headers"]).json()
    ids = [item["id"] for item in items]
    assert a["response_item_id"] not in ids


def test_company_profile_endpoints_reject_foreign_competitor_id(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)

    get_res = client.get(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}/profile/",
        headers=b["headers"],
    )
    assert get_res.status_code == 404

    put_res = client.put(
        f"/workspaces/{b['workspace_id']}/competitors/{a['competitor_id']}/profile/",
        json={"industry": "hijacked"},
        headers=b["headers"],
    )
    assert put_res.status_code == 404


def test_insights_similar_changes_rejects_foreign_change_log(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    res = client.get(
        f"/workspaces/{b['workspace_id']}/insights/change-logs/{a['change_log_id']}/similar",
        headers=b["headers"],
    )
    assert res.status_code == 404


def test_member_role_change_and_removal_across_workspace_is_404(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)

    patch_res = client.patch(
        f"/workspaces/{b['workspace_id']}/members/{a['member_id']}",
        json={"role": "owner"},
        headers=b["headers"],
    )
    assert patch_res.status_code == 404

    delete_res = client.delete(
        f"/workspaces/{b['workspace_id']}/members/{a['member_id']}", headers=b["headers"]
    )
    assert delete_res.status_code == 404


def test_audit_log_does_not_leak_other_workspace(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    audit = client.get(f"/workspaces/{b['workspace_id']}/audit-log/", headers=b["headers"]).json()
    # b's own approval decision hasn't happened yet in this test, so b's log
    # should be empty — proving a's approval.approved entry didn't leak in.
    assert audit == []


def test_export_pdf_rejects_foreign_briefing_id(client, monkeypatch):
    a, b = _two_workspaces(client, monkeypatch)
    res = client.get(
        f"/workspaces/{b['workspace_id']}/exports/briefings/{a['briefing_id']}.pdf",
        headers=b["headers"],
    )
    assert res.status_code == 404


def test_non_member_cannot_reach_workspace_at_all(client, monkeypatch):
    """Baseline sanity check underlying every case above: a user with no
    membership in a workspace gets 404 on its root resource listing,
    regardless of which sub-resource endpoint they try.
    """
    a, b = _two_workspaces(client, monkeypatch)

    res = client.get(f"/workspaces/{a['workspace_id']}/competitors/", headers=b["headers"])
    assert res.status_code == 404
