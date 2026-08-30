from app.services.llm.client import LLMCallResult


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

    import app.services.check_service as check_service
    check_url = f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/{surface['id']}/check"

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $10")
    client.post(check_url, headers=headers)
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "Plan A $15")
    check_res = client.post(check_url, headers=headers).json()

    return workspace, check_res["change_log_id"]


def test_export_change_logs_csv_contains_competitor_and_diff(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace, _ = _seed_workspace_with_change_log(client, headers, monkeypatch)

    res = client.get(f"/workspaces/{workspace['id']}/exports/change-logs.csv", headers=headers)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    body = res.content.decode("utf-8")
    assert "Rival" in body
    assert "Plan A" in body


def test_export_change_logs_docx_returns_valid_document(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace, _ = _seed_workspace_with_change_log(client, headers, monkeypatch)

    res = client.get(f"/workspaces/{workspace['id']}/exports/change-logs.docx", headers=headers)

    assert res.status_code == 200
    assert "wordprocessingml" in res.headers["content-type"]

    import io
    from docx import Document
    document = Document(io.BytesIO(res.content))
    full_text = "\n".join(p.text for p in document.paragraphs)
    assert "Rival" in full_text


def test_export_briefing_pdf(client, monkeypatch):
    import app.routers.briefings as briefings_router
    import app.services.briefing_service as briefing_service
    from app.services.briefing_service import BriefingDraft

    headers = _register_login(client, "owner@example.com")
    workspace, change_log_id = _seed_workspace_with_change_log(client, headers, monkeypatch)

    class _FakeBriefingLLMClient:
        def complete(self, system, user, response_model):
            return LLMCallResult(
                value=BriefingDraft(title="Rival raised prices", body_markdown="They went up 50%."),
                model="fake-model", prompt_tokens=10, completion_tokens=5,
            )

        def embed(self, texts):
            raise NotImplementedError

    # Both names — the briefing is generated inside the queued job, which
    # resolves its client from app.services.briefing_service, not the router.
    fake_llm = _FakeBriefingLLMClient()
    monkeypatch.setattr(briefings_router, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(briefing_service, "get_llm_client", lambda: fake_llm)
    briefing = client.post(
        f"/workspaces/{workspace['id']}/briefings/generate-now",
        json={"audience": "all", "digest_type": "urgent", "change_log_ids": [change_log_id]},
        headers=headers,
    ).json()

    res = client.get(
        f"/workspaces/{workspace['id']}/exports/briefings/{briefing['id']}.pdf", headers=headers
    )

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")


def test_export_pdf_404_for_missing_briefing(client, monkeypatch):
    headers = _register_login(client, "owner@example.com")
    workspace, _ = _seed_workspace_with_change_log(client, headers, monkeypatch)

    res = client.get(f"/workspaces/{workspace['id']}/exports/briefings/999999.pdf", headers=headers)
    assert res.status_code == 404


def test_exports_are_scoped_to_workspace(client, monkeypatch):
    headers_a = _register_login(client, "a@example.com")
    headers_b = _register_login(client, "b@example.com")
    workspace_a, _ = _seed_workspace_with_change_log(client, headers_a, monkeypatch)

    res = client.get(f"/workspaces/{workspace_a['id']}/exports/change-logs.csv", headers=headers_b)
    assert res.status_code == 404
