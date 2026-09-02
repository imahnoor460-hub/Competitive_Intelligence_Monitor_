"""The demo workspace is readable by everyone and writable by nobody.

Two properties, and the pairing is the point: every state-changing endpoint is
refused in a demo workspace, and the *same call* against a normal workspace
still works. A test that only proved the first would pass just as happily if
the guard broke every workspace in the product.

Enforcement is server-side. The frontend hides these actions too, but the API
is reachable with the demo token directly, so hiding is presentation and these
tests are the contract.
"""

import pytest

import app.services.check_service as check_service
import app.services.competitor_discovery_service as discovery_service
from app.models.briefing import Briefing, BriefingAudience, BriefingDigestType, BriefingStatus
from app.models.surface import SurfaceType
from app.models.workspace import Workspace
from app.models.workspace_integration import IntegrationProvider, WorkspaceIntegration
from app.services.delivery.base import DeliveryResult
from app.services.delivery.delivery_service import _deliver_briefings


def _configure_demo_account(monkeypatch, email):
    """Point the app's demo-account setting at this test's user, so the guard
    has something to recognise."""

    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_user_email", email)
    monkeypatch.setattr(settings, "demo_user_password", "demo-password")


def _register(client, email):
    client.post(
        "/auth/register",
        json={"email": email, "password": "supersecret1", "full_name": "Someone"},
    )
    login = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _seed_workspace(client, headers, monkeypatch, name):
    """A workspace with one competitor, one surface and one briefing — enough
    for every mutating endpoint below to have something real to aim at."""

    monkeypatch.setattr(
        discovery_service, "discover_surfaces",
        lambda url: [(SurfaceType.other, "Home", "https://rival.example.com/")],
    )
    monkeypatch.setattr(
        check_service, "capture_clean_snapshot", lambda url: f"content of {url}"
    )

    workspace = client.post(
        "/workspaces/", json={"name": name}, headers=headers
    ).json()
    competitor = client.post(
        f"/workspaces/{workspace['id']}/competitors/",
        json={"name": "Rival"},
        headers=headers,
    ).json()
    surface = client.post(
        f"/workspaces/{workspace['id']}/competitors/{competitor['id']}/surfaces/",
        json={
            "surface_type": "pricing",
            "url": "https://rival.example.com/pricing",
            "check_frequency": "daily",
        },
        headers=headers,
    ).json()

    return workspace["id"], competitor["id"], surface["id"]


def _mark_demo(db_session, workspace_id):
    workspace = (
        db_session.query(Workspace).filter(Workspace.id == workspace_id).first()
    )
    workspace.is_demo = True
    db_session.commit()


DEMO_ACCOUNT = "demo-visitor@example.com"


def _mutations(workspace_id, competitor_id, surface_id):
    """(label, method, path, json) for every state-changing endpoint that is
    scoped to a workspace."""

    ws = f"/workspaces/{workspace_id}"
    comp = f"{ws}/competitors/{competitor_id}"

    return [
        ("add competitor", "post", f"{ws}/competitors/", {"name": "New"}),
        ("delete competitor", "delete", comp, None),
        ("add surface", "post", f"{comp}/surfaces/",
         {"surface_type": "blog", "url": "https://rival.example.com/blog"}),
        ("delete surface", "delete", f"{comp}/surfaces/{surface_id}", None),
        ("discover pages", "post", f"{comp}/surfaces/discover", None),
        ("check one surface", "post", f"{comp}/surfaces/{surface_id}/check", None),
        ("run check (sweep)", "post", f"{ws}/check-all", None),
        ("refresh site summary", "post", f"{comp}/site-summary/refresh", None),
        ("generate briefing", "post", f"{ws}/briefings/generate-now",
         {"audience": "sales", "digest_type": "daily"}),
        ("propose battlecard update", "post", f"{comp}/battlecard/updates", None),
        ("category price lookup", "post", f"{comp}/category-price/",
         {"category": "Sale"}),
        ("refresh traffic", "post", f"{comp}/traffic/refresh", None),
        ("edit company profile", "put", f"{comp}/profile", {"industry": "Retail"}),
        ("set own site", "put", f"{ws}/own-site/", {"url": "https://us.example.com"}),
        ("delete own site", "delete", f"{ws}/own-site/", None),
        ("add response library item", "post", f"{ws}/response-library/",
         {"title": "T", "body": "B"}),
        ("set budget", "put", f"{ws}/budget/", {"monthly_cap_usd": 5}),
        ("configure integration", "put", f"{ws}/integrations/",
         {"provider": "slack", "config": {"webhook_url": "https://hooks.example.com/x"},
          "enabled": True}),
        ("delete integration", "delete", f"{ws}/integrations/slack", None),
        ("test-send integration", "post", f"{ws}/integrations/slack/test-send", None),
        ("invite member", "post", f"{ws}/members",
         {"email": "someone@example.com", "role": "editor"}),
    ]


