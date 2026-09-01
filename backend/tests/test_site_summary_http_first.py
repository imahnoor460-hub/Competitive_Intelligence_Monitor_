"""HTTP-first page text and the automatic site-summary fan-out cap.

site_summary_service used to launch a browser for every active surface on
every check that found new content. Measured on a real storefront that is
~596MB and ~22s per launch, against a 512MB container — so the order is now
HTTP, then browser (only when the HTTP body looks JavaScript-empty, and only
when ENABLE_BROWSER_RENDERING is on), then the stored snapshot.
"""
import pytest

import app.services.check_service as check_service
import app.services.site_summary_service as site_summary_service
from app.models.competitor import Competitor
from app.models.snapshot import Snapshot
from app.models.surface import Surface, SurfaceType
from app.models.user import User
from app.models.workspace import Workspace
from app.services.rendered_content_service import RenderedContentError
from app.services.site_summary_service import _latest_pages, _page_text
from app.services.snapshot import FetchError

LONG = "Ready to wear, unstitched, sale 40% off. " * 40
SHORT = "Enable JavaScript"


@pytest.fixture()
def competitor(db_session):
    owner = User(email="owner@example.com", hashed_password="x", full_name="Owner")
    workspace = Workspace(name="Acme", slug="acme")
    db_session.add_all([owner, workspace])
    db_session.flush()
    rival = Competitor(
        name="Rival", workspace_id=workspace.id, created_by_user_id=owner.id
    )
    db_session.add(rival)
    db_session.flush()
    db_session.commit()
    return rival


def _add_surface(db_session, competitor, url, snapshot_text=None):
    surface = Surface(
        competitor_id=competitor.id, surface_type=SurfaceType.other, url=url
    )
    db_session.add(surface)
    db_session.flush()
    if snapshot_text is not None:
        db_session.add(Snapshot(surface_id=surface.id, text_content=snapshot_text))
    db_session.commit()
    return surface


def test_long_http_text_is_used_and_no_browser_is_launched(db_session, monkeypatch):
    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", lambda url: LONG)
    monkeypatch.setattr(
        site_summary_service,
        "capture_rendered_text",
        lambda url: pytest.fail("browser used despite usable HTTP text"),
    )

    assert _page_text(db_session, 1, "https://rival.example.com") == LONG


def test_short_http_text_falls_back_to_the_browser_when_enabled(db_session, monkeypatch):
    """The Bareeze failure mode: a successful but JavaScript-empty fetch must
    not silently replace a good summary with an empty one.
    """
    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", lambda url: SHORT)
    monkeypatch.setattr(site_summary_service, "capture_rendered_text", lambda url: LONG)
    monkeypatch.setattr("app.core.config.settings.enable_browser_rendering", True)

    assert _page_text(db_session, 1, "https://rival.example.com") == LONG


def test_short_http_text_is_kept_when_rendering_is_disabled(db_session, monkeypatch):
    """Free tier: no browser exists, so a short body is still better evidence
    than nothing and must not be thrown away.
    """
    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", lambda url: SHORT)
    monkeypatch.setattr(
        site_summary_service,
        "capture_rendered_text",
        lambda url: pytest.fail("browser used while rendering is disabled"),
    )

    assert _page_text(db_session, 1, "https://rival.example.com") == SHORT


def test_failed_http_falls_back_to_the_stored_snapshot(db_session, competitor, monkeypatch):
    surface = _add_surface(
        db_session, competitor, "https://rival.example.com", snapshot_text="stored text"
    )

    def _boom(url):
        raise FetchError("dns failure")

    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", _boom)

    assert _page_text(db_session, surface.id, surface.url) == "stored text"


def test_failed_render_falls_back_to_the_stored_snapshot(db_session, competitor, monkeypatch):
    surface = _add_surface(
        db_session, competitor, "https://rival.example.com", snapshot_text="stored text"
    )

    def _no_http(url):
        raise FetchError("dns failure")

    def _no_render(url):
        raise RenderedContentError("browser died")

    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", _no_http)
    monkeypatch.setattr(site_summary_service, "capture_rendered_text", _no_render)
    monkeypatch.setattr("app.core.config.settings.enable_browser_rendering", True)

    assert _page_text(db_session, surface.id, surface.url) == "stored text"


