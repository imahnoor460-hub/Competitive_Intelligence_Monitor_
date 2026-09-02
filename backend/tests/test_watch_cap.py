"""The per-competitor watch cap, and the bounds that keep one bad page from
stalling a sweep.

Run Check used to mean "every active surface in the workspace" — 282 of them
on a real install, which on 0.2 shared CPU is a sweep measured in tens of
minutes and a UI that looks stuck. These cover the three things that changed:
what a sweep picks up, what discovery leaves active, and what happens when a
single fetch never finishes.
"""

import time

import requests

import app.services.check_service as check_service
import app.services.competitor_discovery_service as discovery_scheduling
import app.services.competitor_discovery_service as discovery_service
from app.core.config import settings
from app.models.check_run import CheckRun
from app.models.surface import Surface, SurfaceType
from app.services.snapshot import fetch_html, FetchError
from app.services.surface_selection import partition_by_cap, surface_rank


def _register(client, email="cap@example.com"):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": "Cap"},
    )
    login = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _workspace_with_surfaces(client, headers, monkeypatch, count):
    workspace = client.post(
        "/workspaces/", json={"name": "Acme"}, headers=headers
    ).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/",
        json={"name": "Rival"},
        headers=headers,
    ).json()

    for i in range(count):
        client.post(
            f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
            json={
                "surface_type": "other",
                "url": f"https://rival.example.com/page-{i}",
                "check_frequency": "daily",
            },
            headers=headers,
        )

    monkeypatch.setattr(
        check_service, "capture_clean_snapshot", lambda url: f"content of {url}"
    )
    return workspace["id"], competitor["id"]


# --- the cap itself ---------------------------------------------------------

def test_check_all_is_capped_per_competitor(client, monkeypatch):
    """The failure this fixes: one click, 282 queued checks, an hour of work
    and a progress counter that looks frozen."""

    headers = _register(client)
    workspace_id, _competitor_id = _workspace_with_surfaces(
        client, headers, monkeypatch, count=25
    )

    res = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers)

    assert res.status_code == 202
    assert res.json()["total"] == settings.max_active_surfaces_per_competitor


def test_check_all_covers_every_competitor_up_to_the_cap(client, monkeypatch):
    """Capping per competitor, not per workspace — two competitors with a few
    pages each must both be swept in full."""

    headers = _register(client)
    workspace = client.post(
        "/workspaces/", json={"name": "Acme"}, headers=headers
    ).json()

    for name in ("Rival", "Other"):
        competitor = client.post(
            f"/workspaces/{workspace['id']}/competitors/",
            json={"name": name},
            headers=headers,
        ).json()
        for i in range(3):
            client.post(
                f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
                json={
                    "surface_type": "other",
                    "url": f"https://{name.lower()}.example.com/p{i}",
                    "check_frequency": "daily",
                },
                headers=headers,
            )

    monkeypatch.setattr(
        check_service, "capture_clean_snapshot", lambda url: f"content of {url}"
    )

    res = client.post(f"/workspaces/{workspace['id']}/check-all", headers=headers)

    assert res.json()["total"] == 6


def test_ranking_keeps_the_homepage_and_the_typed_pages():
    surfaces = [
        Surface(id=1, competitor_id=1, surface_type=SurfaceType.other,
                url="https://rival.example.com/collections/sale-1"),
        Surface(id=2, competitor_id=1, surface_type=SurfaceType.pricing,
                url="https://rival.example.com/pricing"),
        Surface(id=3, competitor_id=1, surface_type=SurfaceType.other,
                url="https://rival.example.com/"),
        Surface(id=4, competitor_id=1, surface_type=SurfaceType.blog,
                url="https://rival.example.com/blog"),
    ]

    watched, unwatched = partition_by_cap(surfaces, limit=3)

    assert [s.id for s in watched] == [3, 2, 4]
    assert [s.id for s in unwatched] == [1]


