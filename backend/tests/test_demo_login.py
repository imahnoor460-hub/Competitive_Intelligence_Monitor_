"""The demo sign-in.

The property under test is mostly a negative one: a visitor gets a working
session without the browser ever holding the demo credentials, and a
deployment that has not configured a demo behaves as though the endpoint does
not exist. Normal registration and login are untouched, which the last two
tests pin.
"""

import app.routers.auth as auth_router
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole


DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password-not-in-the-bundle"


def _configure(monkeypatch, email=DEMO_EMAIL, password=DEMO_PASSWORD):
    monkeypatch.setattr(settings, "demo_user_email", email)
    monkeypatch.setattr(settings, "demo_user_password", password)


def _provisioned_user(db_session, password=DEMO_PASSWORD):
    user = User(
        email=DEMO_EMAIL,
        hashed_password=hash_password(password),
        full_name="Demo User",
    )
    db_session.add(user)
    db_session.commit()
    return user


# --- the endpoint -----------------------------------------------------------

def test_demo_login_returns_an_ordinary_session(client, db_session, monkeypatch):
    """Same token shape as /auth/login, and it works on the same endpoints —
    there is no separate demo authentication path to go wrong."""

    _configure(monkeypatch)
    _provisioned_user(db_session)

    res = client.post("/auth/demo-login")

    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    me = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == DEMO_EMAIL


def test_demo_login_takes_no_credentials_from_the_caller(
    client, db_session, monkeypatch
):
    """It accepts no body, so there is nothing for the frontend to know and
    nothing for a caller to substitute."""

    _configure(monkeypatch)
    _provisioned_user(db_session)

    res = client.post(
        "/auth/demo-login",
        json={"email": "someone@else.com", "password": "guess"},
    )

    assert res.status_code == 200
    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {res.json()['access_token']}"},
    )
    # The body was ignored entirely: the session is the configured demo user.
    assert me.json()["email"] == DEMO_EMAIL


def test_the_response_never_carries_the_password(client, db_session, monkeypatch):
    _configure(monkeypatch)
    _provisioned_user(db_session)

    res = client.post("/auth/demo-login")

    assert DEMO_PASSWORD not in res.text
    assert set(res.json()) == {"access_token", "token_type"}


def test_a_deployment_without_a_demo_hides_the_endpoint(client, monkeypatch):
    """404 rather than 403: an install with no demo should not advertise that
    this route exists."""

    monkeypatch.setattr(settings, "demo_user_email", None)
    monkeypatch.setattr(settings, "demo_user_password", None)

    assert client.post("/auth/demo-login").status_code == 404


def test_an_unconfigured_404_says_so_in_the_logs(client, monkeypatch, caplog):
    """In an access log this 404 is indistinguishable from the route not being
    deployed, which is the wrong diagnosis and cost a real debugging session.
    The caller still learns nothing; the operator gets told which variable is
    missing."""

    monkeypatch.setattr(settings, "demo_user_email", None)
    monkeypatch.setattr(settings, "demo_user_password", "set-but-useless-alone")

    with caplog.at_level("WARNING"):
        assert client.post("/auth/demo-login").status_code == 404

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "DEMO_USER_EMAIL" in logged
    assert "not a missing route" in logged
    # The variable that *is* set must not be named as missing.
    assert "DEMO_USER_PASSWORD" not in logged


def test_a_configured_but_unprovisioned_demo_fails_closed(client, monkeypatch):
    """Environment set, `scripts/provision_demo.py` never run — no user row to
    authenticate, so no session."""

    _configure(monkeypatch)

    res = client.post("/auth/demo-login")

    assert res.status_code == 503
    assert DEMO_PASSWORD not in res.text


def test_a_rotated_password_fails_closed_until_reprovisioned(
    client, db_session, monkeypatch
):
    """The endpoint verifies the configured password against the stored hash
    rather than trusting the environment. Rotating DEMO_USER_PASSWORD without
    re-running the provision script therefore stops the demo instead of
    handing out sessions on a stale hash."""

    _provisioned_user(db_session, password="the-old-password")
    _configure(monkeypatch, password="the-new-password")

    assert client.post("/auth/demo-login").status_code == 503


def test_the_demo_login_is_rate_limited(client, db_session, monkeypatch):
    """A shared, unauthenticated endpoint whose work is a bcrypt comparison —
    without a bucket it is a free CPU sink on a 0.2-vCPU container."""

    _configure(monkeypatch)
    _provisioned_user(db_session)
    # Keeps the test cheap: 60 real bcrypt comparisons is seconds of CPU, and
    # what is under test is the limiter, not passlib.
    monkeypatch.setattr(auth_router, "verify_password", lambda *_: True)

    statuses = {client.post("/auth/demo-login").status_code for _ in range(62)}

    assert 429 in statuses


# --- normal auth is untouched ------------------------------------------------

