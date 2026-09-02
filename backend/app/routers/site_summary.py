from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.competitor_site_summary import CompetitorSiteSummary
from app.models.site_summary_job import SiteSummaryJob, SiteSummaryJobStatus
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.site_summary import SiteSummaryJobResponse, SiteSummaryResponse
from app.dependencies import (
    get_current_user,
    get_current_workspace,
    require_role,
    rate_limit,
    require_writable_workspace,
)
from app.queue import JobSpec, dispatch_job
from app.services.llm.factory import get_llm_client
from app.services.site_summary_service import run_site_summary_job

_ACTIVE_STATUSES = [SiteSummaryJobStatus.queued, SiteSummaryJobStatus.running]

router = APIRouter(
    prefix="/workspaces/{workspace_id}/competitors/{competitor_id}/site-summary",
    tags=["Site Summary"]
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
    response_model=SiteSummaryResponse
)
def get_site_summary(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    summary = (
        db.query(CompetitorSiteSummary)
        .filter(CompetitorSiteSummary.competitor_id == competitor_id)
        .first()
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="No site summary generated yet")

    return summary


@router.post(
    "/refresh",
    response_model=SiteSummaryJobResponse,
    status_code=202,
)
def refresh_site_summary(
    workspace_id: int,
    competitor_id: int,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _rate_limit: None = Depends(rate_limit("site-summary-refresh", limit=10, window_seconds=3600.0)),
    _demo: None = Depends(require_writable_workspace("re-run site analysis"))
):
    """Queue an "Analyze site" refresh and return the job to poll.

    This used to generate the summary inline and return it. The refresh reads
    every active surface — up to 40 after discovery — at roughly 1.6s per
    fetch, which is long enough for an edge proxy to drop the connection while
    the work carries on invisibly. Same job + poll shape as briefings,
    battlecard updates and page discovery.

    An already-running job for this competitor is returned as-is rather than
    starting a second one, so a double click cannot queue the same fan-out
    twice. That case answers 200 instead of 202: nothing new was created.
    """

    _get_owned_competitor(db, workspace_id, competitor_id)

    # Checked here rather than inside the job so a deployment with no LLM
    # configured still fails the request loudly, the way it did when this was
    # synchronous, instead of queueing work that can only fail.
    if get_llm_client() is None:
        raise HTTPException(status_code=400, detail="No LLM is configured for this deployment")

    in_flight = (
        db.query(SiteSummaryJob)
        .filter(
            SiteSummaryJob.competitor_id == competitor_id,
            SiteSummaryJob.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(SiteSummaryJob.id.desc())
        .first()
    )
    if in_flight is not None:
        response.status_code = 200
        return in_flight

    job = SiteSummaryJob(
        workspace_id=workspace_id,
        competitor_id=competitor_id,
        status=SiteSummaryJobStatus.queued,
        created_by_user_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # See app/queue.py — arq when REDIS_URL is set, BackgroundTasks otherwise.
    dispatch_job(
        background_tasks,
        JobSpec(
            task_name="run_site_summary_job",
            fn=run_site_summary_job,
            args=(job.id,),
            job_id=f"site-summary:{job.id}",
        ),
    )

    return job


@router.get(
    "/jobs/{job_id}",
    response_model=SiteSummaryJobResponse,
)
def get_site_summary_job(
    workspace_id: int,
    competitor_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace),
):

    job = (
        db.query(SiteSummaryJob)
        .filter(
            SiteSummaryJob.id == job_id,
            SiteSummaryJob.workspace_id == workspace_id,
            SiteSummaryJob.competitor_id == competitor_id,
        )
        .first()
    )

    if job is None:
        raise HTTPException(status_code=404, detail="Site summary job not found")

    return job
