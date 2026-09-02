"""Create (or repair) the shared demo account and its workspace.

Run once per deployment, and again whenever `DEMO_USER_PASSWORD` is rotated:

    cd backend && python -m scripts.provision_demo
    cd backend && python -m scripts.provision_demo --admin-email you@example.com
    cd backend && python -m scripts.provision_demo --unlock

Being flagged restricts the *demo account*, not the workspace, so an admin
added with `--admin-email` can edit the demo's data at any time through the
normal UI while the public "Try the demo" session stays read-only. That is the
intended way to curate it — no unlock, no re-lock.

`--unlock` clears the flag entirely, which opens the workspace to the public
demo session as well. It exists for wholesale changes and should not be left
in that state.

Everything it needs comes from the environment — `DEMO_USER_EMAIL` and
`DEMO_USER_PASSWORD`. The plaintext is read here and immediately handed to
`hash_password`, the same passlib bcrypt context `POST /auth/register` uses;
what reaches the database is a hash, exactly like any other user. The
plaintext is never stored, never printed, and never returned by any endpoint.

Deliberately a script rather than an admin endpoint. An endpoint that can mint
the demo account is an endpoint an attacker can reach; a script needs a shell
on the box. Nothing in the running API can create, modify or read this
account's credentials.

Idempotent, so it is safe to re-run:

* a missing user is created; an existing one has its hash refreshed, which is
  how a rotated `DEMO_USER_PASSWORD` is applied
* the demo workspace is found by its `is_demo` flag, falling back to the
  configured slug only when nothing is flagged yet — and a slug match that
  already holds competitors or other members is **refused**, never taken over
* a missing workspace is created and flagged
* a missing membership is added as `owner`
* `--admin-email` adds an existing user to the demo workspace as `owner`, so
  you can curate the demo signed in as yourself

It does **not** create competitors — see the two modes above.
"""

import sys

from app.core.config import settings
from app.core.security import hash_password
from app.database import SessionLocal
from app.models.competitor import Competitor
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole


def provision_demo(lock: bool = True, admin_email: str | None = None) -> int:
    if not settings.demo_user_email or not settings.demo_user_password:
        print(
            "DEMO_USER_EMAIL and DEMO_USER_PASSWORD must both be set. "
            "Nothing was changed.",
            file=sys.stderr,
        )
        return 1

    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .filter(User.email == settings.demo_user_email)
            .first()
        )

        if user is None:
            user = User(
                email=settings.demo_user_email,
                hashed_password=hash_password(settings.demo_user_password),
                full_name=settings.demo_user_full_name,
            )
            db.add(user)
            db.flush()
            print(f"Created demo user #{user.id}")
        else:
            # Re-hashing rather than comparing: this is how a rotated password
            # is applied, and bcrypt salts every hash differently so there is
            # nothing to compare against anyway.
            user.hashed_password = hash_password(settings.demo_user_password)
            print(f"Refreshed password hash for demo user #{user.id}")

        # Find the demo workspace by its flag first, and only fall back to the
        # slug when no workspace is flagged yet. Slug-first was a real bug: a
        # workspace someone had already created and named "demo" matched, and
        # the script joined the demo user to it — a live workspace with nine
        # competitors and another owner. Locking it would then have turned that
        # person's own workspace read-only.
        workspace = db.query(Workspace).filter(Workspace.is_demo.is_(True)).first()

        if workspace is None:
            candidate = (
                db.query(Workspace)
                .filter(Workspace.slug == settings.demo_workspace_slug)
                .first()
            )

            if candidate is not None:
                # Adopt an existing slug match only when it is demonstrably
                # empty. Anything with content or another member belongs to
                # somebody.
                competitors = (
                    db.query(Competitor)
                    .filter(Competitor.workspace_id == candidate.id)
                    .count()
                )
                other_members = (
                    db.query(WorkspaceMember)
                    .filter(
                        WorkspaceMember.workspace_id == candidate.id,
                        WorkspaceMember.user_id != user.id,
                    )
                    .count()
                )

                if competitors or other_members:
                    print(
                        f"Workspace #{candidate.id} already uses the slug "
                        f"{settings.demo_workspace_slug!r} and is not empty "
                        f"({competitors} competitor(s), {other_members} other "
                        f"member(s)). Refusing to take it over — set "
                        f"DEMO_WORKSPACE_SLUG to an unused value and re-run. "
                        f"Nothing was changed.",
                        file=sys.stderr,
                    )
                    db.rollback()
                    return 1

                workspace = candidate

        if workspace is None:
            workspace = Workspace(
                name=settings.demo_workspace_name,
                slug=settings.demo_workspace_slug,
                is_demo=lock,
            )
            db.add(workspace)
            db.flush()
            print(f"Created demo workspace #{workspace.id}")
        else:
            # Set every run: this script is the only writer of is_demo
            # anywhere, and the flag is what makes the workspace read-only.
            workspace.is_demo = lock
            print(f"Using existing demo workspace #{workspace.id}")

        membership = (
            db.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user.id,
            )
            .first()
        )

        if membership is None:
            # Owner, so you can seed the competitors through the UI while
            # signed in as the demo user (with --unlock). The read-only
            # restrictions key off the workspace flag, never off role, so
            # owner here grants no owner capabilities once it is locked.
            db.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=WorkspaceRole.owner,
                )
            )
            print("Added demo user to the demo workspace as owner")
        else:
            print(f"Membership already present ({membership.role.value})")

        if admin_email:
            admin = db.query(User).filter(User.email == admin_email).first()
            if admin is None:
                print(
                    f"No user with email {admin_email!r} — register that "
                    f"account first. Nothing was changed.",
                    file=sys.stderr,
                )
                db.rollback()
                return 1

            admin_membership = (
                db.query(WorkspaceMember)
                .filter(
                    WorkspaceMember.workspace_id == workspace.id,
                    WorkspaceMember.user_id == admin.id,
                )
                .first()
            )
            if admin_membership is None:
                db.add(WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=admin.id,
                    role=WorkspaceRole.owner,
                ))
                print(f"Added {admin_email} to the demo workspace as owner")
            else:
                admin_membership.role = WorkspaceRole.owner
                print(f"{admin_email} is already a member; ensured owner")

        db.commit()

        # The email is printed; the password never is, not even masked. A
        # length or a prefix in a log is still information about a live secret.
        state = "read-only for the demo account" if lock else "UNLOCKED for everyone"
        print(
            f"Demo ready ({state}): {settings.demo_user_email} "
            f"-> workspace #{workspace.id}"
        )
        if not lock:
            print(
                "The demo session can write while unlocked. Re-run without "
                "--unlock to restore it."
            )
        elif not admin_email:
            print(
                "Curate it signed in as yourself: re-run with "
                "--admin-email <your email> to join the workspace as owner."
            )
        return 0
    finally:
        db.close()


def _admin_email_from(argv: list[str]) -> str | None:
    if "--admin-email" not in argv:
        return None
    index = argv.index("--admin-email") + 1
    return argv[index] if index < len(argv) else None


if __name__ == "__main__":
    raise SystemExit(
        provision_demo(
            lock="--unlock" not in sys.argv,
            admin_email=_admin_email_from(sys.argv),
        )
    )
