from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.own_site import OwnSiteUpdate, OwnSiteResponse
from app.dependencies import (
    get_current_user,
    get_current_workspace,
    require_role,
    require_writable_workspace,
)
from app.services.own_site_service import (
    get_own_site, get_own_site_surface, set_own_site, delete_own_site
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/own-site",
    tags=["Own Site"]
)


def _to_response(competitor_id: int, surface) -> OwnSiteResponse:
    return OwnSiteResponse(
        competitor_id=competitor_id,
        surface_id=surface.id,
        url=surface.url,
        check_frequency=surface.check_frequency,
        last_checked_at=surface.last_checked_at,
    )


@router.get(
    "/",
    response_model=OwnSiteResponse
)
def get_own_site_route(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    own_site = get_own_site(db, workspace_id)
    if own_site is None:
        raise HTTPException(status_code=404, detail="No own-site configured for this workspace")

    surface = get_own_site_surface(db, workspace_id)
    if surface is None:
        raise HTTPException(status_code=404, detail="No own-site configured for this workspace")

    return _to_response(own_site.id, surface)


@router.put(
    "/",
    response_model=OwnSiteResponse
)
def set_own_site_route(
    workspace_id: int,
    payload: OwnSiteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _demo: None = Depends(require_writable_workspace("change the tracked site"))
):

    own_site = set_own_site(db, workspace_id, str(payload.url), current_user.id)
    surface = get_own_site_surface(db, workspace_id)

    return _to_response(own_site.id, surface)


@router.delete("/")
def delete_own_site_route(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("change the tracked site"))
):

    deleted = delete_own_site(db, workspace_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No own-site configured for this workspace")

    return {"message": "Own site removed"}
