from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.battlecard_update_job import BattlecardUpdateJob, BattlecardUpdateJobStatus
from app.models.briefing_job import BriefingJob, BriefingJobStatus
from app.models.check_run import CheckRun, CheckRunStatus
from app.models.check_sweep import CheckSweep, CheckSweepStatus
from app.models.competitor import Competitor
from app.models.competitor_discovery_job import (
    CompetitorDiscoveryJob,
    CompetitorDiscoveryJobStatus,
)
from app.models.site_summary_job import SiteSummaryJob, SiteSummaryJobStatus
from app.models.surface import Surface
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.check_run import (
    ActiveJobsResponse,
    CheckSweepResponse,
    CompetitorJobRef,
)
from app.dependencies import (
    get_current_user,
    get_current_workspace,
    require_role,
    rate_limit,
)
from app.queue import JobSpec, dispatch_jobs
from app.services.check_service import enqueue_surface_check, execute_surface_check
from app.services.surface_selection import partition_by_cap

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["Jobs"]
)

_ACTIVE_CHECK_RUN_STATUSES = [CheckRunStatus.queued, CheckRunStatus.running]
_ACTIVE_SWEEP_STATUSES = [CheckSweepStatus.queued, CheckSweepStatus.running]


@router.post(
    "/check-all",
    response_model=CheckSweepResponse,
    status_code=202,
)
def check_all(
    workspace_id: int,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _rate_limit: None = Depends(rate_limit("check-all", limit=6, window_seconds=3600.0)),
):
    """Check every active surface in the workspace as one tracked sweep.

    This replaces a sequential `await` loop that ran in the browser: the
    frontend used to fetch competitors, then surfaces, then POST a blocking
    check per surface, one after another. Nothing on the server knew a sweep
    was happening, so closing the tab abandoned it halfway with no record.

    Returns immediately with a sweep row to poll. An already-running sweep is
    returned as-is rather than starting a second one, so two tabs — or a
    double click — cannot double-check every surface in the workspace.

    Bounded per competitor by `max_active_surfaces_per_competitor`. "Every
    active surface" was true to the name and wrong in practice: one workspace
    had accumulated 282 of them across eight competitors, so a click queued
    282 checks that a two-slot worker chewed through for the better part of an
    hour. The cap is applied here as well as at discovery time because rows
    predating the cap — or activated by hand — would otherwise still be swept.
    """

    in_flight = (
        db.query(CheckSweep)
        .filter(
            CheckSweep.workspace_id == workspace_id,
            CheckSweep.status.in_(_ACTIVE_SWEEP_STATUSES),
        )
        .order_by(CheckSweep.id.desc())
        .first()
    )
    if in_flight is not None:
        response.status_code = 200
        return in_flight

    active_surfaces = (
        db.query(Surface)
        .join(Competitor, Competitor.id == Surface.competitor_id)
        .filter(
            Competitor.workspace_id == workspace_id,
            Surface.is_active.is_(True),
        )
        .all()
    )

    # Same ranking the scheduler and the cleanup migration use, so a sweep
    # checks the pages a competitor is actually being watched on rather than
    # an arbitrary prefix of them.
    by_competitor: dict[int, list[Surface]] = {}
    for surface in active_surfaces:
        by_competitor.setdefault(surface.competitor_id, []).append(surface)

    surfaces = [
        surface
        for competitor_surfaces in by_competitor.values()
        for surface in partition_by_cap(competitor_surfaces)[0]
    ]

    sweep = CheckSweep(
        workspace_id=workspace_id,
        status=CheckSweepStatus.queued,
        total=0,
        finished=0,
        failed_count=0,
        created_by_user_id=current_user.id,
    )
    db.add(sweep)
    db.commit()
    db.refresh(sweep)

    # Claim a run per surface first, then dispatch the whole batch over one
    # Redis connection. Surfaces already being checked are skipped rather than
    # duplicated, which is why `total` counts what was actually claimed rather
    # than how many surfaces exist.
    specs: list[JobSpec] = []
    for surface in surfaces:
        check_run, created = enqueue_surface_check(db, surface, sweep_id=sweep.id)
        if not created:
            continue
        specs.append(
            JobSpec(
                task_name="execute_surface_check",
                fn=execute_surface_check,
                args=(check_run.id,),
                job_id=f"check:run:{check_run.id}",
            )
        )

    sweep.total = len(specs)
    if not specs:
        # Nothing to do — an empty workspace, or every surface already being
        # checked. Close the sweep now rather than leaving it queued forever
        # with nothing that could ever finish it.
        sweep.status = CheckSweepStatus.success
        sweep.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(sweep)

    if specs:
        dispatch_jobs(background_tasks, specs)

    return sweep


