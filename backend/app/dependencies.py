from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
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