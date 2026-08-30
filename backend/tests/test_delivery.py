import app.services.delivery.slack_connector as slack_connector
import app.services.delivery.email_connector as email_connector
import app.routers.briefings as briefings_router
import app.services.briefing_service as briefing_service
from app.services.delivery.base import DeliveryPayload
from app.services.delivery.slack_connector import SlackConnector
from app.services.delivery.email_connector import EmailConnector
from app.services.llm.client import LLMCallResult
from app.services.briefing_service import BriefingDraft
from app.models.briefing import BriefingStatus


# ---- Connector unit tests ----

class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}")


def test_slack_connector_success(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(slack_connector.requests, "post", fake_post)

    result = SlackConnector().send(
        {"webhook_url": "https://hooks.slack.test/abc"},
        DeliveryPayload(title="Hello", body_markdown="World"),
    )
    assert result.success is True
    assert captured["url"] == "https://hooks.slack.test/abc"
    assert "Hello" in captured["json"]["text"]


def test_slack_connector_missing_webhook_url():
    result = SlackConnector().send({}, DeliveryPayload(title="x", body_markdown="y"))
    assert result.success is False
    assert "webhook_url" in result.detail


def test_slack_connector_http_failure(monkeypatch):
    monkeypatch.setattr(slack_connector.requests, "post", lambda url, json, timeout: _FakeResponse(500))

    result = SlackConnector().send(
        {"webhook_url": "https://hooks.slack.test/abc"},
        DeliveryPayload(title="x", body_markdown="y"),
    )
    assert result.success is False


def test_email_connector_missing_smtp_host(monkeypatch):
    monkeypatch.setattr(email_connector.settings, "smtp_host", None)

    result = EmailConnector().send(
        {"to_email": "sales@example.com"}, DeliveryPayload(title="x", body_markdown="y")
    )
    assert result.success is False
    assert "SMTP" in result.detail


def test_email_connector_success(monkeypatch):
    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(email_connector.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(email_connector.settings, "smtp_port", 587)
    monkeypatch.setattr(email_connector.settings, "smtp_user", "user")
    monkeypatch.setattr(email_connector.settings, "smtp_password", "pass")
    monkeypatch.setattr(email_connector.smtplib, "SMTP", _FakeSMTP)

    result = EmailConnector().send(
        {"to_email": "sales@example.com"}, DeliveryPayload(title="Subject", body_markdown="Body")
    )
    assert result.success is True
    assert sent["login"] == ("user", "pass")
    assert sent["message"]["To"] == "sales@example.com"


# ---- End-to-end approval -> delivery tests ----

class _FakeBriefingLLMClient:
    def complete(self, system, user, response_model):
        assert response_model is BriefingDraft
        return LLMCallResult(
            value=BriefingDraft(title="Pricing move", body_markdown="Body text"),
            model="fake-model", prompt_tokens=10, completion_tokens=5,
        )

    def embed(self, texts):
        raise NotImplementedError


def _patch_briefing_llm(monkeypatch):
    """The briefing is generated inside the queued job (run_briefing_job),
    which resolves its client from app.services.briefing_service — the
    router's own get_llm_client only answers "is an LLM configured at all"
    for the 400. Patching the router alone leaves the job calling the real
    provider, so both names have to point at the fake.
    """

    client = _FakeBriefingLLMClient()
    monkeypatch.setattr(briefings_router, "get_llm_client", lambda: client)
    monkeypatch.setattr(briefing_service, "get_llm_client", lambda: client)


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _seed_workspace_with_scored_change(client, owner_headers, monkeypatch):
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=owner_headers).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival"}, headers=owner_headers
    ).json()
    surface = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
        json={"surface_type": "pricing", "url": "https://rival.example.com", "check_frequency": "daily"},
        headers=owner_headers,
    ).json()

    import app.services.check_service as check_service

    class _ScoringClient:
        def complete(self, system, user, response_model):
            from app.services.llm.scoring import MaterialityResult
            return LLMCallResult(
                value=MaterialityResult(score=85, classification="pricing_move", rationale="Price hike."),
                model="fake-model", prompt_tokens=10, completion_tokens=5,
            )

        def embed(self, texts):
            from app.services.llm.client import EmbedResult
            return EmbedResult(vectors=[[0.1, 0.2]], model="fake-embed", prompt_tokens=2)

    check_url = f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/{surface['id']}/check"
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=owner_headers)
    monkeypatch.setattr(check_service, "get_llm_client", lambda: _ScoringClient())
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $15")
    check_res = client.post(check_url, headers=owner_headers).json()

    return workspace, check_res["change_log_id"]


