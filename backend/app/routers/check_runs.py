from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.check_run import CheckRun
from app.models.competitor import Competitor
from app.models.surface import Surface
from app.models.workspace_member import WorkspaceMember
from app.schemas.check_run import CheckRunResponse
from app.dependencies import get_current_workspace

router = APIRouter(
    prefix="/workspaces/{workspace_id}/check-runs",
    tags=["Check Runs"]
)


@router.get(
    "/latest",
    response_model=list[CheckRunResponse]
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

    return (
        db.query(CheckRun)
        .join(ranked, ranked.c.id == CheckRun.id)
        .filter(ranked.c.row_number == 1)
        .order_by(CheckRun.surface_id)
        .all()
    )