def _call(client, headers, method, path, body):
    fn = getattr(client, method)
    if body is None:
        return fn(path, headers=headers)
    return fn(path, json=body, headers=headers)


# --- the demo refuses every mutation ----------------------------------------

@pytest.mark.parametrize(
    "label,method,path_template,body",
    [(m[0], m[1], m[2], m[3]) for m in _mutations("{ws}", "{comp}", "{surface}")],
    ids=[m[0] for m in _mutations("{ws}", "{comp}", "{surface}")],
)
def test_every_mutation_is_refused_in_the_demo_workspace(
    client, db_session, monkeypatch, label, method, path_template, body
):
    headers = _register(client, DEMO_ACCOUNT)
    workspace_id, competitor_id, surface_id = _seed_workspace(
        client, headers, monkeypatch, "Demo"
    )
    _mark_demo(db_session, workspace_id)
    _configure_demo_account(monkeypatch, DEMO_ACCOUNT)

    path = (
        path_template
        .replace("{ws}", str(workspace_id))
        .replace("{comp}", str(competitor_id))
        .replace("{surface}", str(surface_id))
    )

    res = _call(client, headers, method, path, body)

    assert res.status_code == 403, f"{label} was not refused: {res.status_code}"
    assert "read-only demo" in res.json()["detail"]


@pytest.mark.parametrize(
    "label,method,path_template,body",
    [(m[0], m[1], m[2], m[3]) for m in _mutations("{ws}", "{comp}", "{surface}")],
    ids=[m[0] for m in _mutations("{ws}", "{comp}", "{surface}")],
)
def test_the_same_mutation_still_works_in_a_normal_workspace(
    client, monkeypatch, label, method, path_template, body
):
    """The half that matters just as much: the guard must be invisible to every
    existing account. Any non-403 is a pass — several of these legitimately
    answer 400/404/502 on a bare test fixture (no LLM configured, no
    integration set up); what is under test is that the demo guard is not what
    stopped them."""

    headers = _register(client, "normal@example.com")
    workspace_id, competitor_id, surface_id = _seed_workspace(
        client, headers, monkeypatch, "Normal"
    )

    path = (
        path_template
        .replace("{ws}", str(workspace_id))
        .replace("{comp}", str(competitor_id))
        .replace("{surface}", str(surface_id))
    )

    res = _call(client, headers, method, path, body)

    assert res.status_code != 403, f"{label} was wrongly refused for a normal workspace"


# --- reading is untouched ----------------------------------------------------

@pytest.mark.parametrize(
    "path_template",
    [
        "/workspaces/{ws}/competitors/",
        "/workspaces/{ws}/competitors/{comp}/comparison",
        "/workspaces/{ws}/competitors/{comp}/surfaces/",
        "/workspaces/{ws}/change-logs/",
        "/workspaces/{ws}/briefings/",
        "/workspaces/{ws}/approvals/",
        "/workspaces/{ws}/jobs/active",
        "/workspaces/{ws}/audit-log/",
    ],
)
def test_the_demo_stays_fully_readable(
    client, db_session, monkeypatch, path_template
):
    """A demo nobody can look at is not a demo."""

    headers = _register(client, DEMO_ACCOUNT)
    workspace_id, competitor_id, _surface_id = _seed_workspace(
        client, headers, monkeypatch, "Demo"
    )
    _mark_demo(db_session, workspace_id)
    _configure_demo_account(monkeypatch, DEMO_ACCOUNT)

    path = (
        path_template
        .replace("{ws}", str(workspace_id))
        .replace("{comp}", str(competitor_id))
    )

    assert client.get(path, headers=headers).status_code == 200