@router.get(
    "/check-sweeps/{sweep_id}",
    response_model=CheckSweepResponse,
)
def get_check_sweep(
    workspace_id: int,
    sweep_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace),
):

    sweep = (
        db.query(CheckSweep)
        .filter(CheckSweep.id == sweep_id, CheckSweep.workspace_id == workspace_id)
        .first()
    )

    if sweep is None:
        raise HTTPException(status_code=404, detail="Check sweep not found")

    return sweep


@router.get(
    "/jobs/active",
    response_model=ActiveJobsResponse,
)
def list_active_jobs(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace),
):
    """Every job in this workspace that has not reached a terminal status.

    This is what makes a page refresh survivable. Poll state lives in React
    memory, so before this endpoint existed a reload orphaned every in-flight
    job: the work carried on in the background but the UI never learned it
    had finished. On mount the frontend calls this once and re-attaches a
    poller to whatever comes back.

    The three job types that already have their own poll endpoints are
    returned as identifiers only — the frontend re-attaches using the URLs it
    already builds. Battlecard and discovery jobs carry `competitor_id`
    alongside the id because their poll paths are nested under the
    competitor, so an id on its own cannot rebuild the URL.

    Check runs and sweeps are returned in full because their poll endpoints
    are new and the UI needs their current state to render progress
    immediately, before the first poll tick lands.
    """

    check_runs = (
        db.query(CheckRun)
        .join(Surface, Surface.id == CheckRun.surface_id)
        .join(Competitor, Competitor.id == Surface.competitor_id)
        .filter(
            Competitor.workspace_id == workspace_id,
            CheckRun.status.in_(_ACTIVE_CHECK_RUN_STATUSES),
        )
        .order_by(CheckRun.id.desc())
        .all()
    )

    check_sweeps = (
        db.query(CheckSweep)
        .filter(
            CheckSweep.workspace_id == workspace_id,
            CheckSweep.status.in_(_ACTIVE_SWEEP_STATUSES),
        )
        .order_by(CheckSweep.id.desc())
        .all()
    )

    def _active(model, status_enum, *columns):
        return (
            db.query(*columns)
            .filter(
                model.workspace_id == workspace_id,
                model.status.in_([status_enum.queued, status_enum.running]),
            )
            .order_by(model.id.desc())
            .all()
        )

    def _refs(model, status_enum):
        return [
            CompetitorJobRef(id=row_id, competitor_id=competitor_id)
            for row_id, competitor_id in _active(
                model, status_enum, model.id, model.competitor_id
            )
        ]

    return ActiveJobsResponse(
        check_runs=check_runs,
        check_sweeps=check_sweeps,
        briefing_job_ids=[
            row_id
            for (row_id,) in _active(BriefingJob, BriefingJobStatus, BriefingJob.id)
        ],
        battlecard_update_jobs=_refs(BattlecardUpdateJob, BattlecardUpdateJobStatus),
        competitor_discovery_jobs=_refs(
            CompetitorDiscoveryJob, CompetitorDiscoveryJobStatus
        ),
        site_summary_jobs=_refs(SiteSummaryJob, SiteSummaryJobStatus),
    )
