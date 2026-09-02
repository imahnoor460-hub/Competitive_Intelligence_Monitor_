from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.company_profile import CompanyProfile
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.company_profile import CompanyProfileUpsert, CompanyProfileResponse
from app.dependencies import (
    get_current_workspace,
    require_role,
    require_writable_workspace,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/competitors/{competitor_id}/profile",
    tags=["Company Profiles"]
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
    response_model=CompanyProfileResponse
)
def get_profile(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.competitor_id == competitor_id)
        .first()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile yet for this competitor")

    return profile


@router.put(
    "/",
    response_model=CompanyProfileResponse
)
def upsert_profile(
    workspace_id: int,
    competitor_id: int,
    payload: CompanyProfileUpsert,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _demo: None = Depends(require_writable_workspace("edit company profiles"))
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.competitor_id == competitor_id)
        .first()
    )

    key_people = (
        [person.model_dump() for person in payload.key_people]
        if payload.key_people is not None else None
    )

    if profile is None:
        profile = CompanyProfile(
            competitor_id=competitor_id,
            industry=payload.industry,
            hq_location=payload.hq_location,
            employee_range=payload.employee_range,
            funding_stage=payload.funding_stage,
            key_people=key_people,
            notes_markdown=payload.notes_markdown,
            website_domain=payload.website_domain,
        )
        db.add(profile)
    else:
        profile.industry = payload.industry
        profile.hq_location = payload.hq_location
        profile.employee_range = payload.employee_range
        profile.funding_stage = payload.funding_stage
        profile.key_people = key_people
        profile.notes_markdown = payload.notes_markdown
        profile.website_domain = payload.website_domain

    db.commit()
    db.refresh(profile)

    return profile