def test_the_workspace_list_reports_read_only_per_caller(
    client, db_session, monkeypatch
):
    """`is_demo` is a fact about the workspace; `read_only` is about the
    caller. The UI gates on the second, or an admin curating the demo would
    have their own controls hidden."""

    headers = _register(client, DEMO_ACCOUNT)
    workspace_id, _competitor_id, _surface_id = _seed_workspace(
        client, headers, monkeypatch, "Demo"
    )
    _mark_demo(db_session, workspace_id)

    # Same workspace, same request, seen by an admin: a demo, but writable.
    as_admin = client.get("/workspaces/", headers=headers).json()
    demo_as_admin = next(w for w in as_admin if w["id"] == workspace_id)
    assert demo_as_admin["is_demo"] is True
    assert demo_as_admin["read_only"] is False

    # Seen by the demo account itself: read-only.
    _configure_demo_account(monkeypatch, DEMO_ACCOUNT)
    as_demo = client.get("/workspaces/", headers=headers).json()
    demo_as_demo = next(w for w in as_demo if w["id"] == workspace_id)
    assert demo_as_demo["is_demo"] is True
    assert demo_as_demo["read_only"] is True


# --- approvals and delivery --------------------------------------------------

def test_approving_and_rejecting_are_refused_in_the_demo(
    client, db_session, monkeypatch
):
    """Approval is the only path that can send a briefing outward, so it is
    guarded even though the demo cannot generate one in the first place."""

    headers = _register(client, DEMO_ACCOUNT)
    workspace_id, _competitor_id, _surface_id = _seed_workspace(
        client, headers, monkeypatch, "Demo"
    )
    _mark_demo(db_session, workspace_id)
    _configure_demo_account(monkeypatch, DEMO_ACCOUNT)

    for action in ("approve", "reject"):
        res = client.post(
            f"/workspaces/{workspace_id}/approvals/1/{action}", headers=headers
        )
        assert res.status_code == 403
        assert "read-only demo" in res.json()["detail"]


def test_delivery_is_suppressed_for_a_demo_workspace(client, db_session, monkeypatch):
    """Belt and braces, and not redundant: the scheduler calls deliver_digest
    for every workspace holding approved briefings, with no request behind it,
    so a router-level guard would never see that path."""

    headers = _register(client, "delivery@example.com")
    workspace_id, _competitor_id, _surface_id = _seed_workspace(
        client, headers, monkeypatch, "Demo"
    )

    db_session.add(WorkspaceIntegration(
        workspace_id=workspace_id,
        provider=IntegrationProvider.slack,
        config={"webhook_url": "https://hooks.example.com/should-never-fire"},
        enabled=True,
    ))
    briefing = Briefing(
        workspace_id=workspace_id,
        audience=BriefingAudience.sales,
        digest_type=BriefingDigestType.urgent,
        title="Demo briefing",
        body_markdown="body",
        status=BriefingStatus.approved,
    )
    db_session.add(briefing)
    db_session.commit()

    sent = []
    monkeypatch.setattr(
        "app.services.delivery.delivery_service._CONNECTORS",
        {IntegrationProvider.slack: type(
            "Spy", (),
            {"send": lambda self, config, payload: (
                sent.append(payload), DeliveryResult(success=True)
            )[1]},
        )()},
    )

    # Normal workspace: it tries to send.
    _deliver_briefings(db_session, workspace_id, [briefing])
    assert len(sent) == 1

    # Same workspace, now a demo: it does not.
    _mark_demo(db_session, workspace_id)
    _deliver_briefings(db_session, workspace_id, [briefing])
    assert len(sent) == 1, "delivery fired for a demo workspace"


# --- the account-level escapes ----------------------------------------------

def test_the_demo_account_cannot_create_its_own_workspace(
    client, db_session, monkeypatch
):
    """Otherwise the restrictions are one POST away from irrelevant: a visitor
    makes a fresh unrestricted workspace and runs the paid pipeline there."""

    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_user_email", "demo@example.com")
    monkeypatch.setattr(settings, "demo_user_password", "demo-password")
    headers = _register(client, "demo@example.com")

    res = client.post("/workspaces/", json={"name": "Escape"}, headers=headers)

    assert res.status_code == 403
    assert "demo account" in res.json()["detail"]