def _configure_slack(client, workspace_id, owner_headers, monkeypatch):
    posted = {"count": 0}

    def fake_post(url, json, timeout):
        posted["count"] += 1
        posted["last_json"] = json
        return _FakeResponse(200)

    monkeypatch.setattr(slack_connector.requests, "post", fake_post)

    client.put(
        f"/workspaces/{workspace_id}/integrations/",
        json={"provider": "slack", "config": {"webhook_url": "https://hooks.slack.test/x"}, "enabled": True},
        headers=owner_headers,
    )
    return posted


def test_urgent_briefing_delivers_immediately_on_approval(client, monkeypatch):
    owner_headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)
    posted = _configure_slack(client, workspace["id"], owner_headers, monkeypatch)

    _patch_briefing_llm(monkeypatch)
    briefing = client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"audience": "all", "digest_type": "urgent", "change_log_ids": [change_log_id]},
        headers=owner_headers,
    ).json()
    approval = client.get(
        f"/workspaces/{workspace['id']}/approvals/", headers=owner_headers
    ).json()[0]

    client.post(
        f"/workspaces/{workspace['id']}/approvals/{approval['id']}/approve",
        json={}, headers=owner_headers,
    )

    assert posted["count"] == 1
    assert "Pricing move" in posted["last_json"]["text"]

    briefing_after = client.get(
        f"/workspaces/{workspace['id']}/briefings/{briefing['id']}", headers=owner_headers
    ).json()
    assert briefing_after["status"] == "delivered"
    assert briefing_after["delivered_at"] is not None


def test_daily_briefing_waits_for_digest_not_delivered_on_approval(client, monkeypatch):
    owner_headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)
    posted = _configure_slack(client, workspace["id"], owner_headers, monkeypatch)

    _patch_briefing_llm(monkeypatch)
    briefing = client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"audience": "all", "digest_type": "daily", "change_log_ids": [change_log_id]},
        headers=owner_headers,
    ).json()
    approval = client.get(
        f"/workspaces/{workspace['id']}/approvals/", headers=owner_headers
    ).json()[0]

    client.post(
        f"/workspaces/{workspace['id']}/approvals/{approval['id']}/approve",
        json={}, headers=owner_headers,
    )

    # Approval alone must not trigger delivery for a non-urgent digest_type.
    assert posted["count"] == 0

    briefing_after = client.get(
        f"/workspaces/{workspace['id']}/briefings/{briefing['id']}", headers=owner_headers
    ).json()
    assert briefing_after["status"] == "approved"
    assert briefing_after["delivered_at"] is None


def test_deliver_digest_bundles_and_marks_delivered(client, monkeypatch, db_session):
    owner_headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)
    posted = _configure_slack(client, workspace["id"], owner_headers, monkeypatch)

    _patch_briefing_llm(monkeypatch)
    briefing = client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"audience": "all", "digest_type": "daily", "change_log_ids": [change_log_id]},
        headers=owner_headers,
    ).json()
    approval = client.get(
        f"/workspaces/{workspace['id']}/approvals/", headers=owner_headers
    ).json()[0]
    client.post(
        f"/workspaces/{workspace['id']}/approvals/{approval['id']}/approve", json={}, headers=owner_headers
    )
    assert posted["count"] == 0

    from app.services.delivery.delivery_service import deliver_digest
    from app.models.briefing import BriefingDigestType

    deliver_digest(db_session, workspace["id"], BriefingDigestType.daily)

    assert posted["count"] == 1

    briefing_after = client.get(
        f"/workspaces/{workspace['id']}/briefings/{briefing['id']}", headers=owner_headers
    ).json()
    assert briefing_after["status"] == "delivered"


