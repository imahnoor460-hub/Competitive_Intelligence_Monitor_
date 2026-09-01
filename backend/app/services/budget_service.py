from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.models.llm_usage import TokenUsageLog
from app.models.workspace_budget import WorkspaceBudget

__all__ = [
    "BudgetExceededError", "get_or_create_budget", "estimate_spend_usd",
    "estimate_spend_by_purpose", "check_budget",
]


class BudgetExceededError(Exception):
    pass


def get_or_create_budget(db: Session, workspace_id: int) -> WorkspaceBudget:
    budget = (
        db.query(WorkspaceBudget)
        .filter(WorkspaceBudget.workspace_id == workspace_id)
        .first()
    )
    if budget is not None:
        return budget

    # Two checks of the same workspace can now run at the same instant in
    # different worker processes, and both can pass the SELECT above before
    # either INSERTs — workspace_id is the primary key, so the loser used to
    # die on a UniqueViolation that poisoned the whole session and failed the
    # check. The savepoint confines that failure to this INSERT, leaving the
    # caller's transaction intact so the loser can simply read the winner's
    # row. Harmless before checks were parallelised; required now.
    try:
        with db.begin_nested():
            budget = WorkspaceBudget(workspace_id=workspace_id)
            db.add(budget)
            db.flush()
        return budget
    except IntegrityError:
        return (
            db.query(WorkspaceBudget)
            .filter(WorkspaceBudget.workspace_id == workspace_id)
            .one()
        )


def estimate_spend_usd(db: Session, workspace_id: int, since: datetime) -> float:
    total_tokens = (
        db.query(
            func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0)
            + func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0)
        )
        .filter(
            TokenUsageLog.workspace_id == workspace_id,
            TokenUsageLog.created_at >= since,
        )
        .scalar()
    ) or 0

    return (total_tokens / 1000.0) * settings.llm_cost_per_1k_tokens_usd


def estimate_spend_by_purpose(db: Session, workspace_id: int, since: datetime) -> dict[str, float]:
    """Real spend broken down by what the tokens were actually spent on
    (scoring, briefing/battlecard/synthesis drafting, embeddings) — powers
    the dashboard's cost breakdown chart. Unlike estimate_spend_usd's single
    total, this groups by TokenUsageLog.purpose so a workspace can see
    where its budget is actually going.
    """

    rows = (
        db.query(
            TokenUsageLog.purpose,
            func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0)
            + func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0),
        )
        .filter(
            TokenUsageLog.workspace_id == workspace_id,
            TokenUsageLog.created_at >= since,
        )
        .group_by(TokenUsageLog.purpose)
        .all()
    )

    return {
        purpose.value: (tokens / 1000.0) * settings.llm_cost_per_1k_tokens_usd
        for purpose, tokens in rows
    }


def check_budget(db: Session, workspace_id: int | None) -> None:
    """Call before every LLM API call, not after — the point is to skip the
    call entirely once a workspace is over its cap, not to bill it and then
    complain. A workspace with no configured cap (the default for every
    workspace until an owner sets one) is treated as unlimited.
    """

    if workspace_id is None:
        return

    budget = get_or_create_budget(db, workspace_id)
    if budget.monthly_cap_usd is None:
        return

    spend = estimate_spend_usd(db, workspace_id, since=budget.period_start)
    if spend >= budget.monthly_cap_usd:
        raise BudgetExceededError(
            f"Workspace {workspace_id} has spent an estimated ${spend:.4f} against a "
            f"${budget.monthly_cap_usd:.2f} monthly cap"
        )
