from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.llm_usage import TokenUsageLog, LLMUsagePurpose
from app.services.budget_service import check_budget
from app.services.llm.client import LLMClient
from app.services.llm.prompts import BASELINE_SUMMARY_SYSTEM_PROMPT, baseline_summary_user_prompt

__all__ = ["summarize_baseline_snapshot", "BaselineSummaryResult"]


class BaselineFact(BaseModel):
    label: str
    value: str


class BaselineSummaryResult(BaseModel):
    headline: str | None = None
    facts: list[BaselineFact] = []


def summarize_baseline_snapshot(
    db: Session,
    llm_client: LLMClient,
    workspace_id: int,
    surface_label: str,
    page_text: str,
) -> BaselineSummaryResult:
    """Turns a surface's first captured snapshot into a short, readable
    description — the one case in the check pipeline with no diff to
    summarize instead, since there's nothing to compare a baseline against.
    """

    check_budget(db, workspace_id)

    # Same rule as site_summary_service.generate_site_summary: check_budget's
    # queries leave a transaction open, and the completion below is a blocking
    # network call, so hand the pooled connection back before it. This is the
    # baseline path, which is exactly what every never-checked surface takes
    # after a restart, so it is the one LLM hold that overlaps at scale. The
    # db.add() below emits no SQL (autoflush is off), so the connection stays
    # returned until the caller's commit.
    db.commit()

    result = llm_client.complete(
        system=BASELINE_SUMMARY_SYSTEM_PROMPT,
        user=baseline_summary_user_prompt(surface_label, page_text),
        response_model=BaselineSummaryResult,
    )

    # Reuses the site_summary purpose bucket rather than adding a new enum
    # value — both are "read what's currently on the page" LLM calls, just
    # scoped to one surface instead of a whole competitor, and adding a
    # purpose would require a Postgres ALTER TYPE migration for one extra
    # budget-reporting label.
    db.add(TokenUsageLog(
        workspace_id=workspace_id,
        purpose=LLMUsagePurpose.site_summary,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    ))

    return result.value