def test_ranking_is_total_and_stable():
    """Two surfaces alike in every ranked dimension still order by id — the
    scheduler, the sweep and the cleanup migration must agree on the same set
    every time they are asked."""

    a = Surface(id=7, competitor_id=1, surface_type=SurfaceType.other,
                url="https://rival.example.com/a")
    b = Surface(id=8, competitor_id=1, surface_type=SurfaceType.other,
                url="https://rival.example.com/b")

    assert surface_rank(a) < surface_rank(b)


# --- discovery leaves only the cap active -----------------------------------

def test_discovery_deactivates_everything_past_the_cap(client, monkeypatch, db_session):
    headers = _register(client)
    workspace = client.post(
        "/workspaces/", json={"name": "Acme"}, headers=headers
    ).json()

    found = [
        (SurfaceType.other, f"Page {i}", f"https://rival.example.com/page-{i}")
        for i in range(30)
    ]
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: found)

    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/",
        json={"name": "Rival", "website_url": "https://rival.example.com"},
        headers=headers,
    ).json()

    surfaces = (
        db_session.query(Surface)
        .filter(Surface.competitor_id == competitor["id"])
        .all()
    )
    active = [s for s in surfaces if s.is_active]

    # Everything discovered is still stored — nothing is thrown away.
    assert len(surfaces) == 30
    assert len(active) == settings.max_active_surfaces_per_competitor


def test_discovering_more_pages_cannot_creep_past_the_cap(
    client, monkeypatch, db_session
):
    """Discover-more-pages run twice used to add another batch each time. The
    cap is applied over the competitor's whole set, not just the new rows, so
    a second pass cannot push the watched count up."""

    headers = _register(client)
    workspace = client.post(
        "/workspaces/", json={"name": "Acme"}, headers=headers
    ).json()

    first = [
        (SurfaceType.other, f"Page {i}", f"https://rival.example.com/page-{i}")
        for i in range(8)
    ]
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: first)

    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/",
        json={"name": "Rival", "website_url": "https://rival.example.com"},
        headers=headers,
    ).json()

    second = first + [
        (SurfaceType.other, f"Later {i}", f"https://rival.example.com/later-{i}")
        for i in range(12)
    ]
    monkeypatch.setattr(discovery_service, "discover_surfaces", lambda url: second)

    res = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/discover",
        headers=headers,
    )
    assert res.status_code == 202

    active = (
        db_session.query(Surface)
        .filter(Surface.competitor_id == competitor["id"], Surface.is_active.is_(True))
        .count()
    )
    assert active == settings.max_active_surfaces_per_competitor


def test_check_all_ignores_surfaces_discovery_left_inactive(
    client, monkeypatch, db_session
):
    headers = _register(client)
    workspace_id, competitor_id = _workspace_with_surfaces(
        client, headers, monkeypatch, count=4
    )

    surfaces = (
        db_session.query(Surface)
        .filter(Surface.competitor_id == competitor_id)
        .order_by(Surface.id)
        .all()
    )
    surfaces[0].is_active = False
    surfaces[1].is_active = False
    db_session.commit()

    res = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers)

    assert res.json()["total"] == 2


# --- one page can never hold up the rest ------------------------------------

def test_fetch_gives_up_on_a_server_that_dribbles_forever(monkeypatch):
    """The hang this exists for: requests' timeout is per socket read, so a
    server sending one byte inside every read window never trips it. Only a
    wall-clock deadline ends this."""

    class _EndlessResponse:
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            while True:
                # Just inside a read timeout, forever — exactly the shape a
                # socket-level timeout cannot see.
                time.sleep(0.01)
                yield b"x"

    monkeypatch.setattr(settings, "http_total_timeout", 0.05)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _EndlessResponse())

    started = time.monotonic()
    try:
        fetch_html("https://slow.example.com")
        raise AssertionError("expected FetchError")
    except FetchError as exc:
        assert "still receiving data" in str(exc)

    # Bounded, not merely eventually-terminating.
    assert time.monotonic() - started < 5


