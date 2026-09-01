from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.check_run import CheckRun, CheckRunStatus
from app.models.competitor import Competitor
from app.models.surface import Surface
from app.models.workspace_member import WorkspaceMember
from app.schemas.check_run import CheckRunResponse, LatestCheckRunsResponse
from app.dependencies import get_current_workspace

router = APIRouter(
    prefix="/workspaces/{workspace_id}/check-runs",
    tags=["Check Runs"]
)

# A queued run has no outcome yet, exactly like a running one. Both are
# excluded from `finished_runs` so the dashboard's crawl success rate keeps
# meaning successful/finished rather than silently counting not-yet-started
# work as a completed check.
_UNFINISHED_STATUSES = [CheckRunStatus.running, CheckRunStatus.queued]


@router.get(
    "/latest",
    response_model=LatestCheckRunsResponse
)
def list_latest_check_runs(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):
    """The most recent check run for every surface in the workspace, one row
    per surface, in a single query.

    The dashboard used to build this client-side by fetching
    /surfaces/{id}/check-runs once per surface — one HTTP request and one
    request-scoped DB session per surface, so a workspace with a couple of
    auto-discovered competitors could fire 80+ concurrent requests against a
    15-connection pool on a single page load.

    ROW_NUMBER() rather than PostgreSQL's DISTINCT ON so the same statement
    runs on SQLite (what the tests use) and Postgres. `started_at` is the
    ordering the per-surface endpoint already used; `id` breaks ties, since
    two runs of the same surface can share a timestamp at second resolution.
    """

    # Aggregates span the workspace's whole run history, not just `latest`:
    # the dashboard's crawl success rate has always been measured across every
    # run ever recorded, so returning only the latest per surface would have
    # silently redefined a user-facing number. Same joins and the same
    # workspace filter as the ranking below, so isolation is identical.
    #
    # count(case(...)) rather than sum(case(...)): count ignores NULLs and
    # returns 0 for an empty set, where sum returns NULL.
    total_runs, finished_runs, successful_runs = (
        db.query(
            func.count(CheckRun.id),
            func.count(
                case((CheckRun.status.notin_(_UNFINISHED_STATUSES), 1))
            ),
            func.count(case((CheckRun.status == CheckRunStatus.success, 1))),
        )
        .join(Surface, Surface.id == CheckRun.surface_id)
        .join(Competitor, Competitor.id == Surface.competitor_id)
        .filter(Competitor.workspace_id == workspace_id)
        .one()
    )

    ranked = (
        select(
            CheckRun.id.label("id"),
            func.row_number()
            .over(
                partition_by=CheckRun.surface_id,
                order_by=(CheckRun.started_at.desc(), CheckRun.id.desc()),
            )
            .label("row_number"),
        )
        .join(Surface, Surface.id == CheckRun.surface_id)
        .join(Competitor, Competitor.id == Surface.competitor_id)
        # Workspace isolation happens inside the ranking subquery, not after
        # it: filtering later would rank across every workspace's runs first.
        .where(Competitor.workspace_id == workspace_id)
        .subquery()
    )

    latest = (
        db.query(CheckRun)
        .join(ranked, ranked.c.id == CheckRun.id)
        .filter(ranked.c.row_number == 1)
        .order_by(CheckRun.surface_id)
        .all()
    )

    # Two statements, both covered by ix_check_runs_surface_id_started_at, and
    # both independent of how many surfaces the workspace has — which is the
    # property that matters here. This replaced one HTTP request per surface.
    return LatestCheckRunsResponse(
        latest=latest,
        total_runs=total_runs,
        finished_runs=finished_runs,
        successful_runs=successful_runs,
    )


@router.get(
    "/{check_run_id}",
    response_model=CheckRunResponse
)
def get_check_run(
    workspace_id: int,
    check_run_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):
    """Poll one check run.

    Scoped through Surface -> Competitor rather than a workspace column,
    because check_runs has none — the join is what enforces isolation, the
    same way list_latest_check_runs does it.
    """

    check_run = (
        db.query(CheckRun)
        .join(Surface, Surface.id == CheckRun.surface_id)
        .join(Competitor, Competitor.id == Surface.competitor_id)
        .filter(
            CheckRun.id == check_run_id,
            Competitor.workspace_id == workspace_id,
        )
        .first()
    )

    if check_run is None:
        raise HTTPException(status_code=404, detail="Check run not found")

    return check_run
