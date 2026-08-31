import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.company_profile import CompanyProfile
from app.models.battlecard import Battlecard
from app.models.surface import Surface
from app.models.traffic_snapshot import TrafficSnapshot
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.competitor import (
    CompetitorCreate,
    CompetitorResponse
)
from app.schemas.comparison import ComparisonResponse, BenchmarkComparisonResponse
from app.dependencies import get_current_user, get_current_workspace, require_role
from app.scheduler import schedule_surface
from app.services.comparison_service import summarize_competitor
from app.services.competitor_service import delete_competitor as delete_competitor_cascade
from app.services.own_site_service import get_own_site
from app.services.surface_discovery_service import discover_surfaces, SurfaceDiscoveryError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/competitors",
    tags=["Competitors"]
)


@router.post(
    "/",
    response_model=CompetitorResponse
)
def create_competitor(
    workspace_id: int,
    competitor: CompetitorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor))
):

    existing = (
        db.query(Competitor)
        .filter(
            Competitor.workspace_id == workspace_id,
            Competitor.name == competitor.name
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Competitor already tracked"
        )

    new_competitor = Competitor(
        name=competitor.name,
        workspace_id=workspace_id,
        created_by_user_id=current_user.id
    )

    db.add(new_competitor)
    db.commit()
    db.refresh(new_competitor)
    competitor_id = new_competitor.id

    surfaces_discovered = 0
    if competitor.website_url is not None:
        # discover_surfaces launches a browser with a 60s navigation timeout,
        # and the refresh above left a connection checked out on this
        # request-scoped session — hand it back first. `competitor` is the
        # request schema rather than an ORM instance, so reading website_url
        # after the release costs nothing; new_competitor is deliberately not
        # touched again until the loop below re-acquires.
        db.commit()

        try:
            discovered = discover_surfaces(str(competitor.website_url))
        except SurfaceDiscoveryError as exc:
            logger.warning(
                "Surface discovery failed for competitor %s (%s): %s",
                competitor_id, competitor.website_url, exc,
            )
            discovered = []

        for surface_type, name, url in discovered:
            new_surface = Surface(
                competitor_id=competitor_id,
                surface_type=surface_type,
                name=name,
                url=url,
            )
            db.add(new_surface)
            db.commit()
            db.refresh(new_surface)
            schedule_surface(new_surface)
            surfaces_discovered += 1

    # Not a DB column — set only so the response can report how many pages
    # were auto-discovered without a schema migration.
    new_competitor.surfaces_discovered = surfaces_discovered

    return new_competitor


@router.get(
    "/",
    response_model=list[CompetitorResponse]
)
def get_competitors(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    competitors = (
        db.query(Competitor)
        .filter(
            Competitor.workspace_id == workspace_id,
            Competitor.is_own_site.is_(False)
        )
        .all()
    )

    return competitors


@router.delete("/{competitor_id}")
def delete_competitor(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor))
):

    deleted = delete_competitor_cascade(db, workspace_id, competitor_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Competitor not found"
        )

    return {
        "message": "Competitor deleted"
    }


def _benchmark_block(db: Session, benchmark_competitor: Competitor) -> BenchmarkComparisonResponse:
    traffic = (
        db.query(TrafficSnapshot)
        .filter(TrafficSnapshot.competitor_id == benchmark_competitor.id)
        .order_by(TrafficSnapshot.month.asc())
        .all()
    ) or None

    return BenchmarkComparisonResponse(
        competitor=benchmark_competitor,
        change_summary=summarize_competitor(db, benchmark_competitor.id),
        traffic=traffic,
    )


@router.get(
    "/{competitor_id}/comparison",
    response_model=ComparisonResponse
)
def get_competitor_comparison(
    workspace_id: int,
    competitor_id: int,
    compare_to: int | None = None,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):
    """Everything the Compare page needs in one call: this competitor's
    profile/battlecard/change trend/traffic, plus a "vs." benchmark so the
    frontend can render a side-by-side without orchestrating half a dozen
    separate fetches. The benchmark is your own site if one is configured;
    otherwise pass `compare_to` (another competitor_id in this workspace)
    to compare against that competitor instead — the fallback for
    workspaces with no own-site set up yet.
    """

    competitor = (
        db.query(Competitor)
        .filter(Competitor.id == competitor_id, Competitor.workspace_id == workspace_id)
        .first()
    )
    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.competitor_id == competitor_id)
        .first()
    )
    battlecard = (
        db.query(Battlecard)
        .filter(Battlecard.competitor_id == competitor_id, Battlecard.workspace_id == workspace_id)
        .first()
    )
    traffic = (
        db.query(TrafficSnapshot)
        .filter(TrafficSnapshot.competitor_id == competitor_id)
        .order_by(TrafficSnapshot.month.asc())
        .all()
    ) or None

    change_summary = summarize_competitor(db, competitor_id)

    benchmark_block = None
    own_site_competitor = get_own_site(db, workspace_id)
    if own_site_competitor is not None and own_site_competitor.id != competitor_id:
        benchmark_block = _benchmark_block(db, own_site_competitor)
    elif compare_to is not None and compare_to != competitor_id:
        compare_to_competitor = (
            db.query(Competitor)
            .filter(Competitor.id == compare_to, Competitor.workspace_id == workspace_id)
            .first()
        )
        if compare_to_competitor is not None:
            benchmark_block = _benchmark_block(db, compare_to_competitor)

    return ComparisonResponse(
        competitor=competitor,
        profile=profile,
        battlecard=battlecard,
        change_summary=change_summary,
        traffic=traffic,
        benchmark=benchmark_block,
    )
