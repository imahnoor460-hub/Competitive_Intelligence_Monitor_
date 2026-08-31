"""Workspace-wide latest-check-run endpoint — see routers/check_runs.py.

Replaces the dashboard's per-surface /check-runs fan-out, which was one HTTP
request and one request-scoped DB session per surface on every page load.
"""
import app.services.check_service as check_service


def _register_login_and_workspace(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": email.split("@")[0]},
    )
    login = client.post("/auth/login", json={"email": email, "password": "supersecret1"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    workspace = client.post("/workspaces/", json={"name": "Acme PMM"}, headers=headers).json()
    return headers, workspace["id"]


def _competitor(client, headers, workspace_id, name="Rival"):
    return client.post(
        f"/workspaces/{workspace_id}/competitors/", json={"name": name}, headers=headers
    ).json()


def _surface(client, headers, workspace_id, competitor_id, url):
    return client.post(
        f"/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces/",
        json={"surface_type": "pricing", "url": url, "check_frequency": "daily"},
        headers=headers,
    ).json()


def _check(client, headers, workspace_id, competitor_id, surface_id):
    path = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/surfaces/{surface_id}/check"
    )
    return client.post(path, headers=headers)


def _history(client, headers, workspace_id, competitor_id, surface_id):
    path = (
        f"/workspaces/{workspace_id}/competitors/{competitor_id}"
        f"/surfaces/{surface_id}/check-runs"
    )
    return client.get(path, headers=headers).json()


def _latest(client, headers, workspace_id):
    return client.get(
        f"/workspaces/{workspace_id}/check-runs/latest", headers=headers
    ).json()


def test_returns_exactly_one_run_per_surface(client, monkeypatch):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    competitor = _competitor(client, headers, workspace_id)
    comp_id = competitor["id"]
    a = _surface(client, headers, workspace_id, comp_id, "https://r.example.com/a")
    b = _surface(client, headers, workspace_id, comp_id, "https://r.example.com/b")

    # Three runs on surface a, one on b.
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    _check(client, headers, workspace_id, comp_id, a["id"])
    _check(client, headers, workspace_id, comp_id, b["id"])
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v2 changed")
    _check(client, headers, workspace_id, comp_id, a["id"])
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v3 again")
    _check(client, headers, workspace_id, comp_id, a["id"])

    latest = _latest(client, headers, workspace_id)

    assert len(latest) == 2
    assert sorted(r["surface_id"] for r in latest) == sorted([a["id"], b["id"]])

    # And it really is the newest run for the surface with history.
    history = _history(client, headers, workspace_id, comp_id, a["id"])
    assert len(history) == 3
    newest_id = max(run["id"] for run in history)
    assert next(r for r in latest if r["surface_id"] == a["id"])["id"] == newest_id


def test_returns_every_field_the_per_surface_endpoint_returns(client, monkeypatch):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    comp_id = _competitor(client, headers, workspace_id)["id"]
    surface = _surface(client, headers, workspace_id, comp_id, "https://r.example.com/a")

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    _check(client, headers, workspace_id, comp_id, surface["id"])

    latest = _latest(client, headers, workspace_id)
    per_surface = _history(client, headers, workspace_id, comp_id, surface["id"])

    # Same schema, same row — the dashboard loses nothing by switching.
    assert latest[0].keys() == per_surface[0].keys()
    assert latest[0] == per_surface[0]


def test_surface_with_no_runs_is_omitted(client):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    comp_id = _competitor(client, headers, workspace_id)["id"]
    _surface(client, headers, workspace_id, comp_id, "https://r.example.com/never")

    assert _latest(client, headers, workspace_id) == []


def test_spans_every_competitor_in_the_workspace(client, monkeypatch):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    first = _competitor(client, headers, workspace_id, "Rival A")["id"]
    second = _competitor(client, headers, workspace_id, "Rival B")["id"]
    s1 = _surface(client, headers, workspace_id, first, "https://a.example.com/p")
    s2 = _surface(client, headers, workspace_id, second, "https://b.example.com/p")

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    _check(client, headers, workspace_id, first, s1["id"])
    _check(client, headers, workspace_id, second, s2["id"])

    latest = _latest(client, headers, workspace_id)

    assert sorted(r["surface_id"] for r in latest) == sorted([s1["id"], s2["id"]])


def test_never_leaks_another_workspaces_runs(client, monkeypatch):
    a_headers, a_ws = _register_login_and_workspace(client, "alice@example.com")
    b_headers, b_ws = _register_login_and_workspace(client, "bob@example.com")

    a_comp = _competitor(client, a_headers, a_ws)["id"]
    a_surface = _surface(client, a_headers, a_ws, a_comp, "https://a.example.com/p")
    b_comp = _competitor(client, b_headers, b_ws)["id"]
    b_surface = _surface(client, b_headers, b_ws, b_comp, "https://b.example.com/p")

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    _check(client, a_headers, a_ws, a_comp, a_surface["id"])
    _check(client, b_headers, b_ws, b_comp, b_surface["id"])

    a_latest = _latest(client, a_headers, a_ws)

    assert [r["surface_id"] for r in a_latest] == [a_surface["id"]]

    # A non-member is refused outright rather than served an empty list.
    res = client.get(f"/workspaces/{b_ws}/check-runs/latest", headers=a_headers)
    assert res.status_code in (403, 404)


def test_requires_authentication(client):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")

    assert client.get(f"/workspaces/{workspace_id}/check-runs/latest").status_code == 401


def test_uses_one_query_regardless_of_surface_count(client, db_session, monkeypatch):
    """The point of the endpoint: cost must not scale with surface count."""
    from sqlalchemy import event

    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    comp_id = _competitor(client, headers, workspace_id)["id"]

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    for i in range(12):
        surface = _surface(
            client, headers, workspace_id, comp_id, f"https://r.example.com/p{i}"
        )
        _check(client, headers, workspace_id, comp_id, surface["id"])

    statements = []
    engine = db_session.get_bind()

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        latest = _latest(client, headers, workspace_id)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(latest) == 12

    check_run_queries = [s for s in statements if "FROM check_runs" in s]
    assert len(check_run_queries) == 1, (
        f"expected one check_runs query for 12 surfaces, got {len(check_run_queries)}"
    )
