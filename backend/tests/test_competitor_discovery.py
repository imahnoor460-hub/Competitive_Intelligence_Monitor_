"""Covers the competitor-add flow after page discovery moved out of the
request and into a CompetitorDiscoveryJob (see
services/competitor_discovery_service.py)."""
import app.services.competitor_discovery_service as discovery_service
from app.models.surface import SurfaceType
from app.services.surface_discovery_service import SurfaceDiscoveryError


def _register_login_and_workspace(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    login_res = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

    workspace = client.post("/workspaces/", json={"name": "Acme PMM"}, headers=headers).json()
    return headers, workspace["id"]


_DISCOVERED = [
    (SurfaceType.other, "Home", "https://rival.example.com/"),
    (SurfaceType.pricing, "Pricing", "https://rival.example.com/pricing"),
    (SurfaceType.blog, "Blog", "https://rival.example.com/blog"),
]


def _add_competitor(client, headers, workspace_id, url="https://rival.example.com"):
    return client.post(
        f"/workspaces/{workspace_id}/competitors/",
        json={"name": "Rival", "website_url": url},
        headers=headers,
    )


def test_create_returns_immediately_with_a_queued_discovery_job(client, monkeypatch):
    # The request must not perform discovery itself. If it ever does again,
    # this stub is what the *background job* is supposed to call — so assert
    # on what the response body says rather than on the stub alone.
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: _DISCOVERED)

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    res = _add_competitor(client, headers, workspace_id)

    assert res.status_code == 200
    body = res.json()
    # Discovery is no longer finished by the time this returns, so the create
    # response reports 0 and hands back a job id to poll instead.
    assert body["surfaces_discovered"] == 0
    assert body["discovery_job_id"] is not None


def test_no_website_url_creates_no_discovery_job(client):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/",
        json={"name": "Rival"},
        headers=headers,
    )

    assert res.status_code == 200
    assert res.json()["discovery_job_id"] is None


def test_discovery_job_runs_in_background_and_creates_surfaces(client, monkeypatch):
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: _DISCOVERED)

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    competitor = _add_competitor(client, headers, workspace_id).json()

    # TestClient runs BackgroundTasks synchronously once the response is
    # delivered, so by here the job has already been through the full cycle.
    job = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}"
        f"/discovery-jobs/{competitor['discovery_job_id']}",
        headers=headers,
    ).json()

    assert job["status"] == "success"
    assert job["surfaces_discovered"] == 3
    assert job["error"] is None
    assert job["finished_at"] is not None

    surfaces = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}/surfaces/",
        headers=headers,
    ).json()

    assert len(surfaces) == 3
    # Discovery behavior is preserved: every page keeps its own detected type
    # and its nav link text, rather than being collapsed or dropped.
    assert [s["url"] for s in surfaces] == [u for _, _, u in _DISCOVERED]
    assert [s["name"] for s in surfaces] == [n for _, n, _ in _DISCOVERED]
    assert {s["surface_type"] for s in surfaces} == {"other", "pricing", "blog"}


def test_discovery_failure_marks_the_job_failed(client, monkeypatch):
    def _boom(url):
        raise SurfaceDiscoveryError("Failed to discover pages on https://rival.example.com")

    monkeypatch.setattr(discovery_service, "discover_surfaces", _boom)

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    # A failing discovery must not fail the add itself — the competitor is
    # still created, it just has no pages yet.
    res = _add_competitor(client, headers, workspace_id)
    assert res.status_code == 200
    competitor = res.json()

    job = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}"
        f"/discovery-jobs/{competitor['discovery_job_id']}",
        headers=headers,
    ).json()

    assert job["status"] == "failed"
    assert job["surfaces_discovered"] == 0
    assert "Failed to discover pages" in job["error"]
    assert job["finished_at"] is not None


