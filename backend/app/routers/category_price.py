from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.category_price import CategoryPriceRequest, CategoryPriceResponse
from app.dependencies import (
    require_role,
    rate_limit,
    require_writable_workspace,
)
from app.services.budget_service import BudgetExceededError
from app.services.category_price_service import get_category_price_stats, NoSurfaceAvailable
from app.services.llm.factory import get_llm_client

router = APIRouter(
    prefix="/workspaces/{workspace_id}/competitors/{competitor_id}/category-price",
    tags=["Category Price"]
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


@router.post(
    "/",
    response_model=CategoryPriceResponse
)
def get_category_price(
    workspace_id: int,
    competitor_id: int,
    request: CategoryPriceRequest,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _rate_limit: None = Depends(rate_limit("category-price", limit=30, window_seconds=3600.0)),
    _demo: None = Depends(require_writable_workspace("run category price lookups"))
):
    """Best-effort — see category_price_service.get_category_price_stats.
    Returns 200 with prices_found=0 when no matching listing page can be
    located or it has no visible prices, since that's a real outcome, not a
    failure.
    """

    _get_owned_competitor(db, workspace_id, competitor_id)

    llm_client = get_llm_client()
    if llm_client is None:
        raise HTTPException(status_code=400, detail="No LLM is configured for this deployment")

    try:
        return get_category_price_stats(
            db, llm_client, workspace_id, competitor_id, request.category
        )
    except NoSurfaceAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc))
