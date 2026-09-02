from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.core.config import settings
from app.core.security import decode_access_token
from app.services.rate_limiter import check_rate_limit, RateLimitExceededError

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login"
)

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):

    payload = decode_access_token(token)

    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )

    email = payload.get("sub")

    if email is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )

    user = (
        db.query(User)
        .filter(User.email == email)
        .first()
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User not found"
        )

    return user


def get_current_workspace(
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> WorkspaceMember:
    """Resolves the caller's membership in `workspace_id`, 404ing if they
    aren't a member — this is what keeps one workspace's data invisible to
    another, so every workspace-scoped router depends on it rather than
    querying WorkspaceMember directly.
    """

    membership = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == current_user.id
        )
        .first()
    )

    if membership is None:
        raise HTTPException(
            status_code=404,
            detail="Workspace not found"
        )

    return membership


def require_role(*roles: WorkspaceRole):
    def _check(
        membership: WorkspaceMember = Depends(get_current_workspace)
    ) -> WorkspaceMember:
        if membership.role not in roles:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to perform this action"
            )

        return membership

    return _check


def enforce_rate_limit(
    scope: str,
    workspace_id: int,
    limit: int | None = None,
    window_seconds: float | None = None,
) -> None:
    """The rate-limit guard as a plain call, for endpoints that must run it
    at a specific point in the handler rather than as a dependency — a
    dependency always fires before the body, which is wrong when another
    guard has to be evaluated first (see briefings.generate_now, where the
    budget is checked before a rate-limit token is spent).
    """

    key = f"{scope}:{workspace_id}"
    try:
        check_rate_limit(
            key,
            limit if limit is not None else settings.rate_limit_llm_requests,
            window_seconds if window_seconds is not None else settings.rate_limit_llm_window_seconds,
        )
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc))


def rate_limit(scope: str, limit: int | None = None, window_seconds: float | None = None):
    """FastAPI dependency factory — each `scope` gets its own bucket per
    workspace, so a burst against one LLM-triggering endpoint doesn't eat
    into another's allowance. Defaults come from Settings so a deployer can
    tune the shared rate without touching call sites.
    """

    def _check(
        membership: WorkspaceMember = Depends(get_current_workspace)
    ) -> None:
        enforce_rate_limit(scope, membership.workspace_id, limit, window_seconds)

    return _check


def workspace_is_demo(db: Session, workspace_id: int) -> bool:
    """Whether this workspace is the public demo."""

    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    return bool(workspace is not None and workspace.is_demo)


def is_demo_account(user: User) -> bool:
    """Whether this is the shared account behind the "Try the demo" button."""

    return bool(
        settings.demo_user_email and user.email == settings.demo_user_email
    )


def workspace_is_read_only_for(db: Session, workspace_id: int, user: User) -> bool:
    """Whether *this caller* may not write to *this workspace*.

    Two conditions, and both are required: the workspace is the public demo,
    and the caller is the shared demo account. That separation is the point.
    Restricting everyone in the workspace meant the flag locked the owner out
    too, so every edit to the demo's own data needed a CLI unlock and re-lock.

    Still keyed off the workspace flag and account identity, never off role —
    the demo account is an `owner` there and gets none of an owner's
    capabilities, while an admin invited into the same workspace keeps all of
    them.
    """

    return workspace_is_demo(db, workspace_id) and is_demo_account(user)


def require_writable_workspace(action: str = "make changes"):
    """Refuse a state-changing request against the demo workspace.

    The one guard every mutating workspace-scoped endpoint depends on. It sits
    here, next to `get_current_workspace`, for the same reason tenancy does: a
    check scattered across thirty routers is a check a thirty-first router will
    forget. Frontend buttons are hidden too, but that is presentation — the API
    is reachable with the demo token directly, so this is the enforcement.

    Refuses the demo account in the demo workspace, and nobody else: a normal
    workspace never reaches the raise, and neither does an admin working on the
    demo's own data. See `workspace_is_read_only_for`.
    """

    def _check(
        workspace_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        membership: WorkspaceMember = Depends(get_current_workspace),
    ) -> None:
        if workspace_is_read_only_for(db, workspace_id, current_user):
            raise HTTPException(
                status_code=403,
                detail=f"This is a read-only demo workspace — you cannot {action} here",
            )

    return _check


def require_not_demo_user(action: str = "do that"):
    """The user-level counterpart, for the handful of endpoints that carry no
    workspace_id — account deletion and workspace creation.

    Without it the demo is trivially escapable in both directions: a visitor
    could delete the shared demo account out from under everyone, or create a
    fresh unrestricted workspace and run the paid pipeline inside it.
    """

    def _check(current_user: User = Depends(get_current_user)) -> None:
        if is_demo_account(current_user):
            raise HTTPException(
                status_code=403,
                detail=f"The demo account cannot {action}",
            )

    return _check