def test_unexpected_discovery_error_still_resolves_the_job(client, monkeypatch):
    def _boom(url):
        raise RuntimeError("chromium is not installed")

    monkeypatch.setattr(discovery_service, "discover_surfaces", _boom)

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    competitor = _add_competitor(client, headers, workspace_id).json()

    job = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}"
        f"/discovery-jobs/{competitor['discovery_job_id']}",
        headers=headers,
    ).json()

    assert job["status"] == "failed"
    assert "RuntimeError" in job["error"]


def test_job_is_scoped_to_its_workspace_and_competitor(client, monkeypatch):
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: _DISCOVERED)

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    competitor = _add_competitor(client, headers, workspace_id).json()

    other = client.post(
        f"/workspaces/{workspace_id}/competitors/",
        json={"name": "Other"},
        headers=headers,
    ).json()

    res = client.get(
        f"/workspaces/{workspace_id}/competitors/{other['id']}"
        f"/discovery-jobs/{competitor['discovery_job_id']}",
        headers=headers,
    )
    assert res.status_code == 404


def test_surfaces_are_inserted_in_one_batch(client, db_session, monkeypatch):
    # 40 is _MAX_DISCOVERED — the realistic worst case for a storefront
    # mega-menu, and the shape that made this slow: the old per-surface
    # commit-and-refresh loop was ~80-120 sequential round trips to a pooled
    # Postgres in another region.
    from sqlalchemy import event

    many = [
        (SurfaceType.other, f"Page {i}", f"https://rival.example.com/p{i}")
        for i in range(40)
    ]
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: many)

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")

    statements = []
    engine = db_session.get_bind()

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        competitor = _add_competitor(client, headers, workspace_id).json()
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    inserts = [s for s in statements if "INSERT INTO surfaces" in s]
    surface_selects = [s for s in statements if "FROM surfaces" in s]

    # The old loop did commit-and-refresh per surface, so 40 pages meant 40
    # round-trip SELECTs reloading one row each. Two whole-batch SELECTs are
    # what this locks in — one reading the competitor's existing URLs to skip
    # pages already tracked, one reloading the inserted rows — and that holds
    # on every backend regardless of how many pages were discovered.
    assert len(surface_selects) == 2, (
        f"expected two whole-batch SELECTs, got {len(surface_selects)}"
    )

    # Every insert goes through a single add_all()/flush() rather than a
    # commit per surface. Whether that flush then collapses to one
    # multi-VALUES INSERT is a dialect capability this code doesn't control:
    # the ORM must match RETURNING rows back to objects in order, and SQLite
    # (what these tests run on) has no implicit sentinel to guarantee that, so
    # it emits one statement per row. PostgreSQL, which production runs, does
    # have one and collapses the whole batch into a single INSERT. So this
    # bounds rather than pins the count.
    assert 1 <= len(inserts) <= len(many), (
        f"expected inserts from a single batched flush, got {len(inserts)}"
    )

    surfaces = client.get(
        f"/workspaces/{workspace_id}/competitors/{competitor['id']}/surfaces/",
        headers=headers,
    ).json()
    assert len(surfaces) == 40


def test_background_job_uses_its_own_session_and_closes_it(client, db_session, monkeypatch):
    # The job runs outside the request lifecycle, so it must never borrow the
    # request-scoped session (which FastAPI closes before background tasks
    # run) and must close whatever it opens.
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: _DISCOVERED)

    original_session_local = discovery_service.SessionLocal
    opened = []
    closed = []

    def _tracking_session_local(*args, **kwargs):
        session = original_session_local(*args, **kwargs)
        real_close = session.close

        def _close():
            closed.append(session)
            real_close()

        session.close = _close
        opened.append(session)
        return session

    monkeypatch.setattr(discovery_service, "SessionLocal", _tracking_session_local)

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    _add_competitor(client, headers, workspace_id)

    assert len(opened) == 1, "the job should open exactly one session"
    job_session = opened[0]
    assert job_session is not db_session, "the job must not reuse the request session"
    assert closed == [job_session], "the job must close the session it opened"
    assert not job_session.in_transaction()
