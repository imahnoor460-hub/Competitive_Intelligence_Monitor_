from app.models.approval_item import ApprovalItem
from app.models.battlecard import Battlecard
from app.models.battlecard_update import BattlecardUpdate
from app.models.battlecard_update_job import BattlecardUpdateJob
from app.models.change_embedding import ChangeEmbedding
from app.models.change_log import ChangeLog
from app.models.check_run import CheckRun
from app.models.company_profile import CompanyProfile
from app.models.competitor import Competitor
from app.models.competitor_discovery_job import CompetitorDiscoveryJob
from app.models.competitor_site_summary import CompetitorSiteSummary
from app.models.response_library import ResponseLibraryItem
from app.models.snapshot import Snapshot
from app.models.surface import Surface, SurfaceType
from app.models.traffic_snapshot import TrafficSnapshot


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _ScoringClient:
    def complete(self, system, user, response_model):
        from app.services.llm.client import LLMCallResult
        from app.services.llm.scoring import MaterialityResult
        from app.services.battlecard_service import BattlecardDraft

        if response_model is MaterialityResult:
            return LLMCallResult(
                value=MaterialityResult(score=70, classification="pricing_move", rationale="x"),
                model="fake-model", prompt_tokens=10, completion_tokens=5,
            )
        return LLMCallResult(
            value=BattlecardDraft(
                change_summary="They cut prices", updated_content_markdown="# Battlecard"
            ),
            model="fake-model", prompt_tokens=10, completion_tokens=5,
        )

    def embed(self, texts):
        from app.services.llm.client import EmbedResult
        return EmbedResult(vectors=[[0.1, 0.2]], model="fake-embed", prompt_tokens=2)


class _FakeSiteSummaryClient:
    def complete(self, system, user, response_model):
        from app.services.llm.client import LLMCallResult
        from app.services.site_summary_service import SiteSummaryDraft
        return LLMCallResult(
            value=SiteSummaryDraft(categories=["Men's"], current_offers=["Sale"]),
            model="fake-model", prompt_tokens=10, completion_tokens=5,
        )

    def embed(self, texts):
        raise NotImplementedError


class _FakeTrafficResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"visits": [{"date": "2026-01-01", "visits": 1000.0}]}


def _seed_fully_loaded_competitor(client, monkeypatch, headers, workspace_id):
    """Populates every kind of dependent row a real competitor can have:
    surfaces, snapshots, check_runs, a scored+embedded change_log, a
    company_profile, a battlecard with an approved battlecard_update (and
    its approval_item and its battlecard_update_job), a site summary, a
    traffic snapshot, a competitor_discovery_job, and a response-library
    item — the full fan-out `delete_competitor` must clean up.

    The competitor is created *with* a website_url on purpose: that is what
    makes create_competitor insert a CompetitorDiscoveryJob, which is the
    row every real competitor has and which this seed previously omitted.
    """
    import app.services.check_service as check_service
    import app.services.competitor_discovery_service as discovery_service
    import app.routers.battlecards as battlecards_router
    import app.services.battlecard_service as battlecard_service
    import app.services.site_summary_service as site_summary_service
    import app.routers.site_summary as site_summary_router
    import app.services.traffic_service as traffic_service

    monkeypatch.setattr(
        discovery_service,
        "discover_surfaces",
        lambda url: [(SurfaceType.other, "Home", "https://rival.example.com/")],
    )
    competitor = client.post(
        f"/workspaces/{workspace_id}/competitors/",
        json={"name": "Rival", "website_url": "https://rival.example.com"},
        headers=headers,
    ).json()
    competitor_id = competitor["id"]
    assert competitor["discovery_job_id"] is not None

    surface = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/",
        json={"surface_type": "pricing", "url": "https://rival.example.com", "check_frequency": "daily"},
        headers=headers,
    ).json()
    check_url = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/{surface['id']}/check"
    )

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    client.post(check_url, headers=headers)
    monkeypatch.setattr(check_service, "get_llm_client", lambda: _ScoringClient())
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v2")
    changed = client.post(check_url, headers=headers).json()
    change_log_id = changed["change_log_id"]

    client.put(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/profile/",
        json={"industry": "Fashion", "website_domain": "rival.example.com"},
        headers=headers,
    )

    # Both names — the proposal is drafted inside the queued job, which
    # resolves its client from app.services.battlecard_service. Without the
    # job-side patch no BattlecardUpdate/ApprovalItem rows get created, and
    # the cascade assertions below would pass vacuously.
    bc_client = _ScoringClient()
    monkeypatch.setattr(battlecards_router, "get_llm_client", lambda: bc_client)
    monkeypatch.setattr(battlecard_service, "get_llm_client", lambda: bc_client)
    client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/battlecard/updates",
        json={"change_log_ids": [change_log_id]},
        headers=headers,
    )
    pending = client.get(f"/workspaces/{workspace_id}/approvals/?status=pending", headers=headers).json()
    approval_item_id = next(i["id"] for i in pending if i["item_type"] == "battlecard_update")
    client.post(
        f"/workspaces/{workspace_id}/approvals/{approval_item_id}/approve",
        json={},
        headers=headers,
    )

    monkeypatch.setattr(site_summary_service, "capture_rendered_text", lambda url: "Men's clothing on sale")
    monkeypatch.setattr(site_summary_router, "get_llm_client", lambda: _FakeSiteSummaryClient())
    client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary/refresh",
        headers=headers,
    )

    monkeypatch.setattr("app.core.config.settings.similarweb_api_key", "fake-key")
    monkeypatch.setattr(traffic_service.requests, "get", lambda *a, **k: _FakeTrafficResponse())
    client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/traffic/refresh",
        headers=headers,
    )

    client.post(
        f"/workspaces/{workspace_id}/response-library/",
        json={"competitor_id": competitor_id, "title": "Objection handling", "body_markdown": "..."},
        headers=headers,
    )

    return competitor_id


