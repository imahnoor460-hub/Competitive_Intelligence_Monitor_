from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.briefing import Briefing
from app.models.briefing_job import BriefingJob, BriefingJobStatus
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.briefing import GenerateBriefingRequest, BriefingResponse, BriefingJobResponse
from app.dependencies import (
    get_current_user,
    get_current_workspace,
    require_role,
    enforce_rate_limit,
)
from app.services.budget_service import check_budget, BudgetExceededError
from app.services.llm.factory import get_llm_client
from app.queue import JobSpec, dispatch_job
from app.services.briefing_service import run_briefing_job

router = APIRouter(
    prefix="/workspaces/{workspace_id}/briefings",
    tags=["Briefings"]
)


@router.post(
    "/generate-now",
    response_model=BriefingJobResponse,
    status_code=202
)
def generate_now(
    workspace_id: int,
    payload: GenerateBriefingRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor))
):

    llm_client = get_llm_client()
    if llm_client is None:
        raise HTTPException(
            status_code=400,
            detail="No LLM is configured for this deployment"
        )

    # Both guards run here, synchronously, rather than inside the queued
    # job: the job's own failure is invisible to this request, which has
    # already returned 202 by the time it runs, so a caller over budget
    # would be told the work was accepted and only find out otherwise by
    # polling the job. generate_briefing() still calls check_budget() of
    # its own — spend can cross the cap between enqueue and execution —
    # but that one is a backstop, not the caller-facing answer.
    #
    # Budget is checked before the rate limit so being over budget doesn't
    # also burn a rate-limit token for work that was never going to run.
    try:
        check_budget(db, workspace_id)
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc))

    enforce_rate_limit("briefing-generate", workspace_id)

    job = BriefingJob(
        workspace_id=workspace_id,
        audience=payload.audience,
        digest_type=payload.digest_type,
        change_log_ids=payload.change_log_ids,
        status=BriefingJobStatus.queued,
        created_by_user_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Queued through arq when REDIS_URL is set, otherwise via BackgroundTasks
    # exactly as before (see app/queue.py). The job row is already committed
    # either way, so the response and the frontend's poll are unaffected by
    # which transport carried it. The job id is unique per row, so the
    # queue-level dedupe is a backstop here rather than the main guard.
    dispatch_job(
        background_tasks,
        JobSpec(
            task_name="run_briefing_job",
            fn=run_briefing_job,
            args=(job.id,),
            job_id=f"briefing:{job.id}",
        ),
    )

    return job


@router.get(
    "/jobs/{job_id}",
    response_model=BriefingJobResponse
)
def get_briefing_job(
    workspace_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    job = (
        db.query(BriefingJob)
        .filter(BriefingJob.id == job_id, BriefingJob.workspace_id == workspace_id)
        .first()
    )

    if job is None:
        raise HTTPException(status_code=404, detail="Briefing job not found")

    return job


@router.get(
    "/",
    response_model=list[BriefingResponse]
)
def list_briefings(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    return (
        db.query(Briefing)
        .filter(Briefing.workspace_id == workspace_id)
        .order_by(Briefing.created_at.desc())
        .all()
    )


@router.get(
    "/{briefing_id}",
    response_model=BriefingResponse
)
def get_briefing(
    workspace_id: int,
    briefing_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    briefing = (
        db.query(Briefing)
        .filter(Briefing.id == briefing_id, Briefing.workspace_id == workspace_id)
        .first()
    )

    if briefing is None:
        raise HTTPException(status_code=404, detail="Briefing not found")

    return briefing
