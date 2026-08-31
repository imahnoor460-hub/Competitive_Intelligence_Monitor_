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


def _body(client, headers, workspace_id):
    return client.get(
        f"/workspaces/{workspace_id}/check-runs/latest", headers=headers
    ).json()


def _latest(client, headers, workspace_id):
    return _body(client, headers, workspace_id)["latest"]


def _crawl_success_rate(runs):
    """The dashboard's original formula, computed over whatever runs it held.

    Reproduced here so the aggregate counts can be checked against the real
    thing rather than against a restatement of themselves.
    """
    if len(runs) == 0:
        return None
    finished = [r for r in runs if r["status"] != "running"]
    if len(finished) == 0:
        return None
    successes = [r for r in finished if r["status"] == "success"]
    return (len(successes) / len(finished)) * 100


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

    # Two statements — the ranked latest-per-surface query and the history
    # aggregate — and critically the count does not grow with surface count.
    # That is the property the fan-out violated.
    check_run_queries = [s for s in statements if "FROM check_runs" in s]
    assert len(check_run_queries) == 2, (
        f"expected two check_runs queries for 12 surfaces, got {len(check_run_queries)}"
    )


def _fail_next_check(monkeypatch, message="connection refused"):
    from app.services.snapshot_service import FetchError

    def _raise(url):
        raise FetchError(message)

    monkeypatch.setattr(check_service, "capture_clean_snapshot", _raise)


def test_aggregates_preserve_the_historical_crawl_success_rate(client, monkeypatch):
    """4 failures then 1 success must still read 20%, not 100%.

    The dashboard's crawl success rate is measured across every run ever
    recorded. Computing it from the latest run per surface instead would call
    this surface 100% healthy and silently change a user-facing number, which
    is the whole reason the endpoint carries these counts.
    """
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    comp_id = _competitor(client, headers, workspace_id)["id"]
    surface = _surface(client, headers, workspace_id, comp_id, "https://r.example.com/a")

    for _ in range(4):
        _fail_next_check(monkeypatch)
        _check(client, headers, workspace_id, comp_id, surface["id"])

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "recovered")
    _check(client, headers, workspace_id, comp_id, surface["id"])

    history = _history(client, headers, workspace_id, comp_id, surface["id"])
    assert len(history) == 5
    assert [r["status"] for r in history].count("failed") == 4
    assert [r["status"] for r in history].count("success") == 1

    body = _body(client, headers, workspace_id)

    assert body["total_runs"] == 5
    assert body["finished_runs"] == 5
    assert body["successful_runs"] == 1

    # What the dashboard now computes, against what it computed before from
    # the full per-surface history it used to fetch.
    from_aggregates = (body["successful_runs"] / body["finished_runs"]) * 100
    assert from_aggregates == _crawl_success_rate(history) == 20.0

    # And the value the naive latest-per-surface reading would have produced,
    # so this test fails loudly if the aggregates are ever dropped.
    assert _crawl_success_rate(body["latest"]) == 100.0


def test_aggregates_span_every_surface_and_competitor(client, monkeypatch):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    first = _competitor(client, headers, workspace_id, "Rival A")["id"]
    second = _competitor(client, headers, workspace_id, "Rival B")["id"]
    s1 = _surface(client, headers, workspace_id, first, "https://a.example.com/p")
    s2 = _surface(client, headers, workspace_id, second, "https://b.example.com/p")

    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    _check(client, headers, workspace_id, first, s1["id"])
    _fail_next_check(monkeypatch)
    _check(client, headers, workspace_id, second, s2["id"])

    body = _body(client, headers, workspace_id)

    assert body["total_runs"] == 2
    assert body["finished_runs"] == 2
    assert body["successful_runs"] == 1
    assert (body["successful_runs"] / body["finished_runs"]) * 100 == 50.0


def test_aggregates_are_zero_when_nothing_has_run(client):
    headers, workspace_id = _register_login_and_workspace(client, "alice@example.com")
    comp_id = _competitor(client, headers, workspace_id)["id"]
    _surface(client, headers, workspace_id, comp_id, "https://r.example.com/never")

    body = _body(client, headers, workspace_id)

    # count(), not sum() — an empty set must be 0 rather than null, or the
    # response fails validation.
    assert body == {
        "latest": [],
        "total_runs": 0,
        "finished_runs": 0,
        "successful_runs": 0,
    }


def test_aggregates_respect_workspace_isolation(client, monkeypatch):
    a_headers, a_ws = _register_login_and_workspace(client, "alice@example.com")
    b_headers, b_ws = _register_login_and_workspace(client, "bob@example.com")

    a_comp = _competitor(client, a_headers, a_ws)["id"]
    a_surface = _surface(client, a_headers, a_ws, a_comp, "https://a.example.com/p")
    b_comp = _competitor(client, b_headers, b_ws)["id"]
    b_surface = _surface(client, b_headers, b_ws, b_comp, "https://b.example.com/p")

    # Alice: one success. Bob: three failures — which must not drag Alice's
    # rate down.
    monkeypatch.setattr(check_service, "capture_clean_snapshot", lambda url: "v1")
    _check(client, a_headers, a_ws, a_comp, a_surface["id"])
    for _ in range(3):
        _fail_next_check(monkeypatch)
        _check(client, b_headers, b_ws, b_comp, b_surface["id"])

    a_body = _body(client, a_headers, a_ws)
    b_body = _body(client, b_headers, b_ws)

    assert a_body["total_runs"] == 1
    assert a_body["successful_runs"] == 1
    assert b_body["total_runs"] == 3
    assert b_body["successful_runs"] == 0