def test_delete_competitor_cleans_up_every_dependent_table(client, monkeypatch, db_session):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    workspace_id = workspace["id"]

    competitor_id = _seed_fully_loaded_competitor(client, monkeypatch, headers, workspace_id)

    response_item = client.get(f"/workspaces/{workspace_id}/response-library/", headers=headers).json()[0]
    assert response_item["competitor_id"] == competitor_id

    res = client.delete(f"/workspaces/{workspace_id}/competitors/{competitor_id}", headers=headers)
    assert res.status_code == 200

    db_session.expire_all()

    assert db_session.query(Competitor).filter(Competitor.id == competitor_id).first() is None
    assert db_session.query(Surface).filter(Surface.competitor_id == competitor_id).count() == 0
    assert db_session.query(ChangeLog).filter(ChangeLog.competitor_id == competitor_id).count() == 0
    assert db_session.query(Snapshot).count() == 0
    assert db_session.query(CheckRun).count() == 0
    assert db_session.query(ChangeEmbedding).count() == 0
    assert db_session.query(CompanyProfile).filter(
        CompanyProfile.competitor_id == competitor_id
    ).first() is None
    assert db_session.query(CompetitorSiteSummary).filter(
        CompetitorSiteSummary.competitor_id == competitor_id
    ).first() is None
    assert db_session.query(TrafficSnapshot).filter(
        TrafficSnapshot.competitor_id == competitor_id
    ).count() == 0

    battlecard_ids = [
        row[0] for row in db_session.query(Battlecard.id).filter(
            Battlecard.competitor_id == competitor_id
        ).all()
    ]
    assert not battlecard_ids
    assert db_session.query(BattlecardUpdate).count() == 0
    assert db_session.query(ApprovalItem).count() == 0

    # The two job tables this cascade used to miss. Both have an FK to
    # competitors.id, so on Postgres a surviving row here is a 500 on delete
    # rather than a stale row — see test_delete_competitor_clears_job_tables.
    assert db_session.query(CompetitorDiscoveryJob).filter(
        CompetitorDiscoveryJob.competitor_id == competitor_id
    ).count() == 0
    assert db_session.query(BattlecardUpdateJob).filter(
        BattlecardUpdateJob.competitor_id == competitor_id
    ).count() == 0

    # The manually-authored response-library item survives, just orphaned.
    survivors = client.get(f"/workspaces/{workspace_id}/response-library/", headers=headers).json()
    assert len(survivors) == 1
    assert survivors[0]["competitor_id"] is None


def test_delete_competitor_404_for_unknown_id(client):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()

    res = client.delete(f"/workspaces/{workspace['id']}/competitors/999999", headers=headers)
    assert res.status_code == 404