def test_approval_without_any_integration_leaves_briefing_approved(client, monkeypatch):
    owner_headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)
    _patch_briefing_llm(monkeypatch)

    briefing = client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"audience": "all", "digest_type": "urgent", "change_log_ids": [change_log_id]},
        headers=owner_headers,
    ).json()
    approval = client.get(
        f"/workspaces/{workspace['id']}/approvals/", headers=owner_headers
    ).json()[0]

    approve_res = client.post(
        f"/workspaces/{workspace['id']}/approvals/{approval['id']}/approve",
        json={}, headers=owner_headers,
    )
    assert approve_res.status_code == 200

    briefing_after = client.get(
        f"/workspaces/{workspace['id']}/briefings/{briefing['id']}", headers=owner_headers
    ).json()
    assert briefing_after["status"] == "approved"


def test_approval_succeeds_even_if_delivery_raises_unexpectedly(client, monkeypatch):
    """Regression test: an unexpected exception from delivery (a flaky
    connector, a DB hiccup) must not surface as a 500 on an approval that
    already succeeded and was durably committed — and must not leave the
    approval item stuck pending on a client retry.
    """
    import app.services.approval_service as approval_service

    owner_headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)
    _patch_briefing_llm(monkeypatch)

    client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"audience": "all", "digest_type": "urgent", "change_log_ids": [change_log_id]},
        headers=owner_headers,
    )
    approval = client.get(
        f"/workspaces/{workspace['id']}/approvals/", headers=owner_headers
    ).json()[0]

    def _raise(db, approval_item):
        raise RuntimeError("boom — simulated delivery failure")

    monkeypatch.setattr(approval_service, "deliver_briefing", _raise)

    approve_res = client.post(
        f"/workspaces/{workspace['id']}/approvals/{approval['id']}/approve",
        json={}, headers=owner_headers,
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "approved"

    # A client retry after the (would-be) error must see it as already
    # decided, not stuck pending.
    retry_res = client.post(
        f"/workspaces/{workspace['id']}/approvals/{approval['id']}/approve",
        json={}, headers=owner_headers,
    )
    assert retry_res.status_code == 400


# ---- Integrations router tests ----

def test_editor_cannot_manage_integrations(client, monkeypatch):
    owner_headers = _register_login(client, "owner@example.com")
    editor_headers = _register_login(client, "editor@example.com")
    workspace, _ = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)

    client.post(
        f"/workspaces/{workspace['id']}/members",
        json={"email": "editor@example.com", "role": "editor"},
        headers=owner_headers,
    )

    res = client.put(
        f"/workspaces/{workspace['id']}/integrations/",
        json={"provider": "slack", "config": {"webhook_url": "https://x"}, "enabled": True},
        headers=editor_headers,
    )
    assert res.status_code == 403


def test_integration_test_send_endpoint(client, monkeypatch):
    owner_headers = _register_login(client, "owner@example.com")
    workspace, _ = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)
    posted = _configure_slack(client, workspace["id"], owner_headers, monkeypatch)

    res = client.post(
        f"/workspaces/{workspace['id']}/integrations/slack/test-send", headers=owner_headers
    )
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert posted["count"] == 1


def test_delete_integration(client, monkeypatch):
    owner_headers = _register_login(client, "owner@example.com")
    workspace, _ = _seed_workspace_with_scored_change(client, owner_headers, monkeypatch)
    _configure_slack(client, workspace["id"], owner_headers, monkeypatch)

    delete_res = client.delete(
        f"/workspaces/{workspace['id']}/integrations/slack", headers=owner_headers
    )
    assert delete_res.status_code == 200

    listed = client.get(f"/workspaces/{workspace['id']}/integrations/", headers=owner_headers).json()
    assert listed == []