def test_fetch_refuses_a_body_larger_than_the_limit(monkeypatch):
    """A 512MB container cannot read an arbitrary third-party body into
    memory, and no real page is this size."""

    class _HugeResponse:
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            for _ in range(10):
                yield b"x" * 1024

    monkeypatch.setattr(settings, "http_max_bytes", 2048)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _HugeResponse())

    try:
        fetch_html("https://huge.example.com")
        raise AssertionError("expected FetchError")
    except FetchError as exc:
        assert "byte limit" in str(exc)


def test_a_failing_surface_does_not_stop_the_rest_of_the_sweep(
    client, monkeypatch
):
    """The whole point of per-surface CheckRuns: one page that times out is
    marked failed and the sweep carries on to the others."""

    headers = _register(client)
    workspace_id, _competitor_id = _workspace_with_surfaces(
        client, headers, monkeypatch, count=4
    )

    def _fetch(url):
        if url.endswith("page-1"):
            raise FetchError(f"Timed out fetching {url}")
        return f"content of {url}"

    monkeypatch.setattr(check_service, "capture_clean_snapshot", _fetch)

    res = client.post(f"/workspaces/{workspace_id}/check-all", headers=headers)
    sweep_id = res.json()["id"]

    final = client.get(
        f"/workspaces/{workspace_id}/check-sweeps/{sweep_id}", headers=headers
    ).json()

    # A partial failure is a finished sweep, not a failed one — three pages
    # were checked and the fourth is recorded as failed.
    assert final["status"] == "success"
    assert final["total"] == 4
    assert final["finished"] == 4
    assert final["failed_count"] == 1


# --- the cleanup migration's frozen copy of the ranking ---------------------

def _migration_module(filename="f1b6c30d9a77_0027_tighten_active_surface_cap.py"):
    """Loaded by path: a revision's filename is not an importable module name,
    and migrations are deliberately not on the import path.

    Defaults to the newest cap migration — `0027`, which tightened the cap to
    7. `0026` is still checked below for its ranking, since both revisions run
    in sequence on a fresh database."""

    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app" / "alembic" / "versions"
        / filename
    )
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Row:
    """The shape the migration reads out of raw SQL — a string surface_type,
    not the enum, which is what psycopg2 hands back for a native enum column."""

    def __init__(self, id, competitor_id, surface_type, url):
        self.id = id
        self.competitor_id = competitor_id
        self.surface_type = surface_type
        self.url = url


def test_the_migrations_rank_the_same_pages_the_app_would():
    """Both cap migrations carry a frozen copy of the ranking on purpose, so
    this pins them to the app at the point they were written. If they ever
    diverge, the rows a migration deactivated stop matching the ones the
    running app would have chosen — and the disagreement is silent."""

    migrations = [
        _migration_module(),
        _migration_module("d4a91c7b6e02_0026_cap_active_surfaces.py"),
    ]

    surfaces = [
        Surface(id=1, competitor_id=1, surface_type=SurfaceType.other,
                url="https://rival.example.com/collections/sale"),
        Surface(id=2, competitor_id=1, surface_type=SurfaceType.pricing,
                url="https://rival.example.com/pricing"),
        Surface(id=3, competitor_id=1, surface_type=SurfaceType.other,
                url="https://rival.example.com/"),
        Surface(id=4, competitor_id=1, surface_type=SurfaceType.blog,
                url="https://rival.example.com/blog"),
        Surface(id=5, competitor_id=1, surface_type=SurfaceType.jobs,
                url="https://rival.example.com/careers"),
    ]
    rows = [
        _Row(s.id, s.competitor_id, s.surface_type.value, s.url) for s in surfaces
    ]

    app_order = [s.id for s in sorted(surfaces, key=surface_rank)]

    for migration in migrations:
        assert [r.id for r in sorted(rows, key=migration._rank)] == app_order

    # Only the newest one has to match the setting: 0026 froze the cap it
    # applied (10), and 0027 lowered it to what the app watches today.
    assert migrations[0]._CAP == settings.max_active_surfaces_per_competitor


