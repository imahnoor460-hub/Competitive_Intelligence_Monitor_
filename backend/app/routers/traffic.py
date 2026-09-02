from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.company_profile import CompanyProfile
from app.models.traffic_snapshot import TrafficSnapshot
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.traffic import TrafficSnapshotResponse
from app.dependencies import (
    get_current_workspace,
    require_role,
    rate_limit,
    require_writable_workspace,
)
from app.services.traffic_service import fetch_and_store_traffic, TrafficProviderError

router = APIRouter(
    prefix="/workspaces/{workspace_id}/competitors/{competitor_id}/traffic",
    tags=["Traffic"]
)


def _get_owned_competitor(db: Session, workspace_id: int, competitor_id: int) -> Competitor:
    competitor = (
        db.query(Competitor)
        .filter(Competitor.id == competitor_id, Competitor.workspace_id == workspace_id)
        .first()
    )
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")
    return competitor


@router.get(
    "/",
    response_model=list[TrafficSnapshotResponse]
)
def list_traffic(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    return (
        db.query(TrafficSnapshot)
        .filter(TrafficSnapshot.competitor_id == competitor_id)
        .order_by(TrafficSnapshot.month.asc())
        .all()
    )


@router.post(
    "/refresh",
    response_model=list[TrafficSnapshotResponse]
)
def refresh_traffic(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _rate_limit: None = Depends(rate_limit("traffic-refresh", limit=10, window_seconds=3600.0)),
    _demo: None = Depends(require_writable_workspace("refresh traffic data"))
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.competitor_id == competitor_id)
        .first()
    )
    if profile is None or not profile.website_domain:
        raise HTTPException(
            status_code=400,
            detail="Set a website domain on this competitor's company profile first"
        )

    try:
        snapshots = fetch_and_store_traffic(db, competitor_id, profile.website_domain)
    except TrafficProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return snapshots
