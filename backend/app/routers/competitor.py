from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.competitor_discovery_job import (
    CompetitorDiscoveryJob,
    CompetitorDiscoveryJobStatus,
)
from app.models.company_profile import CompanyProfile
from app.models.battlecard import Battlecard
from app.models.traffic_snapshot import TrafficSnapshot
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.competitor import (
    CompetitorCreate,
    CompetitorResponse,
    CompetitorDiscoveryJobResponse,
)
from app.schemas.comparison import ComparisonResponse, BenchmarkComparisonResponse
from app.dependencies import get_current_user, get_current_workspace, require_role
from app.services.comparison_service import summarize_competitor
from app.queue import JobSpec, dispatch_job
from app.services.competitor_discovery_service import run_competitor_discovery_job
from app.services.competitor_service import delete_competitor as delete_competitor_cascade
from app.services.own_site_service import get_own_site

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
    background_tasks: BackgroundTasks,
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

    # Page discovery drives a real browser (Chromium launch, a 60s navigation
    # timeout and an unconditional 8s settle) and then inserts up to 40
    # surfaces, so it runs as a queued job rather than inline: this request
    # used to stay open for the better part of a minute, which is long enough
    # for an edge proxy to drop it even though every surface had already been
    # committed. Same job + BackgroundTasks + poll shape as briefings and
    # battlecard updates.
    discovery_job_id = None
    if competitor.website_url is not None:
        job = CompetitorDiscoveryJob(
            workspace_id=workspace_id,
            competitor_id=competitor_id,
            website_url=str(competitor.website_url),
            status=CompetitorDiscoveryJobStatus.queued,
            created_by_user_id=current_user.id,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        # See app/queue.py — arq when REDIS_URL is set, BackgroundTasks
        # otherwise. Unique per row, so the queue key is a backstop here.
        dispatch_job(
            background_tasks,
            JobSpec(
                task_name="run_competitor_discovery_job",
                fn=run_competitor_discovery_job,
                args=(job.id,),
                job_id=f"discovery:{job.id}",
            ),
        )
        discovery_job_id = job.id

    # Neither is a DB column — set only so the create response can hand the
    # frontend the job to poll. surfaces_discovered is always 0 here now; the
    # real count lands on the job when discovery finishes.
    new_competitor.surfaces_discovered = 0
    new_competitor.discovery_job_id = discovery_job_id

    return new_competitor


@router.get(
    "/{competitor_id}/discovery-jobs/{job_id}",
    response_model=CompetitorDiscoveryJobResponse
)
def get_competitor_discovery_job(
    workspace_id: int,
    competitor_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    job = (
        db.query(CompetitorDiscoveryJob)
        .filter(
            CompetitorDiscoveryJob.id == job_id,
            CompetitorDiscoveryJob.workspace_id == workspace_id,
            CompetitorDiscoveryJob.competitor_id == competitor_id,
        )
        .first()
    )

    if job is None:
        raise HTTPException(status_code=404, detail="Discovery job not found")

    return job


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