def test_the_migration_recognises_a_homepage_with_or_without_a_trailing_slash():
    migration = _migration_module()

    assert migration._is_homepage("https://rival.example.com")
    assert migration._is_homepage("https://rival.example.com/")
    assert not migration._is_homepage("https://rival.example.com/collections/sale")


# --- what a sweep actually picks up -----------------------------------------

def test_a_sweep_checks_the_root_page_and_the_next_ranked_ones(
    client, monkeypatch, db_session
):
    """The shape the product asks for: the primary/root page plus the
    highest-ranked pages after it, and nothing else."""

    headers = _register(client)
    workspace = client.post(
        "/workspaces/", json={"name": "Acme"}, headers=headers
    ).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/",
        json={"name": "Rival"},
        headers=headers,
    ).json()

    def _add(surface_type, url):
        return client.post(
            f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
            json={
                "surface_type": surface_type,
                "url": url,
                "check_frequency": "daily",
            },
            headers=headers,
        ).json()["id"]

    # Deliberately added last, so an unranked "first N by id" would miss it.
    tail_ids = [
        _add("other", f"https://rival.example.com/collections/c{i}")
        for i in range(12)
    ]
    root_id = _add("other", "https://rival.example.com/")
    pricing_id = _add("pricing", "https://rival.example.com/pricing")

    monkeypatch.setattr(
        check_service, "capture_clean_snapshot", lambda url: f"content of {url}"
    )

    res = client.post(f"/workspaces/{workspace['id']}/check-all", headers=headers)
    sweep_id = res.json()["id"]

    checked = {
        run.surface_id
        for run in db_session.query(CheckRun).filter(CheckRun.sweep_id == sweep_id).all()
    }

    assert len(checked) == settings.max_active_surfaces_per_competitor
    assert root_id in checked
    assert pricing_id in checked
    # The long tail is not swept — it fills only the remaining ranked slots.
    assert len(checked & set(tail_ids)) == (
        settings.max_active_surfaces_per_competitor - 2
    )


def test_the_tail_is_left_unscheduled_entirely(client, monkeypatch, db_session):
    """Past the cap means unwatched, not watched-less-often: nothing schedules
    those surfaces on any cadence, and no job picks them up."""

    scheduled: list[int] = []
    unscheduled: list[int] = []
    monkeypatch.setattr(
        discovery_scheduling, "schedule_surface",
        lambda surface: scheduled.append(surface.id),
    )
    monkeypatch.setattr(
        discovery_scheduling, "unschedule_surface",
        lambda surface_id: unscheduled.append(surface_id),
    )

    headers = _register(client)
    workspace = client.post(
        "/workspaces/", json={"name": "Acme"}, headers=headers
    ).json()

    found = [
        (SurfaceType.other, f"Page {i}", f"https://rival.example.com/page-{i}")
        for i in range(25)
    ]
    monkeypatch.setattr(discovery_scheduling, "discover_surfaces", lambda url: found)

    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/",
        json={"name": "Rival", "website_url": "https://rival.example.com"},
        headers=headers,
    ).json()

    cap = settings.max_active_surfaces_per_competitor
    inactive_ids = [
        s.id
        for s in db_session.query(Surface)
        .filter(
            Surface.competitor_id == competitor["id"],
            Surface.is_active.is_(False),
        )
        .all()
    ]

    assert len(scheduled) == cap
    assert len(inactive_ids) == 25 - cap
    # Every unwatched surface was explicitly taken off the scheduler, and none
    # of them was armed on any cadence.
    assert set(unscheduled) == set(inactive_ids)
    assert not set(scheduled) & set(inactive_ids)