def test_normal_registration_and_login_still_work(client, monkeypatch):
    _configure(monkeypatch)

    registered = client.post(
        "/auth/register",
        json={
            "email": "real@example.com",
            "password": "supersecret1",
            "full_name": "Real",
        },
    )
    assert registered.status_code == 200

    logged_in = client.post(
        "/auth/login",
        json={"email": "real@example.com", "password": "supersecret1"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["token_type"] == "bearer"


def test_the_demo_password_is_not_a_backdoor_into_normal_login(
    client, db_session, monkeypatch
):
    """/auth/login still requires the real password for the demo user like any
    other account — demo-login is a separate door, not a weaker lock."""

    _configure(monkeypatch)
    _provisioned_user(db_session)

    res = client.post(
        "/auth/login", json={"email": DEMO_EMAIL, "password": "wrong-password"}
    )

    assert res.status_code == 401


# --- the provisioning script -------------------------------------------------

def test_provisioning_stores_a_hash_and_is_idempotent(db_session, monkeypatch):
    """The one place the plaintext is read. It must reach the database only as
    a bcrypt hash, and re-running must not duplicate the user, the workspace or
    the membership."""

    import scripts.provision_demo as provision

    _configure(monkeypatch)
    monkeypatch.setattr(provision, "SessionLocal", lambda: db_session)
    # The script closes its session; the fixture owns this one.
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert provision.provision_demo() == 0
    assert provision.provision_demo() == 0

    users = db_session.query(User).filter(User.email == DEMO_EMAIL).all()
    assert len(users) == 1
    assert users[0].hashed_password != DEMO_PASSWORD
    assert DEMO_PASSWORD not in users[0].hashed_password
    assert verify_password(DEMO_PASSWORD, users[0].hashed_password)

    workspaces = (
        db_session.query(Workspace)
        .filter(Workspace.slug == settings.demo_workspace_slug)
        .all()
    )
    assert len(workspaces) == 1

    memberships = (
        db_session.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspaces[0].id,
            WorkspaceMember.user_id == users[0].id,
        )
        .all()
    )
    assert len(memberships) == 1
    assert memberships[0].role == WorkspaceRole.owner


def test_provisioning_refuses_without_configuration(db_session, monkeypatch):
    import scripts.provision_demo as provision

    monkeypatch.setattr(settings, "demo_user_email", None)
    monkeypatch.setattr(settings, "demo_user_password", None)
    monkeypatch.setattr(provision, "SessionLocal", lambda: db_session)

    assert provision.provision_demo() == 1
    assert db_session.query(User).count() == 0


def test_provisioning_refuses_to_take_over_somebody_elses_workspace(
    db_session, monkeypatch
):
    """The bug this exists for: a workspace someone had already created and
    named "demo" matched on slug, and the script joined the demo user to it —
    a live workspace with nine competitors and another owner. Locking it would
    have turned that person's own workspace read-only."""

    import scripts.provision_demo as provision
    from app.models.competitor import Competitor

    _configure(monkeypatch)
    monkeypatch.setattr(provision, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    owner = User(email="real@example.com", hashed_password="x", full_name="Real")
    db_session.add(owner)
    db_session.flush()

    theirs = Workspace(name="demo", slug=settings.demo_workspace_slug)
    db_session.add(theirs)
    db_session.flush()
    db_session.add(WorkspaceMember(
        workspace_id=theirs.id, user_id=owner.id, role=WorkspaceRole.owner
    ))
    db_session.add(Competitor(
        name="Rival", workspace_id=theirs.id, created_by_user_id=owner.id
    ))
    db_session.commit()

    assert provision.provision_demo() == 1

    db_session.refresh(theirs)
    assert theirs.is_demo is False, "someone else's workspace was flagged"
    assert (
        db_session.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == theirs.id)
        .count()
        == 1
    ), "the demo user was added to someone else's workspace"


def test_provisioning_finds_its_workspace_by_flag_not_by_slug(
    db_session, monkeypatch
):
    """Once flagged, the demo workspace is found by `is_demo`, so renaming its
    slug — or someone else claiming that slug later — cannot redirect the
    script at a different workspace."""

    import scripts.provision_demo as provision

    _configure(monkeypatch)
    monkeypatch.setattr(provision, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert provision.provision_demo() == 0
    flagged = db_session.query(Workspace).filter(Workspace.is_demo.is_(True)).one()

    flagged.slug = "renamed-after-the-fact"
    db_session.commit()

    assert provision.provision_demo() == 0

    assert db_session.query(Workspace).filter(Workspace.is_demo.is_(True)).count() == 1
    assert (
        db_session.query(Workspace).filter(Workspace.is_demo.is_(True)).one().id
        == flagged.id
    )


def test_provisioning_can_add_an_admin_to_the_demo_workspace(
    db_session, monkeypatch
):
    """--admin-email is how the demo gets curated without unlocking it: the
    admin edits as themselves while the shared demo session stays read-only."""

    import scripts.provision_demo as provision

    _configure(monkeypatch)
    monkeypatch.setattr(provision, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    admin = User(email="admin@example.com", hashed_password="x", full_name="Admin")
    db_session.add(admin)
    db_session.commit()

    assert provision.provision_demo(admin_email="admin@example.com") == 0

    workspace = db_session.query(Workspace).filter(Workspace.is_demo.is_(True)).one()
    membership = (
        db_session.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == admin.id,
        )
        .one()
    )
    assert membership.role == WorkspaceRole.owner

    # Re-running does not duplicate the membership.
    assert provision.provision_demo(admin_email="admin@example.com") == 0
    assert (
        db_session.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == admin.id,
        )
        .count()
        == 1
    )


def test_provisioning_refuses_an_unknown_admin_email(db_session, monkeypatch):
    """A typo would otherwise pass silently and leave you wondering why you
    still cannot edit the demo."""

    import scripts.provision_demo as provision

    _configure(monkeypatch)
    monkeypatch.setattr(provision, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert provision.provision_demo(admin_email="typo@example.com") == 1
    assert db_session.query(Workspace).filter(Workspace.is_demo.is_(True)).count() == 0
