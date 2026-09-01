from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.battlecard import Battlecard
from app.models.battlecard_update import BattlecardUpdate
from app.models.battlecard_update_job import BattlecardUpdateJob, BattlecardUpdateJobStatus
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.battlecard import (
    ProposeBattlecardUpdateRequest, BattlecardResponse, BattlecardUpdateResponse,
    BattlecardUpdateJobResponse,
)
from app.dependencies import (
    get_current_user,
    get_current_workspace,
    require_role,
    enforce_rate_limit,
)
from app.services.budget_service import check_budget, BudgetExceededError
from app.services.llm.factory import get_llm_client
from app.queue import JobSpec, dispatch_job
from app.services.battlecard_service import run_battlecard_update_job

router = APIRouter(
    prefix="/workspaces/{workspace_id}/competitors/{competitor_id}/battlecard",
    tags=["Battlecards"]
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
    response_model=BattlecardResponse
)
def get_battlecard(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    battlecard = (
        db.query(Battlecard)
        .filter(Battlecard.competitor_id == competitor_id, Battlecard.workspace_id == workspace_id)
        .first()
    )
    if battlecard is None:
        raise HTTPException(status_code=404, detail="No battlecard yet for this competitor")

    return battlecard


@router.post(
    "/updates",
    response_model=BattlecardUpdateJobResponse,
    status_code=202
)
def propose_update(
    workspace_id: int,
    competitor_id: int,
    payload: ProposeBattlecardUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor))
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    llm_client = get_llm_client()
    if llm_client is None:
        raise HTTPException(status_code=400, detail="No LLM is configured for this deployment")

    # Same shape as briefings.generate_now: both guards run here rather than
    # inside the queued job, because this request has already returned 202 by
    # the time the job runs, so an over-budget caller would be told the work
    # was accepted and only learn otherwise by polling the job.
    # draft_update_from_change_logs() still calls check_budget() of its own —
    # spend can cross the cap between enqueue and execution — but that one is
    # a backstop, not the caller-facing answer.
    #
    # Budget before rate limit, so being over budget doesn't also burn a
    # rate-limit token for work that was never going to run. The competitor
    # lookup stays first: a bogus competitor_id is a 404, not a 402/429.
    try:
        check_budget(db, workspace_id)
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc))

    enforce_rate_limit("battlecard-propose", workspace_id)

    job = BattlecardUpdateJob(
        workspace_id=workspace_id,
        competitor_id=competitor_id,
        change_log_ids=payload.change_log_ids,
        status=BattlecardUpdateJobStatus.queued,
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
            task_name="run_battlecard_update_job",
            fn=run_battlecard_update_job,
            args=(job.id,),
            job_id=f"battlecard:{job.id}",
        ),
    )

    return job


@router.get(
    "/updates/jobs/{job_id}",
    response_model=BattlecardUpdateJobResponse
)
def get_battlecard_update_job(
    workspace_id: int,
    competitor_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    job = (
        db.query(BattlecardUpdateJob)
        .filter(
            BattlecardUpdateJob.id == job_id,
            BattlecardUpdateJob.workspace_id == workspace_id,
            BattlecardUpdateJob.competitor_id == competitor_id,
        )
        .first()
    )

    if job is None:
        raise HTTPException(status_code=404, detail="Battlecard update job not found")

    return job


@router.get(
    "/updates",
    response_model=list[BattlecardUpdateResponse]
)
def list_updates(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    battlecard = (
        db.query(Battlecard)
        .filter(Battlecard.competitor_id == competitor_id, Battlecard.workspace_id == workspace_id)
        .first()
    )
    if battlecard is None:
        return []

    return (
        db.query(BattlecardUpdate)
        .filter(BattlecardUpdate.battlecard_id == battlecard.id)
        .order_by(BattlecardUpdate.created_at.desc())
        .all()
    )