def test_the_demo_account_cannot_delete_itself(client, monkeypatch):
    """A shared account, so this would take the demo down for everyone."""

    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_user_email", "demo@example.com")
    monkeypatch.setattr(settings, "demo_user_password", "demo-password")
    headers = _register(client, "demo@example.com")

    res = client.delete("/users/me", headers=headers)

    assert res.status_code == 403


def test_a_normal_account_can_still_create_a_workspace_and_delete_itself(
    client, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_user_email", "demo@example.com")
    headers = _register(client, "normal-user@example.com")

    created = client.post("/workspaces/", json={"name": "Mine"}, headers=headers)
    assert created.status_code == 200

    deleted = client.delete("/users/me", headers=headers)
    assert deleted.status_code != 403


# --- the admin exemption -----------------------------------------------------

def test_an_admin_can_edit_the_demo_workspace_while_the_demo_account_cannot(
    client, db_session, monkeypatch
):
    """The whole point of scoping the guard to the account: curating the demo
    must not require flipping the flag off and on again. Same workspace, same
    endpoint, two callers, opposite outcomes."""

    from app.models.user import User
    from app.models.workspace_member import WorkspaceMember, WorkspaceRole

    admin_headers = _register(client, "admin@example.com")
    workspace_id, _competitor_id, _surface_id = _seed_workspace(
        client, admin_headers, monkeypatch, "Demo"
    )

    demo_headers = _register(client, DEMO_ACCOUNT)
    demo_user = (
        db_session.query(User).filter(User.email == DEMO_ACCOUNT).one()
    )
    db_session.add(WorkspaceMember(
        workspace_id=workspace_id,
        user_id=demo_user.id,
        role=WorkspaceRole.owner,
    ))
    db_session.commit()

    _mark_demo(db_session, workspace_id)
    _configure_demo_account(monkeypatch, DEMO_ACCOUNT)

    path = f"/workspaces/{workspace_id}/competitors/"

    refused = client.post(path, json={"name": "By the demo"}, headers=demo_headers)
    assert refused.status_code == 403
    assert "read-only demo" in refused.json()["detail"]

    allowed = client.post(path, json={"name": "By the admin"}, headers=admin_headers)
    assert allowed.status_code == 200, (
        f"the admin was blocked from curating the demo: {allowed.status_code}"
    )


def test_the_admin_sees_writable_and_the_demo_account_sees_read_only(
    client, db_session, monkeypatch
):
    """Both are owners of the same demo workspace; only the demo session is
    told it cannot write."""

    from app.models.user import User
    from app.models.workspace_member import WorkspaceMember, WorkspaceRole

    admin_headers = _register(client, "admin@example.com")
    workspace_id, _competitor_id, _surface_id = _seed_workspace(
        client, admin_headers, monkeypatch, "Demo"
    )

    demo_headers = _register(client, DEMO_ACCOUNT)
    demo_user = db_session.query(User).filter(User.email == DEMO_ACCOUNT).one()
    db_session.add(WorkspaceMember(
        workspace_id=workspace_id, user_id=demo_user.id, role=WorkspaceRole.owner
    ))
    db_session.commit()

    _mark_demo(db_session, workspace_id)
    _configure_demo_account(monkeypatch, DEMO_ACCOUNT)

    def _seen_by(headers):
        rows = client.get("/workspaces/", headers=headers).json()
        return next(w for w in rows if w["id"] == workspace_id)

    assert _seen_by(admin_headers)["read_only"] is False
    assert _seen_by(demo_headers)["read_only"] is True
    # The workspace itself is a demo either way.
    assert _seen_by(admin_headers)["is_demo"] is True
    assert _seen_by(demo_headers)["is_demo"] is True


def test_an_unflagged_workspace_is_writable_even_by_the_demo_account(
    client, db_session, monkeypatch
):
    """Both halves are required. The demo account is restricted *in the demo
    workspace*, not everywhere — which is what makes `--unlock` still work for
    wholesale changes."""

    headers = _register(client, DEMO_ACCOUNT)
    workspace_id, _competitor_id, _surface_id = _seed_workspace(
        client, headers, monkeypatch, "Unlocked"
    )
    _configure_demo_account(monkeypatch, DEMO_ACCOUNT)

    res = client.post(
        f"/workspaces/{workspace_id}/competitors/",
        json={"name": "While unlocked"},
        headers=headers,
    )

    assert res.status_code == 200