def test_nothing_available_returns_none(db_session, competitor, monkeypatch):
    surface = _add_surface(db_session, competitor, "https://rival.example.com")

    def _boom(url):
        raise FetchError("dns failure")

    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", _boom)

    assert _page_text(db_session, surface.id, surface.url) is None


def test_latest_pages_is_uncapped_by_default(db_session, competitor, monkeypatch):
    for i in range(12):
        _add_surface(db_session, competitor, f"https://rival.example.com/p{i}")
    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", lambda url: LONG)

    assert len(_latest_pages(db_session, competitor.id)) == 12


def test_max_pages_caps_the_fan_out(db_session, competitor, monkeypatch):
    for i in range(12):
        _add_surface(db_session, competitor, f"https://rival.example.com/p{i}")
    fetched: list[str] = []

    def _record(url):
        fetched.append(url)
        return LONG

    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", _record)

    pages = _latest_pages(db_session, competitor.id, max_pages=3)

    assert len(pages) == 3
    # The cap bounds the work done, not just the result handed to the LLM.
    assert len(fetched) == 3


def test_priority_surface_is_fetched_first(db_session, competitor, monkeypatch):
    surfaces = [
        _add_surface(db_session, competitor, f"https://rival.example.com/p{i}")
        for i in range(6)
    ]
    last = surfaces[-1]
    fetched: list[str] = []

    def _record(url):
        fetched.append(url)
        return LONG

    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", _record)

    _latest_pages(db_session, competitor.id, max_pages=2, priority_surface_id=last.id)

    # Without the reordering this surface is 6th and falls outside a cap of 2,
    # so the page that actually changed would never reach the summary.
    assert fetched[0] == last.url


def test_apply_site_summary_caps_the_fan_out(client, db_session, monkeypatch):
    """Regression: one check must not fetch every surface of the competitor.

    Exercised through the real check endpoint so the cap is verified on the
    path that actually fires it, not just on _latest_pages directly.
    """
    headers = _register_login(client, "owner@example.com")
    workspace_id = client.post("/workspaces/", json={"name": "Acme"}, headers=headers).json()["id"]
    competitor_id = client.post(
        f"/workspaces/{workspace_id}/competitors/", json={"name": "Rival"}, headers=headers
    ).json()["id"]

    surface_ids = []
    for i in range(20):
        surface_ids.append(
            client.post(
                f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/",
                json={
                    "surface_type": "other",
                    "url": f"https://rival.example.com/p{i}",
                    "check_frequency": "daily",
                },
                headers=headers,
            ).json()["id"]
        )

    fetched: list[str] = []

    def _record(url):
        fetched.append(url)
        return LONG

    monkeypatch.setattr(site_summary_service, "capture_clean_snapshot", _record)
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "baseline text")
    monkeypatch.setattr(check_service, "get_llm_client", lambda: _FakeClient())

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/surfaces/{surface_ids[0]}/check",
        headers=headers,
    )
    assert res.status_code == 200

    assert 0 < len(fetched) <= check_service._SITE_SUMMARY_AUTO_MAX_PAGES


def _register_login(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class _FakeClient:
    def complete(self, system, user, response_model):
        from app.services.llm.client import LLMCallResult
        from app.services.llm.scoring import MaterialityResult

        if response_model is MaterialityResult:
            return LLMCallResult(
                value=MaterialityResult(score=70, classification="pricing_move", rationale="x"),
                model="fake-model", prompt_tokens=10, completion_tokens=5,
            )
        return LLMCallResult(
            value=site_summary_service.SiteSummaryDraft(
                categories=["Women's"], current_offers=["Sale"]
            ),
            model="fake-model", prompt_tokens=10, completion_tokens=5,
        )

    def embed(self, texts):
        from app.services.llm.client import EmbedResult
        return EmbedResult(vectors=[[0.1, 0.2]], model="fake-embed", prompt_tokens=2)
