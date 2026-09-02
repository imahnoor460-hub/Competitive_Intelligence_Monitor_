from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.workspace_budget import WorkspaceBudgetResponse, WorkspaceBudgetUpdate
from app.dependencies import (
    get_current_workspace,
    require_role,
    require_writable_workspace,
)
from app.services.budget_service import (
    get_or_create_budget, estimate_spend_usd, estimate_spend_by_purpose
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/budget",
    tags=["Budget"]
)


def _to_response(db: Session, budget) -> WorkspaceBudgetResponse:
    spend = estimate_spend_usd(db, budget.workspace_id, since=budget.period_start)
    by_purpose = estimate_spend_by_purpose(db, budget.workspace_id, since=budget.period_start)
    return WorkspaceBudgetResponse(
        workspace_id=budget.workspace_id,
        monthly_cap_usd=budget.monthly_cap_usd,
        period_start=budget.period_start,
        alert_threshold_pct=budget.alert_threshold_pct,
        estimated_spend_usd=spend,
        spend_by_purpose=by_purpose,
    )


@router.get(
    "/",
    response_model=WorkspaceBudgetResponse
)
def get_budget(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    budget = get_or_create_budget(db, workspace_id)
    db.commit()

    return _to_response(db, budget)


@router.put(
    "/",
    response_model=WorkspaceBudgetResponse
)
def update_budget(
    workspace_id: int,
    payload: WorkspaceBudgetUpdate,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("change the budget"))
):

    budget = get_or_create_budget(db, workspace_id)
    budget.monthly_cap_usd = payload.monthly_cap_usd
    if payload.alert_threshold_pct is not None:
        budget.alert_threshold_pct = payload.alert_threshold_pct

    db.commit()
    db.refresh(budget)

    return _to_response(db, budget)