def test_delete_competitor_removes_from_list(client):
    headers = _register_login(client, "owner@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()

    client.delete(f"/workspaces/{workspace['id']}/competitors/{competitor['id']}", headers=headers)

    competitors = client.get(f"/workspaces/{workspace['id']}/competitors/", headers=headers).json()
    assert competitors == []


def test_reviewer_cannot_delete_competitor_but_editor_can(client):
    owner_headers = _register_login(client, "owner@example.com")
    _register_login(client, "reviewer@example.com")
    workspace = client.post("/workspaces/", json={"name": "Acme"}, headers=owner_headers).json()
    client.post(
        f"/workspaces/{workspace['id']}/members",
        json={"email": "reviewer@example.com", "role": "reviewer"},
        headers=owner_headers,
    )
    reviewer_headers = _register_login(client, "reviewer@example.com")
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/", json={"name": "Rival"}, headers=owner_headers
    ).json()

    forbidden = client.delete(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}", headers=reviewer_headers
    )
    assert forbidden.status_code == 403

    allowed = client.delete(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}", headers=owner_headers
    )
    assert allowed.status_code == 200


def test_delete_competitor_isolated_across_workspaces(client):
    a_headers = _register_login(client, "a@example.com")
    b_headers = _register_login(client, "b@example.com")
    workspace_a = client.post("/workspaces/", json={"name": "A Co"}, headers=a_headers).json()
    workspace_b = client.post("/workspaces/", json={"name": "B Co"}, headers=b_headers).json()
    competitor_a = client.post(
        f"/workspaces/{workspace_a['id']}/competitors/", json={"name": "Rival"}, headers=a_headers
    ).json()

    cross = client.delete(
        f"/workspaces/{workspace_b['id']}/competitors/{competitor_a['id']}", headers=b_headers
    )
    assert cross.status_code == 404

    still_there = client.get(f"/workspaces/{workspace_a['id']}/competitors/", headers=a_headers).json()
    assert len(still_there) == 1


def test_delete_competitor_clears_job_tables(client, monkeypatch, db_session):
    """Regression: competitor_discovery_jobs and battlecard_update_jobs both
    carry an FK to competitors.id and were not being cleaned up, so on
    Postgres every DELETE /competitors/{id} failed with a 500 —
    competitor_discovery_jobs first, since create_competitor inserts one for
    every competitor that has a website_url.

    battlecard_update_jobs is the subtler half: it also references
    battlecard_updates.id, so it has to go *before* the BattlecardUpdate
    rows the cascade deletes, not just before the competitor.

    The suite runs on SQLite, which does not enforce foreign keys, so this
    asserts the rows are actually gone rather than relying on the delete
    raising — an assertion that fails the same way on either backend.
    """
    import app.services.competitor_discovery_service as discovery_service

    monkeypatch.setattr(
        discovery_service,
        "discover_surfaces",
        lambda url: [(SurfaceType.other, "Home", "https://rival.example.com/")],
    )

    headers = _register_login(client, "owner@example.com")
    workspace_id = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()["id"]

    competitor_id = client.post(
        f"/workspaces/{workspace_id}/competitors/",
        json={"name": "Rival", "website_url": "https://rival.example.com"},
        headers=headers,
    ).json()["id"]

    # create_competitor inserts this unconditionally for a competitor with a
    # website_url — the row that made this a 100% reproducible failure.
    assert db_session.query(CompetitorDiscoveryJob).filter(
        CompetitorDiscoveryJob.competitor_id == competitor_id
    ).count() == 1

    # A battlecard update job pointing at a real BattlecardUpdate, so the
    # delete order between the two is exercised and not just the competitor FK.
    battlecard = Battlecard(
        workspace_id=workspace_id, competitor_id=competitor_id, title="Rival", content_markdown="#"
    )
    db_session.add(battlecard)
    db_session.flush()
    update = BattlecardUpdate(
        workspace_id=workspace_id, battlecard_id=battlecard.id, proposed_content_markdown="# new"
    )
    db_session.add(update)
    db_session.flush()
    db_session.add(
        BattlecardUpdateJob(
            workspace_id=workspace_id,
            competitor_id=competitor_id,
            change_log_ids=[],
            battlecard_update_id=update.id,
        )
    )
    db_session.commit()

    res = client.delete(f"/workspaces/{workspace_id}/competitors/{competitor_id}", headers=headers)
    assert res.status_code == 200

    db_session.expire_all()

    assert db_session.query(Competitor).filter(Competitor.id == competitor_id).first() is None
    assert db_session.query(CompetitorDiscoveryJob).filter(
        CompetitorDiscoveryJob.competitor_id == competitor_id
    ).count() == 0
    assert db_session.query(BattlecardUpdateJob).filter(
        BattlecardUpdateJob.competitor_id == competitor_id
    ).count() == 0
    assert db_session.query(BattlecardUpdate).count() == 0
